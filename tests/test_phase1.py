import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import pandas as pd
import requests
from streamlit.testing.v1 import AppTest
import app
import dashboard
import tautulli_client as client


def response(data=None, result='success', status=200):
    return Mock(status_code=status, json=Mock(return_value={'response': {'result': result, 'data': data, 'message': 'secret-do-not-display'}}))


def session(user='1', decision='direct play', **extra):
    return dict(user_id=user, user='Viewer', title='Movie', transcode_decision=decision, **extra)


class ClientTests(unittest.TestCase):
    def test_zero(self):
        a = client.normalize_activity({'sessions': [], 'stream_count': '0'})
        self.assertEqual(a['metrics']['Active streams'], 0)
        self.assertEqual(a['metrics']['Unique active users'], 0)
        self.assertIsNone(a['metrics']['Total bandwidth'])

    def test_direct_play(self):
        a = client.normalize_activity({'sessions': [session()]})
        self.assertEqual(a['metrics']['Direct Play'], 1)

    def test_transcode(self):
        s = client.normalize_session(session(decision='transcode', view_offset='30000', duration='60000', transcode_speed='2.5'))
        self.assertEqual(s['decision'], 'Transcode')
        self.assertEqual(s['progress'], 50)
        self.assertEqual(s['speed'], 2.5)

    def test_multiple_users_metrics(self):
        a = client.normalize_activity({'sessions': [session(), session(decision='copy'), session('2', 'transcode')], 'total_bandwidth': '5000'})
        self.assertEqual(a['metrics']['Unique active users'], 2)
        self.assertEqual(a['metrics']['Direct Stream'], 1)
        self.assertEqual(a['metrics']['Transcode'], 1)
        self.assertEqual(a['metrics']['Total bandwidth'], 5000)

    def test_optional_missing(self):
        a = client.normalize_activity({'sessions': [{}]})
        self.assertIsNone(a['metrics']['Unique active users'])
        self.assertIsNone(a['metrics']['Direct Play'])
        self.assertIsNone(a['sessions'][0]['progress'])
        self.assertIsNone(client.number('nan'))

    def test_malformed_activity(self):
        for data in ({}, {'sessions': None}, {'sessions': [1]}, {'sessions': [], 'stream_count': 1}):
            with self.subTest(data=data), self.assertRaises(client.TautulliError):
                client.normalize_activity(data)

    @patch('tautulli_client.requests.get')
    def test_transport_success(self, get):
        get.return_value = response({'sessions': []})
        self.assertEqual(client.get_activity('server', 'runtime-secret')['sessions'], [])
        self.assertEqual(get.call_args.kwargs['params']['cmd'], 'get_activity')
        self.assertFalse(get.call_args.kwargs['allow_redirects'])

    @patch('tautulli_client.requests.get')
    def test_safe_failures(self, get):
        for error in (requests.Timeout('secret-do-not-display'), requests.ConnectionError('secret-do-not-display'), requests.HTTPError('secret-do-not-display')):
            get.side_effect = error
            with self.assertRaises(client.TautulliError) as caught:
                client.get_activity('server', 'runtime-secret')
            self.assertNotIn('secret', str(caught.exception))

    @patch('tautulli_client.requests.get')
    def test_api_auth_and_malformed(self, get):
        for reply in (response({}, 'error'), response({}, status=401), Mock(status_code=200, json=Mock(return_value=[])), Mock(status_code=200, json=Mock(side_effect=ValueError('secret')))):
            get.return_value = reply
            with self.assertRaises(client.TautulliError) as caught:
                client.get_activity('server', 'runtime-secret')
            self.assertNotIn('secret', str(caught.exception))

    @patch('tautulli_client.requests.get')
    def test_no_key_no_request(self, get):
        with self.assertRaises(client.TautulliError):
            client.get_activity('server', '')
        get.assert_not_called()
        self.assertIn('os.environ.get("TAUTULLI_API_KEY", "")', Path('app.py').read_text())

    def test_url(self):
        self.assertEqual(client.normalize_base_url('server'), 'http://server:8181')
        self.assertEqual(client.normalize_base_url('https://server/tautulli/'), 'https://server/tautulli')
        for url in ('http://user:secret@server', 'http://server?apikey=secret', ''):
            with self.assertRaises(client.TautulliError):
                client.normalize_base_url(url)

    @patch('tautulli_client.requests.get')
    def test_artwork_failure_and_unsafe_path(self, get):
        self.assertIsNone(client.get_artwork('server', 'key', 'https://other/image'))
        get.assert_not_called()
        get.side_effect = requests.Timeout()
        self.assertIsNone(client.get_artwork('server', 'key', '/library/metadata/1/thumb/2'))


    @patch('tautulli_client.requests.get')
    def test_artwork_success(self, get):
        from io import BytesIO
        from PIL import Image
        content = BytesIO()
        Image.new('RGB', (2, 2)).save(content, format='PNG')
        get.return_value = Mock(status_code=200, content=content.getvalue())
        self.assertEqual(client.get_artwork('host', 'key', '/library/metadata/1/thumb/2'), content.getvalue())
        self.assertEqual(get.call_args.kwargs['params']['cmd'], 'pms_image_proxy')

    def test_episode_fields(self):
        item = client.normalize_session(dict(media_type='episode', title='Episode', grandparent_title='Show', parent_media_index='2', media_index='3', local=True, stream_video_resolution='1080', duration='0'))
        self.assertEqual(item['episode_title'], 'Episode')
        self.assertEqual(item['location'], 'Local')
        self.assertEqual(item['resolution'], '1080')
        self.assertIsNone(item['progress'])


