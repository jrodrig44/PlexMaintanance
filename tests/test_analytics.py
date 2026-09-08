"""Phase 2 tests: synthetic payloads only; no remote services or production database."""
import os
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
import requests
from streamlit.testing.v1 import AppTest
import analytics as a
import analytics_service as service
import tautulli_client as client


def history_row(row_id=1, user_id=1, **updates):
    result = dict(row_id=row_id, user_id=user_id, user='viewer' + str(user_id), friendly_name='Viewer ' + str(user_id),
                  started=1772902800, stopped=1772903400, play_duration=600, duration=600,
                  full_title='Film', title='Film', media_type='movie', rating_key=10,
                  percent_complete=10, transcode_decision='direct play', platform='Roku', player='TV', location='lan')
    result.update(updates)
    return result


def payload(rows, total=None):
    return {'data': rows, 'recordsFiltered': len(rows) if total is None else total}


def reply(data, result='success', status=200):
    return Mock(status_code=status, json=Mock(return_value={'response': {'result': result, 'data': data, 'message': 'never-show-secret'}}))


def query(**changes):
    return a.HistoryQuery(**dict(dict(start=None, end=None, timezone_name='America/New_York'), **changes))


class NormalizationTests(unittest.TestCase):
    def test_empty_history(self):
        data = a.normalize_history(payload([]), 1000)
        self.assertEqual(a.summary(data['rows'])['plays'], 0)
        self.assertEqual(a.summary(data['rows'])['watch_seconds'], 0)
        self.assertFalse(data['limited'])

    def test_movie_partial_playback(self):
        row = a.normalize_history_row(history_row(play_duration='263', duration=7200, percent_complete='4'))
        self.assertEqual(row['watch_seconds'], 263)
        self.assertEqual(row['completion'], 4)
        self.assertEqual(row['started'].tzinfo, timezone.utc)
        self.assertEqual(row['title_key'], 'rating:10')
        self.assertEqual(a.summary([row])['movies'], 1)

    def test_episode(self):
        row = a.normalize_history_row(history_row(media_type='episode', grandparent_title='Show', grandparent_rating_key=100,
                                               title='Pilot', full_title='Show - Pilot', parent_media_index=1, media_index=2))
        self.assertEqual((row['show'], row['season'], row['episode'], row['episode_title']), ('Show', '1', '2', 'Pilot'))
        self.assertEqual(a.summary([row])['episodes'], 1)

    def test_legacy_duration_seconds(self):
        raw = history_row(duration='120', paused_counter=30)
        del raw['play_duration']
        self.assertEqual(a.normalize_history_row(raw)['watch_seconds'], 120)  # Pauses already excluded.

    def test_missing_invalid_duration(self):
        for value in (None, '', 'bad', -1, True, False, float('nan'), float('inf')):
            row = a.normalize_history_row(history_row(play_duration=value))
            self.assertIsNone(row['watch_seconds'])
        missing = a.normalize_history_row(history_row(play_duration=None))
        valid = a.normalize_history_row(history_row(play_duration=30))
        stats = a.summary([missing, valid])
        self.assertIsNone(stats['watch_seconds'])
        self.assertEqual(stats['known_watch_seconds'], 30)
        self.assertEqual(stats['missing_duration'], 1)
        self.assertIsNone(a.summary([missing])['known_watch_seconds'])

    def test_missing_user_and_title(self):
        row = a.normalize_history_row({})
        self.assertEqual(row['user'], 'Unknown user')
        self.assertIsNone(a.summary([row])['users'])
        self.assertIsNone(a.summary([row])['titles'])
        row = a.normalize_history_row({'user_id': 9})
        self.assertEqual(row['user'], 'User 9')
        self.assertEqual(row['user_key'], 'id:9')

    def test_invalid_timestamp_and_percentage(self):
        for value in ('bad', None, -1, 1e30, float('nan')):
            self.assertIsNone(a.timestamp(value))
        self.assertIsNone(a.normalize_history_row({'percent_complete': 101})['completion'])

    def test_payload_validation(self):
        for data in ({}, {'data': None}, {'data': [None]}, payload([history_row()], 0), payload([history_row(group_count=2)])):
            with self.subTest(data=data), self.assertRaises(client.TautulliError):
                a.normalize_history(data, 1000)
        with self.assertRaises(client.TautulliError):
            a.normalize_history(payload([history_row(), history_row(2)]), 1)

    def test_duplicates_limited_unknown_counts(self):
        result = a.normalize_history(payload([history_row(), history_row(), history_row(2)], 20), 1000)
        self.assertEqual(len(result['rows']), 2)
        self.assertEqual(result['duplicates'], 1)
        self.assertTrue(result['limited'])
        self.assertTrue(a.normalize_history({'data': []}, 1000)['limited'])

    def test_privacy_allowlist(self):
        raw = history_row(ip_address='10.1.1.1', server_token='never-store', email='private@example.test')
        normalized = a.normalize_history_row(raw)
        self.assertFalse({'ip_address', 'server_token', 'email'} & normalized.keys())
        user = a.normalize_user(dict(user_id=2, username='Test', is_active=0, server_token='never-store'))
        self.assertNotIn('server_token', user)
        self.assertEqual(user['active'], 'Inactive')


