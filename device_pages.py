"""Devices and Transcoding views using the existing history page data pipeline."""
import pandas as pd
import streamlit as st
from analytics import summary, format_duration, format_datetime, rankings
from analytics_pages import shown, trend, history_table
from device_analytics import (aggregate_devices, device_summary, select_device, decision_coverage,
                              location_coverage, transcode_summary, transcode_rankings, transcode_category)


def rate(value):
    return 'Unavailable' if value is None else f'{value:.1f}%'


def cards(values):
    for start in range(0, len(values), 3):
        for column, (label, value) in zip(st.columns(3), values[start:start + 3]):
            column.metric(label, shown(value))


def decision_panel(rows):
    decisions = decision_coverage(rows)
    st.caption(f"Known playback decisions: {decisions['known']} of {len(rows)}; unknown: {decisions['unknown']}. Percentages use known decisions only.")
    cards([(name + ' (known)', count) for name, count in decisions['counts'].items()])
    cards([('Direct Play % of known', rate(decisions['rates']['Direct Play'])),
           ('Transcode % of known', rate(decisions['rates']['Transcode']))])


def location_panel(rows):
    locations = location_coverage(rows)
    st.caption(f"Known locations: {locations['known']} of {len(rows)}; unknown: {locations['unknown']}.")
    cards([('Local plays (known)', locations['counts']['Local']), ('Remote plays (known)', locations['counts']['Remote']),
           ('Remote % of known locations', rate(locations['rates']['Remote']))])


def ranking_table(rows, title, key='title_key', name='title'):
    st.caption(title + ' · loaded history')
    data = rankings(rows, key, name)[:10]
    if data:
        st.dataframe(pd.DataFrame([{'Name': r['name'], 'Plays': r['plays'],
                                    'Known watch time': format_duration(r['known_watch_seconds'])} for r in data]),
                     hide_index=True, width='stretch')
    else:
        st.info('Unavailable: no identified values in loaded history.')