class RefreshTests(unittest.TestCase):
    @patch('dashboard.get_server_info', return_value={})
    @patch('dashboard.get_activity', return_value={'sessions': [], 'metrics': {}})
    def test_cache_refresh_and_credentials(self, activity, server):
        state = {}
        dashboard.read_live(state, 'host', 'key')
        dashboard.read_live(state, 'host', 'key')
        self.assertEqual(activity.call_count, 1)
        dashboard.read_live(state, 'host', 'key', True)
        self.assertEqual(activity.call_count, 2)
        success = state['success']
        activity.side_effect = client.TautulliError('Offline')
        dashboard.read_live(state, 'host', 'key', True)
        self.assertEqual(state['success'], success)
        dashboard.read_live(state, 'host', 'key')
        self.assertEqual(activity.call_count, 3)
        dashboard.read_live(state, 'host', 'changed')
        self.assertNotIn('success', state)


class UITests(unittest.TestCase):
    @patch('tautulli_client.requests.get')
    @patch('app.get_disk_video_paths', side_effect=AssertionError('Live pages must not access media'))
    @patch.dict(os.environ, {'TAUTULLI_API_KEY': 'test-only'})
    def test_pages_active_empty_outage_and_missing_mount(self, disk, get):
        def reply(*args, **kwargs):
            return response({'sessions': [session()]} if kwargs['params']['cmd'] == 'get_activity' else {'pms_name': 'Test Plex', 'pms_version': '1.0'})
        get.side_effect = reply
        source = Path('app.py').read_text().replace('def init_db() -> None:', 'def init_db() -> None:\n    raise AssertionError("Live pages must not open SQLite")').replace('def get_disk_video_paths(folder: str) -> List[str]:', 'def get_disk_video_paths(folder: str) -> List[str]:\n    raise AssertionError("Media folder unavailable")')
        at = AppTest.from_string(source).run()
        self.assertFalse(at.exception)
        self.assertTrue(any(m.label == 'Active streams' and m.value == '1' for m in at.metric))
        at.radio[0].set_value('Now Playing').run()
        self.assertFalse(at.exception)
        self.assertTrue(any(s.value == 'Movie' for s in at.subheader))
        self.assertEqual(get.call_count, 2)
        get.side_effect = lambda *a, **k: response({'sessions': []} if k['params']['cmd'] == 'get_activity' else {})
        at.button[0].click().run()
        self.assertTrue(any('Nothing is currently playing' in x.value for x in at.info))
        get.side_effect = requests.Timeout('test-only')
        at.button[0].click().run()
        self.assertFalse(at.exception)
        self.assertTrue(any('offline / degraded' in x.value for x in at.warning))
        disk.assert_not_called()

    @patch('tautulli_client.requests.get', side_effect=AssertionError('Maintenance must not fetch live data'))
    def test_maintenance_loads_without_network(self, get):
        with tempfile.TemporaryDirectory() as folder:
            # AppTest executes a fresh app module, so replace its APP_DIR-derived database constant in test source.
            source = Path('app.py').read_text().replace('DB_PATH = os.path.join(APP_DIR, "scan_history.db")', f'DB_PATH = {str(Path(folder) / "state.db")!r}')
            at = AppTest.from_string(source)
            at.session_state['live_dashboard'] = {}
            # Start directly in Maintenance via the navigation widget's key.
            source = source.replace('st.radio("Navigation", ["Overview", "Now Playing", "Maintenance"])', 'st.radio("Navigation", ["Overview", "Now Playing", "Maintenance"], index=2)')
            at = AppTest.from_string(source).run()
            self.assertFalse(at.exception)
            self.assertTrue(any(t.value == 'Maintenance' for t in at.title))
            get.assert_not_called()


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = patch.object(app, 'DB_PATH', str(Path(self.temp.name) / 'test.db'))
        self.db.start()
        self.addCleanup(self.db.stop)
        app.init_db()

    def test_upsert_keep_and_queue(self):
        df = pd.DataFrame([dict(RatingKey='1', Title='Movie', FilePath='Z:\\Movie.mkv', TotalPlays=0, ServerStatus='Unwatched', Status='Keep')])
        app.upsert_media_state(df)
        app.upsert_media_state(df)
        loaded, metrics, queue = app.load_scan_result(1)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded.iloc[0]['Status'], 'Keep')
        self.assertEqual(queue, [])
        self.assertEqual(app.build_delete_queue_from_df(loaded), [])
        self.assertTrue(app.add_override_column(loaded).iloc[0]['Override'])
        scan = df.copy()
        scan['Status'] = 'Queued for delete'
        app.upsert_media_state(scan)
        loaded, metrics, queue = app.load_scan_result(1)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(len(queue), 1)

    @patch('app.st.progress', return_value=Mock())
    @patch('app.st.empty', return_value=Mock())
    @patch('app.tautulli_get')
    def test_scan_preview_disk_only_and_played(self, get, empty, progress):
        folder = Path(self.temp.name)
        watched, unwatched, orphan = [folder / name for name in ('a.mkv', 'b.mp4', 'c.avi')]
        for path in (watched, unwatched, orphan):
            path.write_bytes(b'test')
        def api(base, key, cmd, **params):
            if cmd == 'get_library_media_info':
                return {'data': [{'rating_key': '1'}, {'rating_key': '2'}]}
            if cmd == 'get_metadata':
                return {'title': params['rating_key'], 'file': str(watched if params['rating_key'] == '1' else unwatched)}
            return {'data': [{}] if params['rating_key'] == '1' else []}
        get.side_effect = api
        df, metrics, queue = app.scan_library('host', 'key', '1', str(folder), True, True)
        self.assertEqual(metrics.played_media, 1)
        self.assertEqual(metrics.unwatched_media, 1)
        self.assertEqual(metrics.disk_only_media, 1)
        self.assertEqual(len(queue), 1)
        self.assertTrue(unwatched.exists())
        self.assertTrue(orphan.exists())
        updated, deleted, failed, actions = app.delete_queued_files(df, queue)
        self.assertEqual((deleted, failed), (1, 0))
        self.assertFalse(unwatched.exists())
        self.assertTrue(orphan.exists())
        self.assertTrue(watched.exists())


if __name__ == '__main__':
    unittest.main()
