"""Synthetic Phase 3 coverage; no external services or media storage required."""
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
import requests
from streamlit.testing.v1 import AppTest
from analytics import normalize_history_row, rankings, HistoryQuery
from playback import device_identity, normalize_decision, normalize_resolution, is_4k, normalize_codec
from device_analytics import (aggregate_devices, device_summary, select_device, decision_coverage,
                              location_coverage, transcode_category, transcode_summary, transcode_rankings)
from tautulli_client import normalize_session
import analytics_service


def raw_row(i=1, **extra):
    return dict(dict(row_id=i, user_id=1, user='Viewer', full_title='Film', title='Film', rating_key=10,
                     media_type='movie', machine_id='client-one', player='Living room', platform='Roku', product='Plex',
                     started=1772902800 + i, play_duration=60, transcode_decision='direct play', location='lan'), **extra)


def row(i=1, **extra):
    return normalize_history_row(raw_row(i, **extra))


def reply(data, result='success', status=200):
    return Mock(status_code=status, json=Mock(return_value={'response': {'result': result, 'data': data, 'message': 'private-secret'}}))


class DeviceTests(unittest.TestCase):
    def test_composite_identity(self):
        a = device_identity({'player': 'TV', 'platform': 'Roku', 'product': 'Plex'})
        b = device_identity({'player': 'Bedroom', 'platform': 'Roku', 'product': 'Plex'})
        self.assertNotEqual(a['device_key'], b['device_key'])
        self.assertEqual(a['device_scope'], 'Player label group')
        self.assertEqual(a, device_identity({'product': 'Plex', 'platform': 'Roku', 'player': 'TV'}))

    def test_incomplete_identity(self):
        self.assertEqual(device_identity({'platform': 'Roku'})['device_scope'], 'Platform/product group')
        self.assertIsNone(device_identity({})['device_key'])
        self.assertEqual(device_identity({'machine_id': 'abc'})['device_scope'], 'Identified client')

    def test_same_platform_distinct_ids_and_rename(self):
        first = row()
        second = row(machine_id='client-two')
        renamed = row(player='Renamed')
        self.assertNotEqual(first['device_key'], second['device_key'])
        self.assertEqual(first['device_key'], renamed['device_key'])
        self.assertEqual(device_summary([first, second, renamed])['identified'], 2)

    def test_unknown_does_not_inflate_devices(self):
        values = [normalize_history_row({}), row(machine_id=None), row()]
        stats = device_summary(values)
        self.assertEqual((stats['identified'], stats['label_groups'], stats['unknown_records']), (1, 1, 1))

    def test_ip_not_identity_and_private_fields_discarded(self):
        normalized = row(machine_id='sensitive-client-identifier', ip_address='10.1.1.1', server_token='secret')
        self.assertNotIn('sensitive-client-identifier', repr(normalized))
        self.assertNotIn('10.1.1.1', repr(normalized))
        self.assertNotIn('secret', repr(normalized))
        self.assertNotIn('machine_id', normalized)
        self.assertEqual(device_identity({'machine_id': '10.1.1.1'})['device_scope'], 'Unknown')

    def test_device_aggregation_watch_time_activity_content(self):
        values = [row(), row(2, play_duration=120), row(3, machine_id='second', play_duration=None)]
        groups = aggregate_devices(values)
        self.assertEqual(groups[0]['plays'], 2)
        self.assertEqual(groups[0]['known_watch_seconds'], 180)
        self.assertEqual(groups[0]['last_activity'], values[1]['started'])
        self.assertEqual(groups[0]['top_content'], 'Film')
        self.assertIsNone(groups[1]['known_watch_seconds'])
        self.assertEqual(len(select_device(values, groups[0]['key'])), 2)

    def test_platform_ranking_and_deterministic_sort(self):
        values = [row(), row(2, machine_id='second'), row(3, platform='Chrome', machine_id='third')]
        self.assertEqual(rankings(values, 'platform', 'platform')[0]['plays'], 2)
        self.assertEqual([d['key'] for d in aggregate_devices(values)], [d['key'] for d in aggregate_devices(values[::-1])])


