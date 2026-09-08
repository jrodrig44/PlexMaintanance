"""Bandwidth page rendering using the shared live and historical pipelines."""
import pandas as pd
import streamlit as st
from dashboard import read_live, display
from tautulli_client import normalize_base_url, TautulliError
from playback import format_bandwidth, bandwidth_mbps
from bandwidth_analytics import (value, is_high, sorted_sessions, current_summary,
                                 history_summary, bandwidth_rankings, bandwidth_days)


def cards(items):
    for offset in range(0, len(items), 3):
        for col, (label, val) in zip(st.columns(3), items[offset:offset + 3]):
            col.metric(label, display(val))


def current_bandwidth(raw_url, api_key, refresh, threshold):
    st.subheader('Current bandwidth')
    st.caption('Plex Streaming Brain reserved/required bandwidth estimates, not measured network throughput. This snapshot is independent of historical filters.')
    try:
        url = normalize_base_url(raw_url)
        state = read_live(st.session_state.setdefault('live_dashboard', {}), url, api_key, refresh)
    except TautulliError as ex:
        st.warning('Current bandwidth unavailable / degraded: ' + str(ex))
        return
    st.caption('Last successful activity read: ' + state.get('success', 'Unavailable'))
    st.caption('Shared live cache: 30 seconds between reruns. Refresh fetches immediately; no background polling.')
    if state.get('error'):
        st.warning('Current bandwidth unavailable / degraded: ' + state['error'])
        st.info('Previous current values are not shown after a failed refresh.')
        return
    rows = state['activity']['sessions']
    stats = current_summary(state['activity'])
    cards([(f'Current {name.lower()} bandwidth', format_bandwidth(val)) for name, val in stats['totals'].items()] +
          [('Current active streams', stats['active']), ('Current remote streams', stats['remote']),
           ('Current transcode streams', stats['transcodes'])])
    st.caption(' · '.join(f'{name}: {source}' for name, source in stats['sources'].items()))
    st.caption(f"Current session bandwidth coverage: {stats['known']}/{stats['active']}; known location: {stats['location_known']}/{stats['active']}. Unknown values are excluded, never assumed zero.")
    if not rows:
        st.info('No active streams.')
        return
    highest = stats['highest']
    st.write('Highest-bandwidth current session (known values): ' +
             (f"{display(highest.get('title'))} · {display(highest.get('user'))} · {display(highest.get('player'))} · {format_bandwidth(value(highest))}" if highest else 'Unavailable'))
    table, chart = [], []
    for index, row in enumerate(sorted_sessions(rows), 1):
        table.append({'Title': display(row.get('title')), 'User': display(row.get('user')),
                      'Player': display(row.get('player')), 'Platform': display(row.get('platform')),
                      'Location': display(row.get('location')), 'Playback': display(row.get('decision')),
                      'Bandwidth': format_bandwidth(value(row)), 'High bandwidth': display(is_high(row, threshold)),
                      'Quality': display(row.get('quality')), 'Resolution': display(row.get('resolution')),
                      'Transcode speed': f"{row['speed']:g}×" if row.get('speed') is not None else 'Unavailable'})
        if value(row) is not None:
            chart.append({'Session': f"{index}. {display(row.get('title'))} · {display(row.get('player'))}", 'Current Mbps': bandwidth_mbps(value(row))})
    st.dataframe(pd.DataFrame(table), hide_index=True, width='stretch')
    if chart:
        st.caption('Current bandwidth by session · known values, highest 20 shown')
        st.bar_chart(pd.DataFrame(chart[:20]).set_index('Session'))