class CalculationTests(unittest.TestCase):
    def setUp(self):
        self.rows = [a.normalize_history_row(history_row()), a.normalize_history_row(history_row(2, 2, transcode_decision='copy', play_duration=120)),
                     a.normalize_history_row(history_row(3, 2, rating_key=11, media_type='episode', full_title='Show - Pilot', title='Pilot',
                                                        grandparent_title='Show', grandparent_rating_key=100, transcode_decision='transcode', play_duration=60, location='wan'))]

    def test_summary_and_breakdown(self):
        stats = a.summary(self.rows)
        self.assertEqual((stats['plays'], stats['users'], stats['titles'], stats['watch_seconds']), (3, 2, 2, 780))
        self.assertEqual(stats['decisions'], dict(zip(a.DECISIONS, (1, 1, 1))))
        self.assertEqual((stats['local'], stats['remote']), (2, 1))
        self.rows[0]['decision'] = None
        self.assertTrue(all(v is None for v in a.summary(self.rows)['decisions'].values()))

    def test_user_aggregation_deleted_and_no_history(self):
        users = [a.normalize_user({'user_id': 1, 'username': 'viewer1'}), a.normalize_user({'user_id': 3, 'username': 'new'})]
        groups = {u['key']: u for u in a.aggregate_users(self.rows, users)}
        self.assertEqual(groups['id:2']['plays'], 2)
        self.assertEqual(groups['id:2']['watch_seconds'], 180)
        self.assertEqual(groups['id:3']['plays'], 0)
        self.assertEqual(groups['id:2']['platform'], 'Roku')
        self.assertIsNone(groups['id:2']['active'])

    def test_title_show_and_daily_aggregation(self):
        self.assertEqual(a.rankings(self.rows)[0]['plays'], 2)
        self.assertEqual(a.rankings(self.rows, 'show_key', 'show')[0]['name'], 'Show')
        self.assertEqual(sum(day['Plays'] for day in a.aggregate_days(self.rows, 'America/New_York')), 3)
        self.assertEqual(sum(day['Watch minutes (known)'] for day in a.aggregate_days(self.rows, 'America/New_York')), 13)

    def test_user_media_title_filters(self):
        self.assertEqual(len(a.filter_history(self.rows, query(user_key='id:2'))), 2)
        self.assertEqual(len(a.filter_history(self.rows, query(media_type='episode'))), 1)
        self.assertEqual(len(a.filter_history(self.rows, query(search='PILOT'))), 1)
        self.assertEqual(len(a.filter_history(self.rows, query(search='absent'))), 0)

    def test_date_presets_custom(self):
        today = date(2026, 9, 7)
        self.assertEqual(a.date_range('Last 30 days', today), (date(2026, 8, 9), today))
        self.assertEqual(a.date_range('Last 7 days', today)[0], date(2026, 9, 1))
        self.assertEqual(a.date_range('Last 90 days', today)[0], today - a.timedelta(days=89))
        self.assertEqual(a.date_range('This year', today)[0], date(2026, 1, 1))
        self.assertEqual(a.date_range('All available', today), (None, None))
        with self.assertRaises(ValueError):
            a.date_range('Custom', today, today, date(2025, 1, 1))

    def test_dst_boundaries(self):
        q = query(start=date(2026, 3, 8), end=date(2026, 3, 8))
        start, end = q.bounds()
        self.assertEqual(end.timestamp() - start.timestamp(), 23 * 3600)
        rows = [a.normalize_history_row(history_row(i, started=t)) for i, t in enumerate([start.timestamp() - 1, start.timestamp(), end.timestamp() - 1, end.timestamp()])]
        self.assertEqual(len(a.filter_history(rows, q)), 2)
        self.assertEqual(a.filter_history([a.normalize_history_row({})], q), [])
        self.assertEqual(len(a.filter_history([a.normalize_history_row({})], query())), 1)
        self.assertIn('-0500', a.format_datetime(start, q.timezone_name))

    def test_cross_timezone_query_padding_and_autumn_dst(self):
        from zoneinfo import ZoneInfo
        for local, remote in [('Etc/GMT+12', 'Pacific/Kiritimati'), ('Pacific/Kiritimati', 'Etc/GMT+12')]:
            q = query(start=date(2026, 3, 8), end=date(2026, 3, 8), timezone_name=local)
            start, end = q.bounds()
            params = q.api_params()
            remote_start = datetime.fromisoformat(params['after']).replace(tzinfo=ZoneInfo(remote))
            remote_end = datetime.fromisoformat(params['before']).replace(tzinfo=ZoneInfo(remote))
            self.assertLess(remote_start.timestamp(), start.timestamp())
            self.assertGreater(remote_end.timestamp(), end.timestamp())
        start, end = query(start=date(2026, 11, 1), end=date(2026, 11, 1)).bounds()
        self.assertEqual(end.timestamp() - start.timestamp(), 25 * 3600)

    def test_formatting(self):
        self.assertEqual(a.format_duration(11880), '3 hr 18 min')
        self.assertEqual(a.format_duration(None), 'Unavailable')
        self.assertEqual(a.format_duration(42 * 60), '42 min')


