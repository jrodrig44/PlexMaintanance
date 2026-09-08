"""Local visual QA harness: streamlit run tests/manual_dashboard.py."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import streamlit as st
import requests
import app

app.DEFAULT_API_KEY = 'mock-only'
app.DB_PATH = str(Path(tempfile.gettempdir()) / 'plex-phase1-visual-qa.db')
mode = st.sidebar.selectbox('Mock scenario', ['Active', 'Empty', 'Offline'])

def mock_get(*args, **kwargs):
    if mode == 'Offline':
        raise requests.Timeout('Mock timeout')
    command = kwargs['params']['cmd']
    data = {'pms_name': 'LAN Plex', 'pms_version': '1.40.0 (mock)'}
    if command == 'get_activity':
        sessions = [] if mode == 'Empty' else [dict(full_title='Example Show - Episode One', title='Episode One', grandparent_title='Example Show', media_type='episode', parent_media_index='1', media_index='1', user_id='1', friendly_name='Sample viewer', player='Living room TV', platform='Roku', product='Plex', local='1', quality_profile='Original', transcode_decision='direct play', stream_video_decision='direct play', stream_audio_decision='direct play', view_offset='900000', duration='2700000', bandwidth='8000')]
        data = {'sessions': sessions, 'total_bandwidth': 8000 if sessions else 0, 'lan_bandwidth': 8000 if sessions else 0, 'wan_bandwidth': 0}
    return Mock(status_code=200, json=Mock(return_value={'response': {'result': 'success', 'data': data}}))

with patch('tautulli_client.requests.get', side_effect=mock_get):
    app.main()
