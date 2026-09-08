"""Phase 4 synthetic bandwidth tests; HTTP mocked, no Plex or media required."""
import os
import unittest
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
import requests
from streamlit.testing.v1 import AppTest
from analytics import normalize_history_row, HistoryQuery, filter_history
from analytics_service import read_history
from dashboard import read_live
from playback import normalize_bandwidth, format_bandwidth, bandwidth_mbps
from tautulli_client import normalize_activity, normalize_session
from bandwidth_analytics import (current_summary, history_summary, sorted_sessions,
                                 bandwidth_rankings, bandwidth_days, is_high)
from tests.test_devices_transcoding import raw_row, reply


def row(i=1, **extra):
    return normalize_history_row(raw_row(i, **extra))


def activity(sessions, **extra):
    return normalize_activity(dict(sessions=sessions, **extra))


class NormalizationTests(unittest.TestCase):
    def test_numeric_bandwidth(self):
        for raw, expected in [('20000', 20000), (2500.5, 2500.5), ('0', 0), (0, 0), (' 12.5 ', 12.5)]:
            self.assertEqual(normalize_bandwidth(raw), expected)
            self.assertEqual(normalize_session({'bandwidth': raw})['bandwidth_kbps'], expected)
            self.assertEqual(row(bandwidth=raw)['bandwidth_kbps'], expected)

    def test_negative(self):
        for raw in [-1, '-20000']:
            self.assertIsNone(normalize_bandwidth(raw))

    def test_booleans(self):
        for raw in [True, False]:
            self.assertIsNone(normalize_bandwidth(raw))
            self.assertIsNone(normalize_session({'bandwidth': raw})['bandwidth_kbps'])
            self.assertIsNone(activity([], total_bandwidth=raw)['metrics']['Total bandwidth'])

    def test_nonfinite_malformed_missing(self):
        for raw in [None, '', 'invalid', {}, [], 'NaN', 'Infinity', float('nan'), float('inf'), '20 Mbps', '200 MB/s', 10**400]:
            self.assertIsNone(normalize_bandwidth(raw))

    def test_units(self):
        self.assertEqual(format_bandwidth(0), '0.00 Mbps')
        self.assertEqual(format_bandwidth(500), '500.00 Kbps')
        self.assertEqual(format_bandwidth(20000), '20.00 Mbps')
        self.assertEqual(format_bandwidth(2000000), '2.00 Gbps')
        self.assertEqual(format_bandwidth(None), 'Unavailable')
        self.assertEqual(bandwidth_mbps(20000), 20)

    def test_no_bitrate_or_bytes_fallback(self):
        raw = dict(bitrate=25000, stream_bitrate=20000, bytes_per_second=20000, transferred_bytes=20000)
        self.assertIsNone(normalize_session(raw)['bandwidth_kbps'])
        self.assertIsNone(row(**raw)['bandwidth_kbps'])