def devices_page(rows, zone):
    stats = device_summary(rows)
    cards([('Playback records (loaded)', stats['plays']), ('Unique identified devices', stats['identified']),
           ('Unique known platforms', stats['platforms']),
           ('Direct Play % of known', rate(stats['decisions']['rates']['Direct Play'])),
           ('Transcode % of known', rate(stats['decisions']['rates']['Transcode'])),
           ('Remote % of known locations', rate(stats['locations']['rates']['Remote']))])
    st.caption(f"Device identity coverage: {stats['identified_records']} of {len(rows)} records have a client identifier; "
               f"{stats['label_groups']} fallback label group(s); {stats['unknown_records']} records have no device identity. "
               'Identified clients are not a count of physical hardware. Label groups may contain multiple devices.')
    st.caption(f"Decision coverage: {stats['decisions']['known']} known, {stats['decisions']['unknown']} unknown. "
               f"Location coverage: {stats['locations']['known']} known, {stats['locations']['unknown']} unknown. "
               f"Platform missing on {stats['unknown_platforms']} record(s). All rates use their own known subset.")
    if not rows:
        st.info('No device activity in the loaded history.')
        return
    devices = aggregate_devices(rows)
    st.subheader('Device and client groups')
    table = [{'Group': i + 1, 'Device / player': d['name'], 'Identity level': d['scope'],
              'Platform (most used)': shown(d['platform']), 'Product (most used)': shown(d['product']),
              'Plays': d['plays'], 'Known watch time': format_duration(d['known_watch_seconds']),
              'Last activity': format_datetime(d['last_activity'], zone), 'Most-watched content': shown(d['top_content']),
              'Source resolution (most common)': shown(d['resolution'])} for i, d in enumerate(devices)]
    st.dataframe(pd.DataFrame(table), hide_index=True, width='stretch', height=320)
    with st.expander('Playback breakdown by device / group'):
        counts = [{'Group': i + 1, 'Device / player': d['name'], 'Known decisions': d['decisions']['known'],
                   'Unknown decisions': d['decisions']['unknown'], **d['decisions']['counts'],
                   'Direct Play % of known': rate(d['decisions']['rates']['Direct Play']),
                   'Transcode % of known': rate(d['decisions']['rates']['Transcode']),
                   'Local (known)': d['locations']['counts']['Local'], 'Remote (known)': d['locations']['counts']['Remote'],
                   'Unknown location': d['locations']['unknown']} for i, d in enumerate(devices)]
        st.dataframe(pd.DataFrame(counts), hide_index=True, width='stretch')
    chart_metric = st.selectbox('Device chart', ['Plays', 'Known watch minutes'])
    ranked = devices if chart_metric == 'Plays' else sorted(devices, key=lambda d: (-(d['known_watch_seconds'] or 0), d['name'], d['key'] or ''))
    chart = [{'Device / group': f"{i + 1}. {d['name']}", chart_metric:
              d['plays'] if chart_metric == 'Plays' else d['known_watch_seconds'] / 60}
             for i, d in enumerate(ranked[:10]) if chart_metric == 'Plays' or d['known_watch_seconds'] is not None]
    left, right = st.columns(2)
    with left:
        st.caption('Top devices / groups · loaded history')
        if chart:
            st.bar_chart(pd.DataFrame(chart).set_index('Device / group'))
        else:
            st.info('Watch duration unavailable.')
    with right:
        st.caption('Platform usage · known platforms in loaded history')
        platforms = rankings(rows, 'platform', 'platform')[:10]
        if platforms:
            st.bar_chart(pd.DataFrame([{'Platform': r['name'], 'Plays': r['plays']} for r in platforms]).set_index('Platform'))
        else:
            st.info('Platforms unavailable.')
    selected = st.selectbox('Device / group details', range(len(devices)),
                            format_func=lambda i: f"Group {i + 1}: {devices[i]['name']} · {devices[i]['scope']}")
    device = devices[selected]
    detail = select_device(rows, device['key'])
    stats = summary(detail)
    st.subheader('Device / group details: ' + device['name'])
    st.caption(device['scope'] + ' · this drilldown uses the identity level shown, not an assumed physical device.')
    cards([('Plays (loaded)', len(detail)), ('Known watch time', format_duration(stats['known_watch_seconds'])),
           ('Unique users', stats['users']), ('Unique titles', stats['titles'])])
    st.caption(f"Missing watch duration: {stats['missing_duration']} record(s).")
    decision_panel(detail)
    location_panel(detail)
    trend(detail, zone)
    content = st.selectbox('Device ranking', ['Top users', 'Top content', 'Top shows', 'Top movies'])
    if content == 'Top users':
        ranking_table(detail, content, 'user_key', 'user')
    elif content == 'Top shows':
        ranking_table([r for r in detail if r['media_type'] == 'episode'], content, 'show_key', 'show')
    else:
        ranking_table([r for r in detail if r['media_type'] == 'movie'] if content == 'Top movies' else detail, content)
    st.subheader('Recent device / group activity')
    history_table(detail, zone)


def transcode_table(rows, zone):
    if not rows:
        st.info('No matching transcodes in loaded history.')
        return
    optional = {'source_resolution': 'Source resolution', 'stream_resolution': 'Stream resolution',
                'video_decision': 'Video decision', 'audio_decision': 'Audio decision', 'subtitle_decision': 'Subtitle decision',
                'video_codec': 'Source video codec', 'audio_codec': 'Source audio codec', 'container': 'Source container',
                'stream_video_codec': 'Stream video codec', 'stream_audio_codec': 'Stream audio codec', 'stream_container': 'Stream container'}
    present = {key: label for key, label in optional.items() if any(r.get(key) is not None for r in rows)}
    records = []
    for r in sorted(rows, key=lambda r: r['started'].timestamp() if r['started'] else -1, reverse=True):
        title = r['title'] or 'Unavailable'
        if r['media_type'] == 'episode':
            title += f" · {r['show'] or 'Unknown show'} S{r['season'] or '?'} E{r['episode'] or '?'}"
        records.append({'Started': format_datetime(r['started'], zone), 'User': r['user'], 'Title / episode': title,
                        'Device / player': r['device_name'], 'Identity level': r['device_scope'],
                        'Platform': shown(r['platform']), 'Location': shown(r['location']), 'Playback': r['decision'],
                        'Category': transcode_category(r), **{label: shown(r.get(key)) for key, label in present.items()}})
    st.dataframe(pd.DataFrame(records), hide_index=True, width='stretch', height=360)


