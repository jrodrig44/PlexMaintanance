"""Server credentials stay out of Streamlit widgets; sessions can override."""
import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from tests.test_phase1 import response


class CredentialTests(TestCase):
    @patch.dict(os.environ, {'TAUTULLI_API_KEY': 'synthetic-server-only'})
    @patch('tautulli_client.requests.get')
    def test_server_key_private_override_and_two_sessions(self, get):
        get.side_effect = lambda *a, **k: response({'sessions': []} if k['params']['cmd'] == 'get_activity' else {})
        for _ in range(2):
            at = AppTest.from_string(Path('app.py').read_text()).run(timeout=20)
            self.assertFalse(at.exception)
            self.assertTrue(any(c.value == 'API Key: Configured on server' for c in at.caption))
            self.assertEqual(get.call_args.kwargs['params']['apikey'], 'synthetic-server-only')
            self.assertFalse(any('Key' in t.label for t in at.text_input))
            self.assertNotIn('synthetic-server-only', repr(at._tree))
        at.checkbox[0].check().run()
        field = next(t for t in at.text_input if t.label == 'Temporary API Key')
        self.assertEqual(field.value, '')
        field.set_value('synthetic-override').run()
        self.assertEqual(get.call_args.kwargs['params']['apikey'], 'synthetic-override')
        at.checkbox[0].uncheck().run()
        self.assertEqual(get.call_args.kwargs['params']['apikey'], 'synthetic-server-only')

    @patch.dict(os.environ, {'TAUTULLI_API_KEY': ''})
    @patch('tautulli_client.requests.get')
    def test_no_server_key_temporary_fallback(self, get):
        get.side_effect = lambda *a, **k: response({'sessions': []} if k['params']['cmd'] == 'get_activity' else {})
        at = AppTest.from_string(Path('app.py').read_text()).run(timeout=20)
        self.assertFalse(at.exception)
        get.assert_not_called()
        field = next(t for t in at.text_input if t.label == 'API Key')
        self.assertEqual(field.value, '')
        self.assertTrue(any('device/session-specific' in c.value for c in at.caption))
        field.set_value('synthetic-session').run()
        self.assertEqual(get.call_args.kwargs['params']['apikey'], 'synthetic-session')