class CurrentTests(unittest.TestCase):
    def test_authoritative_aggregates(self):
        stats = current_summary(activity([{'bandwidth': 1}], total_bandwidth='90000', lan_bandwidth=40000, wan_bandwidth=50000))
        self.assertEqual(stats['totals'], {'Total': 90000, 'Local': 40000, 'Remote': 50000})
        self.assertEqual(set(stats['sources'].values()), {'Tautulli aggregate'})

    def test_no_active_sessions(self):
        stats = current_summary(activity([]))
        self.assertEqual(stats['totals'], {'Total': 0, 'Local': 0, 'Remote': 0})
        self.assertEqual((stats['active'], stats['remote'], stats['transcodes']), (0, 0, 0))
        self.assertIsNone(stats['highest'])

    def test_current_sort_unknown_last_zero_known(self):
        rows = activity([{'title': str(i), 'bandwidth': b} for i, b in enumerate([None, 0, 25000, 20000])])['sessions']
        self.assertEqual([r['title'] for r in sorted_sessions(rows)], ['2', '3', '1', '0'])

    def test_complete_sums_and_decisions(self):
        stats = current_summary(activity([{'bandwidth': 8000, 'local': 1, 'transcode_decision': 'direct play'},
                                          {'bandwidth': 25000, 'local': 0, 'transcode_decision': 'transcode'}]))
        self.assertEqual(stats['totals'], {'Total': 33000, 'Local': 8000, 'Remote': 25000})
        self.assertEqual((stats['remote'], stats['transcodes'], stats['known']), (1, 1, 2))
        self.assertEqual(stats['highest']['decision'], 'Transcode')

    def test_partial_current(self):
        stats = current_summary(activity([{'bandwidth': 8000, 'local': 1}, {'local': 0}]))
        self.assertIsNone(stats['totals']['Total'])
        self.assertIsNone(stats['totals']['Remote'])
        self.assertEqual(stats['totals']['Local'], 8000)
        self.assertEqual(stats['known'], 1)
        self.assertIsNone(stats['transcodes'])

    def test_missing_all_values(self):
        stats = current_summary(activity([{}]))
        self.assertTrue(all(v is None for v in stats['totals'].values()))
        self.assertIsNone(stats['highest'])
        self.assertIsNone(stats['remote'])

    def test_unknown_location_blocks_location_sums(self):
        stats = current_summary(activity([{'bandwidth': 8000, 'local': 1}, {'bandwidth': 25000}]))
        self.assertEqual(stats['totals']['Total'], 33000)
        self.assertIsNone(stats['totals']['Local'])
        self.assertIsNone(stats['totals']['Remote'])
        self.assertIsNone(stats['remote'])

    def test_remote_only_and_local_only(self):
        for location, present, absent in [('lan', 'Local', 'Remote'), ('wan', 'Remote', 'Local')]:
            stats = current_summary(activity([{'bandwidth': 8000, 'location': location}]))
            self.assertEqual(stats['totals'][present], 8000)
            self.assertEqual(stats['totals'][absent], 0)

    def test_high_strict_threshold(self):
        for b, expected in [(None, None), (19999, False), (20000, False), (20001, True)]:
            self.assertIs(is_high(row(bandwidth=b), 20), expected)