def transcoding_page(rows, zone):
    stats = transcode_summary(rows)
    cards([('Playback records (loaded)', len(rows))])
    decision_panel(rows)
    transcodes = stats['transcodes']
    remote = stats['remote']
    four = stats['four_k_decisions']
    cards([('Video transcodes (known subset)', stats['video_transcodes']), ('Audio-only (classified subset)', stats['audio_only']),
           ('Remote transcodes (known locations)', remote['counts']['Remote'] if remote['known'] else None)])
    st.caption(f"Within {len(transcodes)} known transcodes: video decision known on {stats['video']['known']}, "
               f"unknown on {stats['video']['unknown']}; audio/video category established on {stats['classified_transcodes']}; "
               f"location known on {remote['known']}, unknown on {remote['unknown']}.")
    st.caption('Standard Tautulli history omits detailed video/audio decisions, source resolution and codecs. '
               'These metrics remain unavailable unless those fields are explicitly present. No per-playback detail requests or inferred transcode reasons are used.')
    if not rows:
        st.info('No playback history available for transcoding analytics.')
        return
    left, right = st.columns(2)
    with left:
        st.caption('Playback decisions · loaded history')
        st.bar_chart(pd.DataFrame([{'Decision': label, 'Plays': count} for label, count in
                                  {**stats['decisions']['counts'], 'Unknown': stats['decisions']['unknown']}.items()]).set_index('Decision'))
    with right:
        st.caption('Categories within known transcodes · loaded history')
        if stats['categories']:
            st.bar_chart(pd.DataFrame([{'Category': k, 'Plays': v} for k, v in sorted(stats['categories'].items())]).set_index('Category'))
        else:
            st.info('No known transcodes in loaded history.')
    st.subheader('4K source playback · loaded history')
    cards([('4K playback (known sources)', stats['four_k']),
           ('4K transcodes (known decisions)', stats['four_k_transcode_count']),
           ('4K Direct Play (known decisions)', stats['four_k_direct_play_count'])])
    st.caption(f"Source resolution classifiable on {stats['resolution_known']} of {len(rows)} records; "
               f"unknown on {stats['resolution_unknown']}. Among known 4K records: {four['known']} known decisions, {four['unknown']} unknown. "
               '4K requires an explicit 4K/UHD/2160 source value; stream resolution and bitrate are not substitutes.')
    location_panel(transcodes)
    choices = transcode_rankings(rows)
    choice = st.selectbox('Transcode ranking', list(choices))
    values = choices[choice][:10]
    st.caption('Most transcodes by ' + choice.lower() + ' · known values in loaded history')
    if values:
        st.dataframe(pd.DataFrame([{'Name': r['name'], **({'Identity level': r['scope']} if 'scope' in r else {}),
                                    'Transcodes': r['plays'], 'Known watch time': format_duration(r['known_watch_seconds'])}
                                   for r in values]), hide_index=True, width='stretch')
    else:
        st.info('Unavailable: no identified values for this ranking.')
    st.subheader('Recent transcodes')
    transcode_table(transcodes, zone)
    with st.expander('Recent 4K transcodes'):
        if stats['four_k'] is None:
            st.info('Source resolution unavailable; 4K transcodes cannot be identified.')
        else:
            transcode_table(stats['four_k_transcodes'], zone)