class DecisionTests(unittest.TestCase):
    def test_direct_stream_shared_live_history_mapping(self):
        for value in ('copy', 'direct stream', 'DIRECT STREAM'):
            self.assertEqual(normalize_decision(value), 'Direct Stream')
            self.assertEqual(row(transcode_decision=value)['decision'], 'Direct Stream')
            self.assertEqual(normalize_session({'transcode_decision': value})['decision'], 'Direct Stream')

    def test_known_subset_rates(self):
        values = [row(i) for i in range(60)] + [row(i, transcode_decision='copy') for i in range(10)]
        values += [row(i, transcode_decision='transcode') for i in range(20)] + [row(i, transcode_decision=None) for i in range(10)]
        coverage = decision_coverage(values)
        self.assertEqual((coverage['known'], coverage['unknown']), (90, 10))
        self.assertAlmostEqual(coverage['rates']['Direct Play'], 66.6666667)
        self.assertAlmostEqual(coverage['rates']['Transcode'], 22.2222222)

    def test_unknown_empty_and_location_rates(self):
        for values in ([], [row(transcode_decision='unexpected')]):
            self.assertIsNone(decision_coverage(values)['rates']['Direct Play'])
        location = location_coverage([row(), row(location='wan'), row(location=None)])
        self.assertEqual(location['unknown'], 1)
        self.assertEqual(location['rates']['Remote'], 50)

    def test_video_only(self):
        self.assertEqual(transcode_category(row(transcode_decision='transcode', video_decision='transcode', audio_decision='copy')), 'Video-only transcode')

    def test_audio_only(self):
        self.assertEqual(transcode_category(row(transcode_decision='transcode', video_decision='direct play', audio_decision='transcode')), 'Audio-only transcode')

    def test_video_and_audio(self):
        self.assertEqual(transcode_category(row(transcode_decision='transcode', stream_video_decision='transcode', stream_audio_decision='transcode')), 'Video + audio transcode')

    def test_unknown_classification_and_conflicts(self):
        for values in ({}, {'video_decision': 'transcode'}, {'audio_decision': 'transcode'}, {'video_decision': 'copy', 'audio_decision': 'copy'}):
            self.assertEqual(transcode_category(row(transcode_decision='transcode', **values)), 'Unknown')
        self.assertEqual(transcode_category(row(video_decision='transcode')), 'Unknown')
        self.assertEqual(transcode_category(row(transcode_decision=None)), 'Unknown')
        self.assertEqual(transcode_category(row()), 'Direct Play')
        self.assertEqual(transcode_category(row(transcode_decision='copy')), 'Direct Stream')