def historical_bandwidth(rows, zone, threshold):
    if not rows:
        st.info('No playback history in the loaded data matches these filters.')
        return
    stats = history_summary(rows, threshold)
    st.caption(f"Historical bandwidth coverage: {stats['known']}/{stats['total']} loaded matching playbacks; {stats['unknown']} missing or invalid.")
    if not stats['known']:
        st.info('Historical bandwidth unavailable: stock Tautulli get_history does not expose sufficient bandwidth detail. Current activity cannot supply historical trends. History, Devices and Transcoding provide supported playback counts and durations.')
        return
    st.caption('Conditional history detail: calculations use only supplied bandwidth fields in kbps. Means are per-playback averages, not time-weighted throughput; peaks are maximum known playback values, not simultaneous network peaks. No transferred-byte estimate is calculated.')
    cards([('Average known playback bandwidth', format_bandwidth(stats['mean'])),
           ('Median known playback bandwidth', format_bandwidth(stats['median'])),
           ('Peak known playback bandwidth', format_bandwidth(stats['peak'])),
           ('High-bandwidth playbacks (known)', stats['high'])])
    st.subheader('Local and remote · known bandwidth and location')
    st.caption(f"Joint location/bandwidth coverage: {stats['located']}/{stats['total']} loaded playbacks; {stats['known'] - stats['located']} bandwidth-known playbacks have unknown location. Remote percentage uses only the joint known subset.")
    cards([('Known local playbacks', stats['local_count']), ('Known remote playbacks', stats['remote_count']),
           ('Remote % of known sessions', f"{stats['remote_percent']:.1f}%" if stats['remote_percent'] is not None else None),
           ('Average local bandwidth', format_bandwidth(stats['local_mean'])),
           ('Average remote bandwidth', format_bandwidth(stats['remote_mean'])),
           ('Highest remote bandwidth', format_bandwidth(stats['remote_peak']))])
    highest = stats['remote_highest']
    if highest:
        st.write(f"Highest remote playback: {display(highest.get('title'))} · {display(highest.get('user'))} · {display(highest.get('player'))}")
    location = [{'Location': name, 'Average known Mbps': bandwidth_mbps(stats[field])}
                for name, field in [('Local', 'local_mean'), ('Remote', 'remote_mean')] if stats[field] is not None]
    if location:
        st.bar_chart(pd.DataFrame(location).set_index('Location'))
    st.subheader('Bandwidth rankings · known playback values')
    dimension = st.selectbox('Bandwidth ranking', ['Users', 'Devices / groups', 'Titles'])
    order = st.selectbox('Rank by', ['Average bandwidth', 'Peak bandwidth', 'High-bandwidth count'])
    key, label = {'Users': ('user_key', 'user'), 'Devices / groups': ('device_key', 'device_name'),
                  'Titles': ('title_key', 'title')}[dimension]
    field = {'Average bandwidth': 'mean', 'Peak bandwidth': 'peak', 'High-bandwidth count': 'high'}[order]
    groups = bandwidth_rankings(rows, key, label, threshold, field)
    st.caption(f"Identified {dimension.lower()} coverage: {sum(r['known'] for r in groups)}/{stats['known']} bandwidth-known playbacks. Rankings exclude unidentified records.")
    if dimension == 'Devices / groups':
        st.caption('Device grouping matches the Devices page. Player-label and platform/product groups may combine multiple physical devices. Remote and transcode means use their own known subsets.')
    table = []
    for group in groups:
        record = {dimension: group['name'], 'Known / loaded': f"{group['known']}/{group['total']}",
                  'Average bandwidth': format_bandwidth(group['mean']), 'Peak bandwidth': format_bandwidth(group['peak']),
                  'High-bandwidth count': group['high']}
        if dimension == 'Devices / groups':
            record.update({'Identity scope': group['scope'], 'Known bandwidth + location': group['located'],
                           'Remote samples': group['remote_count'], 'Remote average': format_bandwidth(group['remote_mean']),
                           'Known bandwidth + decision': group['decision_known'],
                           'Transcode samples': group['transcode_count'], 'Transcode average': format_bandwidth(group['transcode_mean'])})
        table.append(record)
    if table:
        st.dataframe(pd.DataFrame(table), hide_index=True, width='stretch')
        column = 'High-bandwidth count' if field == 'high' else order + ' (Mbps)'
        st.bar_chart(pd.DataFrame([{dimension: f"{i + 1}. {r['name']}", column: r[field] if field == 'high' else bandwidth_mbps(r[field])}
                                  for i, r in enumerate(groups[:10])]).set_index(dimension))
    else:
        st.info('No identified groups with known bandwidth.')
    daily = bandwidth_days(rows, zone, threshold)
    if daily:
        st.subheader('Daily bandwidth · known playback values')
        st.caption('Only days containing loaded playback rows appear. Missing bandwidth remains unavailable. Lines are not used to imply continuous sampling.')
        frame = pd.DataFrame([{'Date': r['Date'], 'Average Mbps': bandwidth_mbps(r['mean']),
                               'Remote average Mbps': bandwidth_mbps(r['remote_mean']),
                               'High-bandwidth count': r['high'], 'Known bandwidth': r['known'],
                               'Known remote bandwidth': r['remote_count'], 'Loaded playbacks': r['total']} for r in daily]).set_index('Date')
        st.bar_chart(frame[['Average Mbps', 'Remote average Mbps']])
        st.bar_chart(frame[['High-bandwidth count']])
        st.dataframe(frame, width='stretch')