class HistoryTests(unittest.TestCase):
    def test_absent_and_empty(self):
        for rows in [[], [row()], [row(bandwidth='invalid')]]:
            stats = history_summary(rows)
            for name in ['mean', 'median', 'peak', 'high', 'remote_percent']:
                self.assertIsNone(stats[name])
            self.assertEqual(stats['known'], 0)
            self.assertEqual(bandwidth_rankings(rows, 'user_key', 'user'), [])

    def test_known_mean_median_peak_coverage_high(self):
        stats = history_summary([row(i, bandwidth=b) for i, b in enumerate([10000, 20000, 90000, None])])
        self.assertEqual((stats['mean'], stats['median'], stats['peak']), (40000, 20000, 90000))
        self.assertEqual((stats['known'], stats['unknown'], stats['total'], stats['high']), (3, 1, 4, 1))

    def test_even_median_and_zero(self):
        stats = history_summary([row(bandwidth=0), row(2, bandwidth=10000)])
        self.assertEqual((stats['mean'], stats['median'], stats['peak'], stats['high']), (5000, 5000, 10000, 0))

    def test_locations_joint_subset_and_remote_highest(self):
        stats = history_summary([row(bandwidth=10000), row(2, bandwidth=30000, location='wan'),
                                 row(3, bandwidth=90000, location=None), row(4, location='wan')])
        self.assertEqual((stats['local_mean'], stats['remote_mean'], stats['remote_peak']), (10000, 30000, 30000))
        self.assertEqual((stats['local_count'], stats['remote_count'], stats['located'], stats['remote_percent']), (1, 1, 2, 50))
        self.assertEqual(stats['remote_highest']['bandwidth_kbps'], 30000)

    def test_user_ranking_average_peak_high(self):
        values = [row(1, user_id=1, bandwidth=60000), row(2, user_id=1, bandwidth=0),
                  row(3, user_id=2, bandwidth=40000), row(4, user_id=2, bandwidth=40000)]
        self.assertEqual(bandwidth_rankings(values, 'user_key', 'user')[0]['key'], 'id:2')
        self.assertEqual(bandwidth_rankings(values, 'user_key', 'user', order='peak')[0]['key'], 'id:1')
        self.assertEqual(bandwidth_rankings(values, 'user_key', 'user', order='high')[0]['key'], 'id:2')

    def test_device_identity_subsets_and_unknown(self):
        values = [row(bandwidth=10000), row(2, bandwidth=30000, location='wan', transcode_decision='transcode'),
                  row(3, bandwidth=50000, machine_id='other'), row(4), normalize_history_row({'bandwidth': 90000})]
        ranked = bandwidth_rankings(values, 'device_key', 'device_name')
        self.assertEqual(len(ranked), 2)
        group = next(r for r in ranked if r['known'] == 2)
        self.assertEqual((group['mean'], group['remote_mean'], group['transcode_mean']), (20000, 30000, 30000))
        self.assertEqual((group['known'], group['total'], group['located'], group['transcode_count']), (2, 3, 2, 1))
        self.assertEqual(group['scope'], 'Identified client')

    def test_title_ranking_and_stable_sort(self):
        values = [row(bandwidth=10000), row(2, bandwidth=30000, rating_key=20)]
        ranked = bandwidth_rankings(values, 'title_key', 'title')
        self.assertEqual(ranked[0]['peak'], 30000)
        self.assertEqual(ranked, bandwidth_rankings(values[::-1], 'title_key', 'title'))

    def test_daily_timezone_missing_dates_and_bandwidth(self):
        before = datetime(2026, 3, 8, 4, 30, tzinfo=timezone.utc).timestamp()
        after = datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc).timestamp()
        values = [row(started=before, bandwidth=10000), row(2, started=after, bandwidth=30000, location='wan'),
                  row(3, started=after + 86400), row(4, started=None, bandwidth=90000)]
        days = bandwidth_days(values, 'America/New_York')
        self.assertEqual([r['Date'] for r in days], [date(2026, 3, 7), date(2026, 3, 8), date(2026, 3, 9)])
        self.assertEqual(days[1]['remote_mean'], 30000)
        self.assertEqual(days[1]['high'], 1)
        self.assertIsNone(days[2]['mean'])
        self.assertIsNone(days[2]['high'])
        filtered = filter_history(values, HistoryQuery(date(2026, 3, 8), date(2026, 3, 8), 'America/New_York'))
        self.assertEqual(history_summary(filtered)['mean'], 30000)

    def test_public_normalized_schema(self):
        normalized = row(bandwidth=20000, ip_address='10.2.3.4', machine_id='private-machine', api_key='private-key', extra={'secret': 'private'})
        live = normalize_session({'bandwidth': 20000, 'ip_address': '10.2.3.4', 'machine_id': 'private-machine', 'api_key': 'private-key'})
        for data in [normalized, live]:
            for secret in ['10.2.3.4', 'private-machine', 'private-key', 'secret']:
                self.assertNotIn(secret, repr(data))


class CacheTests(unittest.TestCase):
    @patch('tautulli_client.requests.get')
    def test_cache_ttls_refresh_and_connection(self, get):
        get.side_effect = lambda *a, **k: reply({'data': [raw_row(bandwidth=20000)], 'recordsFiltered': 1} if k['params']['cmd'] == 'get_history' else {'sessions': []})
        live, history = {}, {}
        query = HistoryQuery(None, None, 'UTC')
        with patch('dashboard.time.monotonic', return_value=1000):
            read_live(live, 'host', 'key')
            read_history(history, 'host', 'key', query)
        with patch('dashboard.time.monotonic', return_value=1029):
            read_live(live, 'host', 'key')
            read_history(history, 'host', 'key', query)
        self.assertEqual(get.call_count, 3)
        with patch('dashboard.time.monotonic', return_value=1030):
            read_live(live, 'host', 'key')
        self.assertEqual(get.call_count, 4)
        with patch('dashboard.time.monotonic', return_value=1180):
            read_history(history, 'host', 'key', query)
        self.assertEqual(get.call_count, 5)
        read_live(live, 'host', 'key', True)
        read_history(history, 'host', 'key', query, True)
        self.assertEqual(get.call_count, 8)
        read_live(live, 'host', 'changed')
        read_history(history, 'host', 'changed', query)
        self.assertEqual(get.call_count, 11)