class TranscodingTests(unittest.TestCase):
    def test_4k_normalization(self):
        for value in ('4k', '4K', 'uhd', '2160', '2160p', 2160, ' 4K UHD '):
            self.assertEqual(normalize_resolution(value), '4K')
            self.assertTrue(is_4k(value))
        for value in ('1080', '1080p', '720p', '8k'):
            self.assertFalse(is_4k(value))
        for value in ('', None, 'high bitrate', 'HD', True):
            self.assertIsNone(is_4k(value))

    def test_non_4k_sources_establish_zero_4k_transcodes(self):
        stats = transcode_summary([row(video_resolution='1080', transcode_decision='transcode')])
        self.assertEqual(stats['four_k'], 0)
        self.assertEqual(stats['four_k_transcode_count'], 0)
        self.assertEqual(stats['four_k_direct_play_count'], 0)

    def test_no_guess_from_stream_or_bitrate(self):
        normalized = row(stream_video_resolution='4k', bitrate=100000)
        self.assertIsNone(normalized['source_resolution'])
        self.assertIsNone(transcode_summary([normalized])['four_k'])

    def test_4k_transcodes_and_direct_play(self):
        values = [row(video_resolution='2160'), row(2, video_resolution='4k', transcode_decision='transcode'),
                  row(3, video_resolution='1080', transcode_decision='transcode'), row(4, transcode_decision=None)]
        stats = transcode_summary(values)
        self.assertEqual(stats['four_k'], 2)
        self.assertEqual(len(stats['four_k_transcodes']), 1)
        self.assertEqual(stats['four_k_decisions']['counts']['Direct Play'], 1)
        self.assertEqual(stats['resolution_unknown'], 1)

    def test_video_audio_and_remote_counts_coverage(self):
        values = [row(transcode_decision='transcode', location='wan', video_decision='transcode', audio_decision='copy'),
                  row(2, transcode_decision='transcode', location=None, video_decision='copy', audio_decision='transcode'),
                  row(3, transcode_decision='transcode', location='lan')]
        stats = transcode_summary(values)
        self.assertEqual(stats['video_transcodes'], 1)
        self.assertEqual(stats['audio_only'], 1)
        self.assertEqual(stats['remote']['counts']['Remote'], 1)
        self.assertEqual(stats['remote']['unknown'], 1)
        self.assertEqual(stats['classified_transcodes'], 2)

    def test_transcodes_by_user_device_title_platform_show(self):
        values = [row(transcode_decision='transcode'), row(2, transcode_decision='transcode', media_type='episode',
                  grandparent_title='Show', grandparent_rating_key=20), row(3)]
        ranked = transcode_rankings(values)
        for name in ('Users', 'Devices / groups', 'Titles', 'Platforms'):
            self.assertEqual(ranked[name][0]['plays'], 2)
        self.assertEqual(ranked['Shows'][0]['name'], 'Show')

    def test_optional_field_allowlist_and_normalization(self):
        normalized = row(video_codec='H.265', audio_codec='AAC', container='MKV', stream_video_codec='h264',
                         stream_audio_codec='aac', stream_container='mpegts', stream_video_resolution='1080',
                         stream_subtitle_decision='copy', unrelated='discard')
        self.assertEqual(normalized['video_codec'], 'hevc')
        self.assertEqual(normalized['stream_resolution'], '1080p')
        self.assertEqual(normalized['subtitle_decision'], 'Direct Stream')
        self.assertEqual(normalized['audio_codec'], 'aac')
        self.assertEqual(normalized['container'], 'mkv')
        self.assertEqual(normalized['stream_video_codec'], 'h264')
        self.assertEqual(normalized['stream_audio_codec'], 'aac')
        self.assertEqual(normalized['stream_container'], 'mpegts')
        self.assertNotIn('unrelated', normalized)
        self.assertIsNone(normalize_codec('https://private/?token=secret'))

    def test_stock_history_detailed_fields_unavailable(self):
        normalized = row(transcode_decision='transcode')
        for name in ('source_resolution', 'stream_resolution', 'video_codec', 'audio_codec', 'container', 'video_decision', 'audio_decision', 'subtitle_decision'):
            self.assertIsNone(normalized[name])
        stats = transcode_summary([normalized])
        self.assertIsNone(stats['four_k'])
        self.assertIsNone(stats['video_transcodes'])
        self.assertIsNone(stats['audio_only'])


class CacheUpgradeTests(unittest.TestCase):
    @patch('tautulli_client.requests.get')
    def test_schema_upgrade_drops_old_normalized_history(self, get):
        query = HistoryQuery(None, None, 'UTC')
        state = {}
        _, entries = analytics_service.connection_cache(state, 'host', 'key')
        entries[('history', query)] = {'attempt': float('inf'), 'data': {'rows': [{}]}}
        get.return_value = reply({'data': [raw_row()], 'recordsFiltered': 1})
        data = analytics_service.read_history(state, 'host', 'key', query)
        self.assertIn('device_key', data['data']['rows'][0])
        analytics_service.read_history(state, 'host', 'key', query)
        self.assertEqual(get.call_count, 1)