class ServiceTests(unittest.TestCase):
    @patch('tautulli_client.requests.get')
    def test_bounded_params_and_list_response(self, get):
        get.return_value = reply(payload([]))
        q = query(start=date(2026, 3, 8), end=date(2026, 3, 10), user_key='id:2', media_type='episode', search='Pilot')
        result = service.read_history({}, 'server', 'test-key', q)
        self.assertIn('data', result)
        params = get.call_args.kwargs['params']
        self.assertEqual((params['start'], params['length'], params['grouping'], params['include_activity']), (0, 1000, 0, 0))
        self.assertEqual((params['user_id'], params['media_type'], params['search']), ('2', 'episode', 'Pilot'))
        self.assertLess(params['after'], '2026-03-08')
        self.assertGreater(params['before'], '2026-03-10')
        get.return_value = reply([{'user_id': 2, 'username': 'Test', 'server_token': 'private'}])
        result = service.read_users({}, 'server', 'test-key')
        self.assertEqual(result['data'][0]['key'], 'id:2')
        self.assertNotIn('private', repr(result))

    @patch('tautulli_client.requests.get')
    @patch('analytics_service.time.monotonic')
    def test_cache_ttl_refresh_connection_date_filter_identity(self, clock, get):
        get.return_value = reply(payload([]))
        state = {}
        clock.return_value = 0
        service.read_history(state, 'server', 'key', query())
        clock.return_value = 179
        service.read_history(state, 'server', 'key', query())
        self.assertEqual(get.call_count, 1)
        clock.return_value = 180
        service.read_history(state, 'server', 'key', query())
        self.assertEqual(get.call_count, 2)
        service.read_history(state, 'server', 'key', query(), True)
        service.read_history(state, 'server', 'key', query(start=date(2026, 1, 1)))
        service.read_history(state, 'server', 'key', query(user_key='id:2'))
        service.read_history(state, 'server', 'key', query(media_type='movie'))
        service.read_history(state, 'server', 'key', query(search='Film'))
        self.assertEqual(get.call_count, 7)
        service.read_history(state, 'other', 'key', query())
        self.assertEqual(len(state['entries']), 1)
        service.read_history(state, 'other', 'changed', query())
        self.assertEqual(get.call_count, 9)
        self.assertNotIn('changed', repr(state))

    @patch('tautulli_client.requests.get')
    def test_failed_refresh_hides_stale_and_caches_error(self, get):
        get.return_value = reply(payload([history_row()]))
        state = {}
        self.assertIn('data', service.read_history(state, 'server', 'key', query()))
        get.side_effect = requests.Timeout('never-show-secret')
        result = service.read_history(state, 'server', 'key', query(), True)
        self.assertIn('error', result)
        self.assertNotIn('data', result)
        self.assertNotIn('never-show-secret', repr(result))
        service.read_history(state, 'server', 'key', query())
        self.assertEqual(get.call_count, 2)

    @patch('tautulli_client.requests.get')
    def test_outage_auth_and_malformed(self, get):
        for result in (reply({}, result='error'), reply({}, status=401), reply({'data': None}), reply([], status=200)):
            get.return_value = result
            self.assertIn('error', service.read_history({}, 'server', 'key', query()))
        get.return_value = reply({})
        self.assertIn('error', service.read_users({}, 'server', 'key'))
        get.return_value = reply([None])
        self.assertIn('error', service.read_users({}, 'server', 'key'))

    @patch('tautulli_client.requests.get', return_value=reply(payload([])))
    def test_cache_bounded_and_invalid_limit(self, get):
        state = {}
        for n in range(12):
            service.read_history(state, 'host', 'key', query(search=str(n)))
        self.assertLessEqual(len(state['entries']), 8)
        with self.assertRaises(client.TautulliError):
            service.read_history(state, 'host', 'key', query(limit=5000))


