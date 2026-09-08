"""Pure bandwidth calculations over normalized, public playback records.

All rates are decimal kbps. History means/peaks describe known playback values,
not simultaneous traffic, measured throughput, or transferred bytes.
"""
from collections import defaultdict
from statistics import fmean, median
from zoneinfo import ZoneInfo
from playback import normalize_bandwidth, bandwidth_mbps


def value(row):
    # The old live alias keeps an existing 30-second cache compatible on reload.
    return normalize_bandwidth(row.get('bandwidth_kbps', row.get('bandwidth')))


def is_high(row, threshold_mbps):
    mbps = bandwidth_mbps(value(row))
    return None if mbps is None else mbps > threshold_mbps


def sorted_sessions(rows):
    return sorted(rows, key=lambda r: (value(r) is None, -(value(r) or 0)))


def complete_sum(rows):
    values = [value(r) for r in rows]
    return sum(values) if all(v is not None for v in values) else None


def current_summary(activity):
    rows = activity['sessions']
    locations_known = all(r.get('location') in ('Local', 'Remote') for r in rows)
    totals, sources = {}, {}
    for name, location in (('Total', None), ('Local', 'Local'), ('Remote', 'Remote')):
        aggregate = normalize_bandwidth(activity['metrics'].get(name + ' bandwidth'))
        if aggregate is not None:
            totals[name], sources[name] = aggregate, 'Tautulli aggregate'
        else:
            relevant = rows if location is None else [r for r in rows if r.get('location') == location]
            totals[name] = complete_sum(relevant) if location is None or locations_known else None
            sources[name] = 'Complete session sum' if totals[name] is not None else 'Unavailable'
    known = [r for r in sorted_sessions(rows) if value(r) is not None]
    return {'totals': totals, 'sources': sources, 'active': len(rows), 'known': len(known),
            'location_known': sum(r.get('location') in ('Local', 'Remote') for r in rows),
            'remote': sum(r.get('location') == 'Remote' for r in rows) if locations_known else None,
            'transcodes': sum(r.get('decision') == 'Transcode' for r in rows)
            if all(r.get('decision') in ('Direct Play', 'Direct Stream', 'Transcode') for r in rows) else None,
            'highest': known[0] if known else None}


def history_summary(rows, threshold_mbps=20):
    known = [r for r in rows if value(r) is not None]
    values = [value(r) for r in known]
    local = [r for r in known if r.get('location') == 'Local']
    remote = [r for r in known if r.get('location') == 'Remote']
    transcodes = [r for r in known if r.get('decision') == 'Transcode']
    located = len(local) + len(remote)
    return {'total': len(rows), 'known': len(known), 'unknown': len(rows) - len(known),
            'mean': fmean(values) if values else None, 'median': median(values) if values else None,
            'peak': max(values) if values else None,
            'high': sum(is_high(r, threshold_mbps) for r in known) if known else None,
            'local_count': len(local), 'remote_count': len(remote), 'located': located,
            'local_mean': fmean(value(r) for r in local) if local else None,
            'remote_mean': fmean(value(r) for r in remote) if remote else None,
            'remote_peak': max((value(r) for r in remote), default=None),
            'remote_highest': max(remote, key=value) if remote else None,
            'remote_percent': len(remote) / located * 100 if located else None,
            'decision_known': sum(r.get('decision') in ('Direct Play', 'Direct Stream', 'Transcode') for r in known),
            'transcode_count': len(transcodes),
            'transcode_mean': fmean(value(r) for r in transcodes) if transcodes else None}


def bandwidth_rankings(rows, key, label, threshold_mbps=20, order='mean'):
    groups = defaultdict(list)
    for row in rows:
        if row.get(key):
            groups[row[key]].append(row)
    records = []
    for identity, members in groups.items():
        stats = history_summary(members, threshold_mbps)
        if not stats['known']:
            continue
        names = sorted({r.get(label) or 'Unavailable' for r in members})
        records.append({**stats, 'name': names[0], 'key': identity,
                        'scope': members[0].get('device_scope')})
    return sorted(records, key=lambda r: (-r[order], r['name'].casefold(), r['key']))


def bandwidth_days(rows, zone, threshold_mbps=20):
    groups = defaultdict(list)
    for row in rows:
        if row.get('started') is not None:
            groups[row['started'].astimezone(ZoneInfo(zone)).date()].append(row)
    return [{'Date': day, **history_summary(members, threshold_mbps)} for day, members in sorted(groups.items())]
