"""Device and transcode calculations over normalized loaded history only."""
from collections import Counter, defaultdict
from analytics import DECISIONS, watch_totals, favorite, rankings
from playback import is_4k


def coverage(rows, field, values):
    counts = Counter(row.get(field) for row in rows if row.get(field) in values)
    known = sum(counts.values())
    return {'known': known, 'unknown': len(rows) - known,
            'counts': {v: counts[v] for v in values},
            'rates': {v: 100 * counts[v] / known if known else None for v in values}}


def decision_coverage(rows):
    return coverage(rows, 'decision', DECISIONS)


def location_coverage(rows):
    return coverage(rows, 'location', ('Local', 'Remote'))


def transcode_category(row):
    overall = row.get('decision')
    video, audio = row.get('video_decision'), row.get('audio_decision')
    if overall in ('Direct Play', 'Direct Stream'):
        # Contradictory component decisions cannot establish a trustworthy category.
        return 'Unknown' if 'Transcode' in (video, audio) else overall
    if overall != 'Transcode':
        return 'Unknown'
    passthrough = ('Direct Play', 'Direct Stream')
    if video == 'Transcode' and audio == 'Transcode':
        return 'Video + audio transcode'
    if video == 'Transcode' and audio in passthrough:
        return 'Video-only transcode'
    if audio == 'Transcode' and video in passthrough:
        return 'Audio-only transcode'
    return 'Unknown'


def aggregate_devices(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row.get('device_key')].append(row)
    result = []
    for key, group in groups.items():
        names = sorted({r.get('device_name') or 'Unknown device' for r in group})
        moments = [r['started'] for r in group if r['started'] is not None]
        result.append({'key': key, 'name': ' / '.join(names), 'scope': group[0].get('device_scope', 'Unknown'),
                       'plays': len(group), **watch_totals(group),
                       'last_activity': max(moments) if moments else None,
                       'platform': favorite(group, 'platform'), 'product': favorite(group, 'product'),
                       'top_content': favorite([dict(r, content=r['show'] or r['title']) for r in group], 'content'),
                       'resolution': favorite(group, 'source_resolution'),
                       'decisions': decision_coverage(group), 'locations': location_coverage(group)})
    return sorted(result, key=lambda r: (-r['plays'], r['name'], r['scope'], r['key'] or ''))


def device_summary(rows):
    devices = aggregate_devices(rows)
    return {'plays': len(rows), 'identified': sum(d['scope'] == 'Identified client' for d in devices),
            'label_groups': sum(d['scope'] in ('Player label group', 'Platform/product group') for d in devices),
            'identified_records': sum(r.get('device_scope') == 'Identified client' for r in rows),
            'unknown_records': sum(r.get('device_key') is None for r in rows),
            'platforms': len({r['platform'] for r in rows if r['platform'] is not None}),
            'unknown_platforms': sum(r['platform'] is None for r in rows),
            'decisions': decision_coverage(rows), 'locations': location_coverage(rows)}


def select_device(rows, key):
    return [r for r in rows if r.get('device_key') == key]


def transcode_summary(rows):
    transcodes = [r for r in rows if r['decision'] == 'Transcode']
    video = coverage(transcodes, 'video_decision', DECISIONS)
    categories = Counter(transcode_category(r) for r in transcodes)
    # Report known-subset counts only when at least one relevant field is known.
    four_k_known = [r for r in rows if is_4k(r.get('source_resolution')) is not None]
    four_k = [r for r in four_k_known if is_4k(r.get('source_resolution'))]
    four_decisions = decision_coverage(four_k)
    four_counts_available = bool(four_decisions['known'] or (four_k_known and not four_k))
    return {'plays': len(rows), 'decisions': decision_coverage(rows),
            'video': video, 'video_transcodes': video['counts']['Transcode'] if video['known'] else None,
            'categories': dict(categories), 'classified_transcodes': len(transcodes) - categories['Unknown'],
            'audio_only': categories['Audio-only transcode'] if len(transcodes) > categories['Unknown'] else None,
            'remote': location_coverage(transcodes),
            'resolution_known': len(four_k_known), 'resolution_unknown': len(rows) - len(four_k_known),
            'four_k': len(four_k) if four_k_known else None,
            'four_k_decisions': four_decisions,
            'four_k_transcode_count': four_decisions['counts']['Transcode'] if four_counts_available else None,
            'four_k_direct_play_count': four_decisions['counts']['Direct Play'] if four_counts_available else None,
            'four_k_transcodes': [r for r in four_k if r['decision'] == 'Transcode'],
            'transcodes': transcodes}


def transcode_rankings(rows):
    transcodes = [r for r in rows if r['decision'] == 'Transcode']
    return {'Devices / groups': aggregate_devices(transcodes),
            'Users': rankings(transcodes, 'user_key', 'user'),
            'Titles': rankings(transcodes), 'Shows': rankings(transcodes, 'show_key', 'show'),
            'Platforms': rankings(transcodes, 'platform', 'platform'),
            'Source resolutions': rankings(transcodes, 'source_resolution', 'source_resolution'),
            'Source video codecs': rankings(transcodes, 'video_codec', 'video_codec')}