class AnalyticsUITests(unittest.TestCase):
    def source(self, page):
        source = Path('app.py').read_text()
        source = source.replace('def init_db() -> None:', 'def init_db() -> None:\n    raise AssertionError("Analytics must not open SQLite")')
        source = source.replace('def get_disk_video_paths(folder: str) -> List[str]:', 'def get_disk_video_paths(folder: str) -> List[str]:\n    raise AssertionError("Media path unavailable")')
        nav = '["Overview", "Now Playing", "History", "Users", "Devices", "Transcoding", "Bandwidth", "Maintenance"]'
        return source.replace('st.radio("Navigation", ' + nav + ')', 'st.radio("Navigation", ' + nav + ', index=' + str(2 if page == 'History' else 3) + ')')

    @patch.dict(os.environ, {'TAUTULLI_API_KEY': 'mock-key', 'PLEX_DASHBOARD_TIMEZONE': 'America/New_York'})
    @patch('tautulli_client.requests.get')
    def test_history_users_without_media_and_no_n_plus_one(self, get):
        now = datetime.now(timezone.utc).timestamp()
        def call(*args, **kwargs):
            command = kwargs['params']['cmd']
            if command == 'get_users':
                return reply([{'user_id': 1, 'username': 'viewer1', 'friendly_name': 'Viewer 1', 'is_active': 1}])
            self.assertEqual(command, 'get_history')
            return reply(payload([history_row(started=now), history_row(2, 2, started=now, media_type='episode', title='Pilot', full_title='Show - Pilot', grandparent_title='Show')]))
        get.side_effect = call
        for page in ('History', 'Users'):
            with self.subTest(page=page):
                get.reset_mock()
                at = AppTest.from_string(self.source(page)).run(timeout=20)
                self.assertFalse(at.exception)
                self.assertTrue(any(m.label == 'Total plays (loaded)' and m.value == '2' for m in at.metric))
                self.assertEqual(get.call_count, 2)
                at.run()
                self.assertEqual(get.call_count, 2)
                if page == 'Users':
                    at.selectbox[-1].set_value(1).run()
                    self.assertEqual(get.call_count, 2)
                at.button[0].click().run()
                self.assertEqual(get.call_count, 4)
                self.assertFalse(at.exception)

    @patch.dict(os.environ, {'TAUTULLI_API_KEY': 'mock-key'})
    @patch('tautulli_client.requests.get')
    def test_filter_submission_and_directory_failure(self, get):
        now = datetime.now(timezone.utc).timestamp()
        def call(*args, **kwargs):
            if kwargs['params']['cmd'] == 'get_users':
                return reply({}, result='error')
            return reply(payload([history_row(started=now)], 3000))
        get.side_effect = call
        at = AppTest.from_string(self.source('History')).run()
        self.assertFalse(at.exception)
        self.assertTrue(any('User directory unavailable' in item.value for item in at.warning))
        self.assertTrue(any('Limited dataset' in item.value for item in at.warning))
        search = next(widget for widget in at.text_input if widget.label == 'Search title')
        search.set_value('Film')
        next(widget for widget in at.button if widget.label == 'Apply filters').click().run()
        self.assertFalse(at.exception)
        self.assertEqual(get.call_args.kwargs['params']['search'], 'Film')
        self.assertEqual(get.call_count, 3)

    @patch.dict(os.environ, {'TAUTULLI_API_KEY': 'mock-key'})
    @patch('tautulli_client.requests.get')
    def test_empty_failure_directory_missing_and_unrelated_page(self, get):
        get.side_effect = lambda *args, **kwargs: reply([] if kwargs['params']['cmd'] == 'get_users' else payload([]))
        at = AppTest.from_string(self.source('History')).run()
        self.assertFalse(at.exception)
        self.assertTrue(any('No playback history' in item.value for item in at.info))
        get.side_effect = requests.Timeout('never-show-secret')
        at.button[0].click().run()
        self.assertFalse(at.exception)
        self.assertTrue(any('History unavailable' in item.value for item in at.warning))
        self.assertEqual(len(at.dataframe), 0)
        get.side_effect = lambda *args, **kwargs: reply({'sessions': []} if kwargs['params']['cmd'] == 'get_activity' else {})
        at.radio[0].set_value('Now Playing').run()
        self.assertFalse(at.exception)
        self.assertTrue(any('Nothing is currently playing' in item.value for item in at.info))


if __name__ == '__main__':
    unittest.main()
