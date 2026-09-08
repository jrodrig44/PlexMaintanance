"""Streamlit live pages, independent of maintenance storage and filesystem paths."""
import hashlib
import time
from datetime import datetime, timezone
import streamlit as st
from tautulli_client import TautulliError, get_activity, get_server_info, get_artwork, normalize_base_url


def read_live(state, base_url, api_key, refresh=False):
    identity = hashlib.sha256((base_url + '\0' + api_key).encode()).hexdigest()
    if state.get('identity') != identity:
        state.clear()
        state['identity'] = identity
    now = time.monotonic()
    if refresh or now - state.get('attempt', -float('inf')) >= 30:
        state['attempt'] = now
        try:
            state['activity'] = get_activity(base_url, api_key)
            state['success'] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            state['error'] = None
        except TautulliError as ex:
            state['error'] = str(ex)
        if refresh or now - state.get('server_attempt', -float('inf')) >= 300:
            state['server_attempt'] = now
            try:
                state['server'] = get_server_info(base_url, api_key)
                state['server_error'] = None
            except TautulliError as ex:
                state['server'] = {}
                state['server_error'] = str(ex)
    return state


def display(value):
    return 'Unavailable' if value is None or value == '' else str(value)


def bandwidth(value):
    return 'Unavailable' if value is None else f'{value / 1000:,.2f} Mbps'


def duration(value):
    if value is None:
        return 'Unavailable'
    seconds = int(value / 1000)
    return f'{seconds // 60}:{seconds % 60:02d}'


def render_live_dashboard(page, raw_url, api_key):
    st.title(page)
    refresh = st.button('Refresh', type='primary')
    try:
        base_url = normalize_base_url(raw_url)
    except TautulliError as ex:
        st.warning(str(ex))
        return
    state = st.session_state.setdefault('live_dashboard', {})
    read_live(state, base_url, api_key, refresh)
    st.caption('Last successful activity refresh: ' + state.get('success', 'Unavailable'))
    st.caption('Activity is reused for 30 seconds between reruns. Refresh fetches immediately; there is no background polling.')
    if state.get('error'):
        st.warning('Tautulli offline / degraded: ' + state['error'])
        st.info('Current activity is unavailable. Saved Maintenance data remains accessible.')
        return
    st.success('Tautulli connected')
    activity = state['activity']
    if page == 'Overview':
        server = state.get('server', {})
        st.write('Plex server: ' + display(server.get('name')))
        st.caption('Plex version: ' + display(server.get('version')))
        if state.get('server_error'):
            st.warning('Server information unavailable: ' + state['server_error'])
        items = list(activity['metrics'].items())
        for offset in range(0, len(items), 3):
            for column, (label, value) in zip(st.columns(3), items[offset:offset+3]):
                column.metric(label, bandwidth(value) if 'bandwidth' in label else display(value))
        return
    if not activity['sessions']:
        st.info('Nothing is currently playing.')
    show_artwork = st.toggle('Show artwork', value=False)
    for session in activity['sessions']:
        with st.container(border=True):
            if show_artwork:
                cache = state.setdefault('artwork', {})
                path = session['artwork']
                if path not in cache or time.monotonic() - cache[path][0] >= 600:
                    if len(cache) >= 50:
                        cache.clear()
                    cache[path] = (time.monotonic(), get_artwork(base_url, api_key, path))
                if cache[path][1]:
                    st.image(cache[path][1], width=120)
                else:
                    st.caption('Artwork unavailable')
            st.subheader(display(session['title']))
            if session['episode_title']:
                st.write(f"{display(session['show'])} · Season {display(session['season'])}, Episode {display(session['episode'])} · {session['episode_title']}")
            st.write(f"{display(session['user'])} · {display(session['player'])} · {display(session['platform'])} · {display(session['product'])}")
            st.write(f"{display(session['location'])} · {display(session['quality'])} · {display(session['resolution'])} · {display(session['decision'])}")
            st.caption(f"Video: {display(session['video'])} · Audio: {display(session['audio'])}")
            if session['progress'] is not None:
                st.progress(session['progress'] / 100, text=f"{session['progress']:.0f}%")
            else:
                st.caption('Progress: Unavailable')
            st.caption(f"{duration(session['elapsed'])} / {duration(session['duration'])} · Bandwidth: {bandwidth(session['bandwidth'])}")
            if session['speed'] is not None:
                st.caption(f"Transcode speed: {session['speed']:g}×")