class PageTests(unittest.TestCase):
    def source(self, page):
        source = Path('app.py').read_text()
        source = source.replace('def init_db() -> None:', 'def init_db() -> None:\n    raise AssertionError("Must not open SQLite")')
        source = source.replace('def get_disk_video_paths(folder: str) -> List[str]:', 'def get_disk_video_paths(folder: str) -> List[str]:\n    raise AssertionError("Must not access media")')
        nav = '["Overview", "Now Playing", "History", "Users", "Devices", "Transcoding", "Bandwidth", "Maintenance"]'
        return source.replace('st.radio("Navigation", ' + nav + ')', 'st.radio("Navigation", ' + nav + ', index=' + str(4 if page == 'Devices' else 5) + ')')

    @patch.dict(os.environ, {'TAUTULLI_API_KEY': 'mock-only'})
    @patch('tautulli_client.requests.get')
    def test_devices_transcoding_cache_refresh_drilldown_no_n_plus_one(self, get):
        now = datetime.now(timezone.utc).timestamp()
        values = [raw_row(started=now), raw_row(2, started=now, transcode_decision='copy'),
                  raw_row(3, started=now, transcode_decision='transcode', machine_id='client-two', video_resolution='4k', video_decision='transcode', audio_decision='copy'),
                  raw_row(4, started=now, transcode_decision=None)]
        def call(*args, **kwargs):
            command = kwargs['params']['cmd']
            self.assertIn(command, ('get_users', 'get_history'))
            return reply([{'user_id': 1, 'username': 'Viewer'}] if command == 'get_users' else {'data': values, 'recordsFiltered': 4000})
        get.side_effect = call
        at = AppTest.from_string(self.source('Devices')).run(timeout=20)
        self.assertFalse(at.exception)
        self.assertEqual(get.call_count, 2)
        self.assertTrue(any('Limited dataset' in w.value for w in at.warning))
        self.assertTrue(any(m.label == 'Unique identified devices' and m.value == '2' for m in at.metric))
        next(s for s in at.selectbox if s.label == 'Device / group details').set_value(1).run()
        self.assertEqual(get.call_count, 2)
        at.radio[0].set_value('Transcoding').run()
        self.assertFalse(at.exception)
        self.assertEqual(get.call_count, 2)
        self.assertTrue(any(m.label == 'Transcode % of known' and m.value == '33.3%' for m in at.metric))
        self.assertTrue(any(m.label == '4K transcodes (known decisions)' and m.value == '1' for m in at.metric))
        for choice in ('Users', 'Source video codecs', 'Devices / groups'):
            next(s for s in at.selectbox if s.label == 'Transcode ranking').set_value(choice).run()
        self.assertEqual(get.call_count, 2)
        at.button[0].click().run()
        self.assertEqual(get.call_count, 4)
        for name in ('History', 'Users', 'Devices'):
            at.radio[0].set_value(name).run()
            self.assertFalse(at.exception)
        self.assertEqual(get.call_count, 4)
        self.assertNotIn('client-two', repr([df.value for df in at.dataframe]))

    @patch.dict(os.environ, {'TAUTULLI_API_KEY': 'mock-only'})
    @patch('tautulli_client.requests.get')
    def test_empty_pages(self, get):
        get.side_effect = lambda *a, **k: reply([] if k['params']['cmd'] == 'get_users' else {'data': [], 'recordsFiltered': 0})
        for page, expected in [('Devices', 'No device activity'), ('Transcoding', 'No playback history available')]:
            at = AppTest.from_string(self.source(page)).run(timeout=20)
            self.assertFalse(at.exception)
            self.assertTrue(any(expected in i.value for i in at.info))

    @patch.dict(os.environ, {'TAUTULLI_API_KEY': 'mock-only'})
    @patch('tautulli_client.requests.get')
    def test_page_failures_and_recovery_to_live(self, get):
        for page in ('Devices', 'Transcoding'):
            for failure in ('outage', 'auth', 'malformed'):
                def call(*args, **kwargs):
                    if failure == 'outage':
                        raise requests.Timeout('mock-only')
                    return reply({}, result='error') if failure == 'auth' else reply({'data': None})
                get.side_effect = call
                at = AppTest.from_string(self.source(page)).run(timeout=20)
                self.assertFalse(at.exception)
                self.assertTrue(any('History unavailable' in w.value for w in at.warning))
                get.side_effect = lambda *a, **k: reply({'sessions': []} if k['params']['cmd'] == 'get_activity' else {})
                at.radio[0].set_value('Overview').run()
                self.assertFalse(at.exception)
                at.radio[0].set_value('Now Playing').run()
                self.assertFalse(at.exception)

    @patch.dict(os.environ, {'TAUTULLI_API_KEY': 'mock-only'})
    @patch('tautulli_client.requests.get')
    def test_stock_missing_fields_and_all_unknown(self, get):
        now = datetime.now(timezone.utc).timestamp()
        get.side_effect = lambda *a, **k: reply([] if k['params']['cmd'] == 'get_users' else {'data': [dict(row_id=1, started=now)], 'recordsFiltered': 1})
        for page in ('Devices', 'Transcoding'):
            at = AppTest.from_string(self.source(page)).run(timeout=20)
            self.assertFalse(at.exception)
            self.assertTrue(any(m.label == 'Transcode % of known' and m.value == 'Unavailable' for m in at.metric))


if __name__ == '__main__':
    unittest.main()