@patch.dict(os.environ, {'TAUTULLI_API_KEY': 'mock-only'})
class PageTests(unittest.TestCase):
    def source(self):
        source = Path('app.py').read_text()
        source = source.replace('def init_db() -> None:', 'def init_db() -> None:\n    raise AssertionError("Must not open SQLite")')
        source = source.replace('def get_disk_video_paths(folder: str) -> List[str]:', 'def get_disk_video_paths(folder: str) -> List[str]:\n    raise AssertionError("Must not access media")')
        nav = '["Overview", "Now Playing", "History", "Users", "Devices", "Transcoding", "Bandwidth", "Maintenance"]'
        return source.replace('st.radio("Navigation", ' + nav + ')', 'st.radio("Navigation", ' + nav + ', index=6)')

    def mock(self, command, bandwidth=True, empty=False):
        now = datetime.now(timezone.utc).timestamp()
        if command == 'get_users':
            return reply([{'user_id': 1, 'username': 'Viewer'}])
        if command == 'get_history':
            values = [] if empty else [raw_row(started=now, **({'bandwidth': 8000} if bandwidth else {})),
                                     raw_row(2, started=now, location='wan', transcode_decision='transcode', machine_id='private-machine', ip_address='10.2.3.4', **({'bandwidth': 25000} if bandwidth else {}))]
            return reply({'data': values, 'recordsFiltered': 0 if empty else 5000})
        if command == 'get_activity':
            return reply({'sessions': [] if empty else [dict(title='Low', user='Viewer', player='TV', platform='Roku', bandwidth=8000, local=1, transcode_decision='direct play'),
                           dict(title='High', user='Viewer', player='Browser', platform='Chrome', bandwidth=25000, local=0, transcode_decision='transcode', transcode_speed=2.5, quality_profile='Original', stream_video_resolution='1080', machine_id='private-machine', ip_address='10.2.3.4')]})
        self.assertEqual(command, 'get_server_info')
        return reply({})

    @patch('tautulli_client.requests.get')
    def test_full_page_refresh_caches_rankings_privacy_isolation(self, get):
        get.side_effect = lambda *a, **k: self.mock(k['params']['cmd'])
        with patch('sqlite3.connect', side_effect=AssertionError('No Maintenance DB')):
            at = AppTest.from_string(self.source()).run(timeout=20)
            self.assertFalse(at.exception)
            self.assertEqual(get.call_count, 4)
            self.assertTrue(any('Limited dataset' in w.value for w in at.warning))
            self.assertTrue(any(m.label == 'Current total bandwidth' and m.value == '33.00 Mbps' for m in at.metric))
            self.assertTrue(any(m.label == 'Current remote bandwidth' and m.value == '25.00 Mbps' for m in at.metric))
            self.assertEqual(at.dataframe[0].value['Title'].tolist(), ['High', 'Low'])
            self.assertEqual(at.dataframe[0].value['Playback'].tolist(), ['Transcode', 'Direct Play'])
            self.assertEqual(at.dataframe[0].value['High bandwidth'].tolist(), ['True', 'False'])
            for ranking in ['Devices / groups', 'Titles', 'Users']:
                next(s for s in at.selectbox if s.label == 'Bandwidth ranking').set_value(ranking).run()
                self.assertFalse(at.exception)
                rendered = repr([df.value for df in at.dataframe]) + repr([x.value for x in at.markdown])
                for private in ['private-machine', '10.2.3.4', 'mock-only']:
                    self.assertNotIn(private, rendered)
            at.number_input[0].set_value(25.0).run()
            self.assertEqual(at.dataframe[0].value['High bandwidth'].tolist(), ['False', 'False'])
            for page in ['Overview', 'Now Playing', 'History', 'Users', 'Devices', 'Transcoding', 'Bandwidth']:
                at.radio[0].set_value(page).run()
                self.assertFalse(at.exception)
            self.assertEqual(get.call_count, 4)
            at.button[0].click().run()
            self.assertFalse(at.exception)
            self.assertEqual(Counter(c.kwargs['params']['cmd'] for c in get.call_args_list),
                             {'get_activity': 2, 'get_server_info': 2, 'get_users': 2, 'get_history': 2})

    @patch('tautulli_client.requests.get')
    def test_stock_history_unavailable_current_still_available(self, get):
        get.side_effect = lambda *a, **k: self.mock(k['params']['cmd'], bandwidth=False)
        at = AppTest.from_string(self.source()).run(timeout=20)
        self.assertFalse(at.exception)
        self.assertTrue(any('stock Tautulli get_history' in i.value for i in at.info))
        self.assertTrue(any(m.label == 'Current total bandwidth' for m in at.metric))
        self.assertFalse(any(m.label == 'Average known playback bandwidth' for m in at.metric))
        self.assertFalse(any(s.label == 'Bandwidth ranking' for s in at.selectbox))

    @patch('tautulli_client.requests.get')
    def test_empty(self, get):
        get.side_effect = lambda *a, **k: self.mock(k['params']['cmd'], empty=True)
        at = AppTest.from_string(self.source()).run(timeout=20)
        self.assertFalse(at.exception)
        self.assertTrue(any('No active streams' in i.value for i in at.info))
        self.assertTrue(any('No playback history' in i.value for i in at.info))
        self.assertTrue(any(m.label == 'Current total bandwidth' and m.value == '0.00 Mbps' for m in at.metric))

    @patch('tautulli_client.requests.get')
    def test_failures_independent_and_no_stale_values(self, get):
        for failing in ['get_activity', 'get_history']:
            for failure in ['outage', 'auth', 'malformed']:
                with self.subTest(failing=failing, failure=failure):
                    get.side_effect = lambda *a, **k: self.mock(k['params']['cmd'])
                    at = AppTest.from_string(self.source()).run(timeout=20)
                    def respond(*a, **k):
                        cmd = k['params']['cmd']
                        if cmd != failing:
                            return self.mock(cmd)
                        if failure == 'outage':
                            raise requests.Timeout('private-secret')
                        if failure == 'auth':
                            return reply({}, status=401)
                        return reply({'sessions': None, 'data': None})
                    get.side_effect = respond
                    at.button[0].click().run()
                    self.assertFalse(at.exception)
                    labels = [m.label for m in at.metric]
                    self.assertEqual('Current total bandwidth' in labels, failing != 'get_activity')
                    self.assertEqual('Average known playback bandwidth' in labels, failing != 'get_history')
                    self.assertTrue(any('unavailable / degraded' in w.value for w in at.warning))
                    self.assertNotIn('private-secret', repr([w.value for w in at.warning]))

    @patch('tautulli_client.requests.get')
    def test_filters_shared_and_current_independent(self, get):
        get.side_effect = lambda *a, **k: self.mock(k['params']['cmd'])
        at = AppTest.from_string(self.source()).run(timeout=20)
        self.assertEqual(next(s for s in at.selectbox if s.label == 'Date range').value, 'Last 30 days')
        next(s for s in at.selectbox if s.label == 'Media type').set_value('episode')
        next(s for s in at.text_input if s.label == 'Search title').set_value('Nothing matches')
        next(b for b in at.button if b.label == 'Apply filters').click().run()
        self.assertFalse(at.exception)
        self.assertTrue(any('No playback history' in i.value for i in at.info))
        self.assertTrue(any(m.label == 'Current active streams' and m.value == '2' for m in at.metric))
        counts = Counter(c.kwargs['params']['cmd'] for c in get.call_args_list)
        self.assertEqual(counts['get_activity'], 1)
        self.assertEqual(counts['get_history'], 2)
        params = get.call_args.kwargs['params']
        self.assertEqual((params['media_type'], params['search'], params['length']), ('episode', 'Nothing matches', 1000))


if __name__ == '__main__':
    unittest.main()
