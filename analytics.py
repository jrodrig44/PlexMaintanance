"""Pure historical normalization, filtering and aggregation. No filesystem or UI I/O."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from playback import normalize_decision, normalize_playback_fields
from tautulli_client import TautulliError, number as _number

PRESETS = ('Last 7 days', 'Last 30 days', 'Last 90 days', 'This year', 'All available', 'Custom')
DECISIONS = ('Direct Play', 'Direct Stream', 'Transcode')


def number(value):
    return None if isinstance(value, bool) else _number(value)


def text(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    return str(value).strip() or None


def timestamp(value):
    value = number(value)
    if value is None or value == 0:
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def identity(user_id, username):
    return 'id:' + user_id if user_id is not None else ('name:' + username if username else None)


def normalize_user(raw):
    user_id, username = text(raw.get('user_id')), text(raw.get('username'))
    return {'key': identity(user_id, username), 'user_id': user_id, 'username': username,
            'name': text(raw.get('friendly_name')) or username or (f'User {user_id}' if user_id else 'Unknown user'),
            'active': {'1': 'Active', '0': 'Inactive', 'true': 'Active', 'false': 'Inactive'}.get(str(raw.get('is_active')).lower()),
            'last_seen': timestamp(raw.get('last_seen'))}


def normalize_history_row(raw):
    def field(name):
        return text(raw.get(name))
    user_id, username = field('user_id'), field('user')
    title = field('full_title') or field('title')
    media_type = 'live' if str(raw.get('live')) == '1' else field('media_type')
    rating_key = field('rating_key')
    title_key = ('rating:' + rating_key if rating_key else field('guid'))
    if not title_key and title:
        title_key = '|'.join([media_type or '', title, field('year') or ''])
    complete = number(raw.get('percent_complete'))
    if complete is not None and complete > 100:
        complete = None
    # Both fields are seconds of recorded playback, with pauses already subtracted.
    watch = number(raw.get('play_duration') if 'play_duration' in raw else raw.get('duration'))
    return {
        **normalize_playback_fields(raw),
        'row_id': field('row_id'), 'started': timestamp(raw.get('started')) or timestamp(raw.get('date')),
        'stopped': timestamp(raw.get('stopped')), 'user_key': identity(user_id, username),
        'user_id': user_id, 'username': username,
        'user': field('friendly_name') or username or (f'User {user_id}' if user_id else 'Unknown user'),
        'title': title, 'title_key': title_key, 'show': field('grandparent_title'),
        'show_key': field('grandparent_rating_key') or field('grandparent_title'),
        'season': field('parent_media_index'), 'episode': field('media_index'),
        'episode_title': field('title') if media_type == 'episode' else None,
        'media_type': media_type, 'player': field('player'), 'platform': field('platform'),
        'location': {'lan': 'Local', 'wan': 'Remote'}.get(field('location')),
        'watch_seconds': watch, 'completion': complete,
        'decision': normalize_decision(field('transcode_decision')),
    }


def normalize_history(data, limit):
    raw = data.get('data')
    if not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw):
        raise TautulliError('Tautulli history returned an invalid row list.')
    if len(raw) > limit:
        raise TautulliError('Tautulli ignored the history row limit. Narrow the filters or check its version.')
    rows, seen = [], set()
    duplicates = 0
    for row in raw:
        count = number(row.get('group_count'))
        if count is not None and count > 1:
            raise TautulliError('Tautulli returned grouped history despite grouping=0. Check its version.')
        normalized = normalize_history_row(row)
        row_id = normalized['row_id']
        if row_id is not None and row_id in seen:
            duplicates += 1
            continue
        if row_id is not None:
            seen.add(row_id)
        rows.append(normalized)
    total = number(data.get('recordsFiltered'))
    if total is not None and (not total.is_integer() or total < len(raw)):
        raise TautulliError('Tautulli history returned inconsistent pagination counts.')
    return {'rows': rows, 'returned': len(raw), 'available': int(total) if total is not None else None,
            'limited': total is None or total > len(raw) or duplicates > 0,
            'duplicates': duplicates}


def date_range(preset, today, custom_start=None, custom_end=None):
    if preset == 'All available':
        return None, None
    if preset == 'Custom':
        if not isinstance(custom_start, date) or not isinstance(custom_end, date) or custom_start > custom_end:
            raise ValueError('Choose a valid start date on or before the end date.')
        return custom_start, custom_end
    if preset == 'This year':
        return date(today.year, 1, 1), today
    days = {'Last 7 days': 7, 'Last 30 days': 30, 'Last 90 days': 90}[preset]
    return today - timedelta(days=days - 1), today


@dataclass(frozen=True)
class HistoryQuery:
    start: date | None
    end: date | None
    timezone_name: str
    user_key: str | None = None
    media_type: str | None = None
    search: str = ''
    limit: int = 1000

    def bounds(self):
        zone = ZoneInfo(self.timezone_name)
        start = datetime.combine(self.start, time.min, zone) if self.start else None
        end = datetime.combine(self.end + timedelta(days=1), time.min, zone) if self.end else None
        return start, end

    def api_params(self):
        # Tautulli interprets dates in its host timezone. Widen the query, then
        # apply exact timezone-aware bounds locally, including DST transitions.
        params = {'grouping': 0, 'include_activity': 0, 'order_column': 'date', 'order_dir': 'desc',
                  'start': 0, 'length': self.limit}
        if self.start:
            params['after'] = (self.start - timedelta(days=2)).isoformat()
        if self.end:
            params['before'] = (self.end + timedelta(days=3)).isoformat()
        if self.user_key and self.user_key.startswith('id:'):
            params['user_id'] = self.user_key[3:]
        if self.media_type:
            params['media_type'] = self.media_type
        if self.search.strip():
            params['search'] = self.search.strip()
        return params


def filter_history(rows, query):
    start, end = query.bounds()
    result = []
    for row in rows:
        moment = row['started']
        if (start or end) and (moment is None or (start and moment < start) or (end and moment >= end)):
            continue
        if query.user_key is not None and row['user_key'] != query.user_key:
            continue
        if query.media_type and row['media_type'] != query.media_type:
            continue
        if query.search.strip().casefold() not in (row['title'] or '').casefold():
            continue
        result.append(row)
    return result


def watch_totals(rows):
    valid = [r['watch_seconds'] for r in rows if r['watch_seconds'] is not None]
    return {'watch_seconds': sum(valid) if len(valid) == len(rows) else None,
            'known_watch_seconds': sum(valid) if valid or not rows else None,
            'missing_duration': len(rows) - len(valid)}


def summary(rows):
    def unique(key):
        return len({r[key] for r in rows}) if all(r[key] is not None for r in rows) else None
    def count(key, value):
        return sum(r[key] == value for r in rows) if all(r[key] is not None for r in rows) else None
    return {**watch_totals(rows), 'plays': len(rows), 'users': unique('user_key'), 'titles': unique('title_key'),
            'movies': count('media_type', 'movie'), 'episodes': count('media_type', 'episode'),
            'decisions': {d: count('decision', d) for d in DECISIONS},
            'local': count('location', 'Local'), 'remote': count('location', 'Remote')}


def rankings(rows, key='title_key', label='title'):
    groups = defaultdict(list)
    for row in rows:
        if row[key] is not None:
            groups[row[key]].append(row)
    result = [{'key': k, 'name': group[0][label] or 'Unavailable', 'plays': len(group), **watch_totals(group)}
              for k, group in groups.items()]
    return sorted(result, key=lambda r: (-r['plays'], r['name']))


def favorite(rows, key):
    if not rows or any(r[key] is None for r in rows):
        return None
    counts = Counter(r[key] for r in rows)
    winners = sorted(k for k, count in counts.items() if count == max(counts.values()))
    return ', '.join(winners)


def aggregate_users(rows, users):
    groups = defaultdict(list)
    for row in rows:
        groups[row['user_key']].append(row)
    known = {u['key']: u for u in users}
    result = []
    for key in dict.fromkeys([*known, *groups]):
        group = groups[key]
        user = known.get(key) or {'key': key, 'user_id': group[0]['user_id'], 'username': group[0]['username'],
                                  'name': group[0]['user'], 'active': None, 'last_seen': None}
        times = [r['started'] for r in group if r['started'] is not None]
        stats = summary(group)
        result.append({**user, **stats, 'last_activity': max(times) if times else None,
                       'top_content': favorite([dict(r, content=r['show'] or r['title']) for r in group], 'content'),
                       'platform': favorite(group, 'platform'), 'player': favorite(group, 'player')})
    return sorted(result, key=lambda u: (-u['plays'], u['name']))


def aggregate_days(rows, timezone_name):
    zone = ZoneInfo(timezone_name)
    groups = defaultdict(list)
    for row in rows:
        if row['started']:
            groups[row['started'].astimezone(zone).date()].append(row)
    return [{'Date': day, 'Plays': len(group), 'Watch minutes (known)':
             None if watch_totals(group)['known_watch_seconds'] is None else watch_totals(group)['known_watch_seconds'] / 60}
            for day, group in sorted(groups.items())]


def format_duration(seconds):
    if seconds is None:
        return 'Unavailable'
    minutes = int(seconds // 60)
    if seconds < 60:
        return f'{int(seconds)} sec'
    return f'{minutes // 60} hr {minutes % 60:02d} min' if minutes >= 60 else f'{minutes} min'


def format_datetime(value, timezone_name):
    return value.astimezone(ZoneInfo(timezone_name)).strftime('%Y-%m-%d %H:%M %Z (%z)') if value else 'Unavailable'
