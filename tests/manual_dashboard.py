"""Local visual QA harness: streamlit run tests/manual_dashboard.py."""
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import streamlit as st
import requests
import app

app.DEFAULT_API_KEY = 'mock-only'
app.DB_PATH = str(Path(tempfile.gettempdir()) / 'plex-phase1-visual-qa.db')
mode = st.sidebar.selectbox('Mock scenario', ['Active', 'Empty', 'Offline', 'Detailed synthetic', 'Bandwidth synthetic'])
if st.session_state.get('mock_scenario') != mode:
    st.session_state.pop('live_dashboard', None)
    st.session_state.pop('analytics_cache', None)
    st.session_state['mock_scenario'] = mode

def mock_get(*args, **kwargs):
    if mode == 'Offline':
        raise requests.Timeout('Mock timeout')
    command = kwargs['params']['cmd']
    data = {'pms_name': 'LAN Plex', 'pms_version': '1.40.0 (mock)'}
    if command == 'get_activity':
        sessions = [] if mode == 'Empty' else [dict(full_title='Example Show - Episode One', title='Episode One', grandparent_title='Example Show', media_type='episode', parent_media_index='1', media_index='1', user_id='1', friendly_name='Sample viewer', player='Living room TV', platform='Roku', product='Plex', local='1', quality_profile='Original', transcode_decision='direct play', stream_video_decision='direct play', stream_audio_decision='direct play', view_offset='900000', duration='2700000', bandwidth='8000')]
        if sessions and mode == 'Bandwidth synthetic':
            sessions.append(dict(title='Remote Movie', friendly_name='Sample guest', user_id='2', player='Browser', platform='Chrome',
                                 local='0', bandwidth=25000, transcode_decision='transcode', transcode_speed=2.5, quality_profile='Original'))
        remote_bandwidth = 25000 if mode == 'Bandwidth synthetic' else 0
        data = {'sessions': sessions, 'total_bandwidth': (8000 if sessions else 0) + remote_bandwidth,
                'lan_bandwidth': 8000 if sessions else 0, 'wan_bandwidth': remote_bandwidth}
    if command == 'get_users':
        data = [{'user_id': 1, 'username': 'sample', 'friendly_name': 'Sample viewer', 'is_active': 1},
                {'user_id': 2, 'username': 'guest', 'friendly_name': 'Sample guest', 'is_active': 0}]
    if command == 'get_history':
        now = int(datetime.now(timezone.utc).timestamp())
        history = [] if mode == 'Empty' else [
            dict(row_id=1, user_id=1, user='sample', friendly_name='Sample viewer', started=now - 3600,
                 stopped=now - 1800, play_duration=1800, full_title='Example Show - Episode One', title='Episode One',
                 media_type='episode', grandparent_title='Example Show', grandparent_rating_key=100, rating_key=101,
                 parent_media_index=1, media_index=1, platform='Roku', player='Living room TV', location='lan',
                 transcode_decision='direct play', percent_complete=67),
            dict(row_id=2, user_id=2, user='guest', friendly_name='Sample guest', started=now - 86400,
                 stopped=now - 84600, play_duration=1800, full_title='Example Movie', title='Example Movie',
                 media_type='movie', rating_key=102, platform='Chrome', player='Browser', location='wan',
                 transcode_decision='transcode', percent_complete=33),
        ]
        if history:
            history[0].update(machine_id='mock-client-one', product='Plex')
            history[1].update(machine_id='mock-client-two', product='Plex Web')
            history.extend([dict(history[0], row_id=3, transcode_decision='copy'),
                            dict(history[1], row_id=4, transcode_decision=None)])
        if mode == 'Bandwidth synthetic':
            # Conditional bandwidth QA only; stock get_history has no bandwidth.
            for item, sample in zip(history, [8000, 25000, 20000, None]):
                item['bandwidth'] = sample
        if mode == 'Detailed synthetic':
            # Conditional-detail QA only: standard get_history omits these fields.
            for item in history:
                item.update(video_resolution='2160', stream_video_resolution='1080',
                            video_codec='hevc', audio_codec='aac', container='mkv',
                            video_decision='transcode' if item['transcode_decision'] == 'transcode' else 'copy',
                            audio_decision='copy')
        data = {'data': history, 'recordsFiltered': len(history), 'recordsTotal': len(history)}
    return Mock(status_code=200, json=Mock(return_value={'response': {'result': 'success', 'data': data}}))

with patch('tautulli_client.requests.get', side_effect=mock_get):
    app.main()
