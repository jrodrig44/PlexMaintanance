"""History and Users pages. All calculations and API reads live outside rendering."""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import pandas as pd
import streamlit as st
from analytics import (PRESETS, HistoryQuery, date_range, filter_history, summary, aggregate_users,
                       aggregate_days, rankings, format_duration, format_datetime)
from analytics_service import read_history, read_users, connection_cache
from tautulli_client import TautulliError


def shown(value):
    return 'Unavailable' if value is None else value


def metric_cards(rows, detail=False):
    stats = summary(rows)
    values = [('Total plays (loaded)', stats['plays']), ('Total watch time', format_duration(stats['watch_seconds'])),
              ('Unique titles', shown(stats['titles'])), ('Unique users', shown(stats['users']))]
    if detail:
        values = values[:3] + [('Movie plays', shown(stats['movies'])), ('Episode plays', shown(stats['episodes']))]
    for offset in range(0, len(values), 3):
        for column, (label, value) in zip(st.columns(3), values[offset:offset + 3]):
            column.metric(label, value)
    if stats['missing_duration']:
        st.caption(f"Watch duration is missing or invalid for {stats['missing_duration']} play(s). Known subtotal: {format_duration(stats['known_watch_seconds'])}.")


def trend(rows, zone):
    daily = aggregate_days(rows, zone)
    if daily:
        frame = pd.DataFrame(daily).set_index('Date')
        left, right = st.columns(2)
        with left:
            st.caption('Plays over time · loaded history')
            st.bar_chart(frame[['Plays']])
        with right:
            st.caption('Watch time over time · known durations only')
            if frame['Watch minutes (known)'].notna().any():
                st.bar_chart(frame[['Watch minutes (known)']])
            else:
                st.info('Watch time unavailable.')


def history_table(rows, zone):
    if not rows:
        st.info('No playback history in the loaded data matches these filters.')
        return
    records = []
    for row in sorted(rows, key=lambda r: r['started'].timestamp() if r['started'] else -1, reverse=True):
        records.append({'Started': format_datetime(row['started'], zone), 'User': row['user'],
                        'Title': shown(row['title']), 'Show': shown(row['show']),
                        'Season': shown(row['season']), 'Episode': shown(row['episode']),
                        'Episode title': shown(row['episode_title']), 'Media type': shown(row['media_type']),
                        'Player': shown(row['player']), 'Platform': shown(row['platform']),
                        'Location': shown(row['location']), 'Watched duration': format_duration(row['watch_seconds']),
                        'Completion': 'Unavailable' if row['completion'] is None else f"{row['completion']:g}%",
                        'Playback': shown(row['decision'])})
    st.dataframe(pd.DataFrame(records), hide_index=True, width='stretch', height=420)


def ranking_chart(rows, label, key='title_key', name='title'):
    values = rankings(rows, key, name)[:10]
    st.caption(label + ' · by plays in loaded history')
    if values:
        # Keep separate identities even if their display titles match.
        frame = pd.DataFrame([{'Content': f"{i + 1}. {r['name']}", 'Plays': r['plays']} for i, r in enumerate(values)])
        st.bar_chart(frame.set_index('Content'))
    else:
        st.info('Unavailable: no identified content in the loaded history.')


def filters(page, zone, users):
    today = datetime.now(ZoneInfo(zone)).date()
    names = {u['key']: f"{u['name']} ({u['user_id'] or u['username'] or 'unknown'})" for u in users if u['key']}
    with st.form('analytics_filters_' + page):
        preset = st.selectbox('Date range', PRESETS, index=1)
        left, right = st.columns(2)
        custom_start = left.date_input('Custom start', today - timedelta(days=29))
        custom_end = right.date_input('Custom end', today)
        st.caption('Custom dates apply only when Date range is Custom.')
        selected_user, media, search = None, None, ''
        if page in ('History', 'Devices', 'Transcoding'):
            selected_user = st.selectbox('User', [None, *names], format_func=lambda k: 'All users' if k is None else names[k])
            media = st.selectbox('Media type', [None, 'movie', 'episode', 'track', 'live'], format_func=lambda k: k or 'All media')
            search = st.text_input('Search title')
        limit = st.selectbox('Maximum rows to load', [500, 1000, 2000], index=1)
        st.form_submit_button('Apply filters')
    start, end = date_range(preset, today, custom_start, custom_end)
    return HistoryQuery(start, end, zone, selected_user, media, search, limit)


def users_page(rows, users, zone):
    metric_cards(rows)
    aggregates = aggregate_users(rows, users)
    st.subheader('Users · selected range, loaded history')
    st.caption('Zero plays means no matching plays in the loaded rows. It does not prove inactivity when the dataset is limited. Last activity is from this range, not lifetime activity.')
    table = [{'Username': shown(u['username']), 'Display name': u['name'], 'Status': shown(u['active']),
              'Last activity (loaded)': format_datetime(u['last_activity'], zone), 'Plays (loaded)': u['plays'],
              'Watch time': format_duration(u['watch_seconds']), 'Top content (plays)': shown(u['top_content']),
              'Most-used platform': shown(u['platform']), 'Most-used player': shown(u['player']),
              'Local plays': shown(u['local']), 'Remote plays': shown(u['remote'])} for u in aggregates]
    if not table:
        st.info('No users or viewing history are available.')
        return
    st.dataframe(pd.DataFrame(table), hide_index=True, width='stretch', height=320)
    ranking = sorted([u for u in aggregates if u['known_watch_seconds'] is not None and u['plays']],
                     key=lambda u: u['known_watch_seconds'], reverse=True)[:10]
    if ranking:
        st.caption('Top users by watch time · known durations in loaded history')
        st.bar_chart(pd.DataFrame([{'User': f"{u['name']} ({u['user_id'] or u['username'] or 'unknown'})",
                                    'Minutes': u['known_watch_seconds'] / 60} for u in ranking]).set_index('User'))
    selected = st.selectbox('User details', range(len(aggregates)),
                            format_func=lambda i: f"{aggregates[i]['name']} ({aggregates[i]['user_id'] or aggregates[i]['username'] or 'unknown'})")
    user = aggregates[selected]
    detail = [r for r in rows if r['user_key'] == user['key']]
    st.subheader('User details: ' + user['name'])
    metric_cards(detail, detail=True)
    st.write('Most-used platform: ' + str(shown(user['platform'])) + ' · Player: ' + str(shown(user['player'])))
    for column, (label, count) in zip(st.columns(3), summary(detail)['decisions'].items()):
        column.metric(label, shown(count))
    trend(detail, zone)
    ranking_chart(detail, 'Top watched content')
    left, right = st.columns(2)
    with left:
        ranking_chart([r for r in detail if r['media_type'] == 'episode'], 'Top shows', 'show_key', 'show')
    with right:
        ranking_chart([r for r in detail if r['media_type'] == 'movie'], 'Top movies')
    st.subheader('Recent user activity')
    history_table(detail, zone)


def render_analytics(page, raw_url, api_key):
    st.title(page)
    zone = os.environ.get('PLEX_DASHBOARD_TIMEZONE', 'America/New_York')
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        st.error('Invalid PLEX_DASHBOARD_TIMEZONE. Set an IANA timezone such as America/New_York or UTC.')
        return
    st.caption(f'Dates and day boundaries: {zone}. All metrics and charts describe loaded history only.')
    refresh = st.button('Refresh', type='primary')
    state = st.session_state.setdefault('analytics_cache', {})
    try:
        url, entries = connection_cache(state, raw_url, api_key)
        user_result = read_users(state, url, api_key, refresh)
        users = user_result.get('data', [])
        if user_result.get('error'):
            st.warning('User directory unavailable: ' + user_result['error'])
        # Include deleted/absent directory users from previously loaded history.
        historical_users = {}
        for key, entry in entries.items():
            if key[0] == 'history' and 'data' in entry:
                for row in entry['data']['rows']:
                    historical_users[row['user_key']] = {'key': row['user_key'], 'user_id': row['user_id'],
                                                         'username': row['username'], 'name': row['user'], 'active': None, 'last_seen': None}
        filter_users = list({**historical_users, **{u['key']: u for u in users}}.values())
        query = filters(page, zone, filter_users)
        result = read_history(state, url, api_key, query, refresh)
    except (TautulliError, ValueError, OverflowError) as ex:
        message = str(ex) if isinstance(ex, TautulliError) else 'Invalid date range. Check the custom start and end dates.'
        st.warning(message)
        return
    if result.get('error'):
        st.warning('History unavailable / degraded: ' + result['error'])
        st.info('Previous results are not shown after a failed refresh. Other pages remain available.')
        return
    st.caption('Last successful history read: ' + format_datetime(result['success'], zone))
    data = result['data']
    if data['limited']:
        available = str(data['available']) if data['available'] is not None else 'an unknown number of'
        st.warning(f"Limited dataset: received {data['returned']} of {available} candidate records. Narrow the range/filter or increase the row limit. These are not full-range totals.")
    if data['duplicates']:
        st.warning(f"Removed {data['duplicates']} duplicate history rows from the response.")
    invalid_dates = sum(row['started'] is None for row in data['rows'])
    if invalid_dates:
        st.warning(f'{invalid_dates} record(s) have no valid timestamp. They are excluded from date-filtered results and daily charts.')
    rows = filter_history(data['rows'], query)
    st.caption(f"{len(rows)} matching playback records from {data['returned']} returned candidates. Tautulli date filters are widened before exact local-time filtering. Cache: 3 minutes; Refresh bypasses it.")
    if page == 'History':
        metric_cards(rows)
        trend(rows, zone)
        st.subheader('Recent playback')
        history_table(rows, zone)
    elif page == 'Users':
        users_page(rows, users, zone)
    else:
        from device_pages import devices_page, transcoding_page
        if page == 'Devices':
            devices_page(rows, zone)
        else:
            transcoding_page(rows, zone)
