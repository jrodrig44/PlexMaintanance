"""Shared Tautulli transport and live payload normalization; no persistence."""
import math
from urllib.parse import urlsplit
import requests


class TautulliError(RuntimeError):
    """Safe, actionable error without remote response bodies or request URLs."""


def normalize_base_url(raw):
    value = (raw or '').strip()
    if not value:
        raise TautulliError('Tautulli URL cannot be empty.')
    if not value.startswith(('http://', 'https://')):
        value = 'http://' + value + ('' if value.rsplit(':', 1)[-1].isdigit() else ':8181')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise TautulliError('Invalid Tautulli URL.') from None
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise TautulliError('Use a Tautulli host URL without credentials, query, or fragment.')
    return value.rstrip('/')


def _request(base_url, api_key, command, **params):
    base_url = normalize_base_url(base_url)
    if not api_key.strip():
        raise TautulliError('Set TAUTULLI_API_KEY or enter an API key in the sidebar.')
    try:
        response = requests.get(f'{base_url}/api/v2', params={**params, 'apikey': api_key, 'cmd': command}, timeout=20, allow_redirects=False)
        if response.status_code in (401, 403):
            raise TautulliError('Tautulli authentication failed. Check the API key.')
        if 300 <= response.status_code < 400:
            raise TautulliError('Tautulli redirected the request. Configure its final URL.')
        response.raise_for_status()
        return response
    except requests.Timeout:
        raise TautulliError('Tautulli request timed out. Check LAN connectivity.') from None
    except requests.RequestException:
        raise TautulliError('Tautulli HTTP request failed. Check the service URL and LAN connectivity.') from None
    except ValueError:
        raise TautulliError('Tautulli request configuration is invalid.') from None


def tautulli_get(base_url, api_key, command, **params):
    response = _request(base_url, api_key, command, **params)
    try:
        payload = response.json()
    except ValueError:
        raise TautulliError('Tautulli returned invalid JSON.') from None
    envelope = payload.get('response') if isinstance(payload, dict) else None
    if not isinstance(envelope, dict):
        raise TautulliError('Tautulli returned a malformed API response.')
    if envelope.get('result') != 'success':
        raise TautulliError('Tautulli API rejected the request. Check the API key and command permissions.')
    data = envelope.get('data')
    if not isinstance(data, dict):
        raise TautulliError('Tautulli returned malformed command data.')
    return data


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def normalize_session(raw):
    def field(*names):
        return next((str(raw[n]) for n in names if raw.get(n) not in (None, '')), None)
    elapsed, duration = number(raw.get('view_offset')), number(raw.get('duration'))
    progress = number(raw.get('progress_percent'))
    if progress is None and elapsed is not None and duration:
        progress = elapsed / duration * 100
    decision = field('transcode_decision')
    decision = {'direct play': 'Direct Play', 'copy': 'Direct Stream', 'direct stream': 'Direct Stream', 'transcode': 'Transcode'}.get((decision or '').lower())
    return {
        'artwork': field('grandparent_thumb', 'thumb'),
        'title': field('full_title', 'title'),
        'show': field('grandparent_title'),
        'season': field('parent_media_index'),
        'episode': field('media_index'),
        'episode_title': field('title') if raw.get('media_type') == 'episode' else None,
        'user': field('friendly_name', 'user'),
        'user_id': field('user_id', 'user'),
        'player': field('player', 'device'),
        'platform': field('platform'),
        'product': field('product'),
        'location': {'1': 'Local', '0': 'Remote', 'true': 'Local', 'false': 'Remote'}.get(
            str(raw.get('local')).lower(),
            {'lan': 'Local', 'wan': 'Remote'}.get(raw.get('location')),
        ),
        'quality': field('quality_profile'),
        'resolution': field('stream_video_full_resolution', 'stream_video_resolution'),
        'decision': decision,
        'video': field('stream_video_decision', 'video_decision'),
        'audio': field('stream_audio_decision', 'audio_decision'),
        'progress': min(progress, 100) if progress is not None else None,
        'elapsed': elapsed,
        'duration': duration,
        'bandwidth': number(raw.get('bandwidth')),
        'speed': number(raw.get('transcode_speed')),
    }

def normalize_activity(data):
    sessions = data.get('sessions')
    if not isinstance(sessions, list) or any(not isinstance(s, dict) for s in sessions):
        raise TautulliError('Tautulli activity is missing a valid session list.')
    sessions = [normalize_session(s) for s in sessions]
    count = number(data.get('stream_count'))
    if count is not None and count != len(sessions):
        raise TautulliError('Tautulli activity contains an inconsistent session count.')
    users = [s['user_id'] for s in sessions]
    decisions = [s['decision'] for s in sessions]
    metrics = {'Active streams': len(sessions), 'Unique active users': len(set(users)) if all(users) else None}
    for label in ('Direct Play', 'Direct Stream', 'Transcode'):
        metrics[label] = decisions.count(label) if all(decisions) else None
    for label, key in [('Total bandwidth','total_bandwidth'), ('Local bandwidth','lan_bandwidth'), ('Remote bandwidth','wan_bandwidth')]:
        metrics[label] = number(data.get(key))
    return {'sessions': sessions, 'metrics': metrics}


def get_activity(base_url, api_key):
    return normalize_activity(tautulli_get(base_url, api_key, 'get_activity'))


def get_server_info(base_url, api_key):
    data = tautulli_get(base_url, api_key, 'get_server_info')
    return {'name': data.get('pms_name'), 'version': data.get('pms_version')}



def get_artwork(base_url, api_key, path):
    """Fetch only Plex metadata artwork through Tautulli, never browser credential URLs."""
    import re
    from io import BytesIO
    from PIL import Image, UnidentifiedImageError
    if not path or not re.fullmatch(r'/library/metadata/\d+/(?:thumb|art)(?:/\d+)?', path):
        return None
    try:
        response = _request(base_url, api_key, 'pms_image_proxy', img=path, width=180, height=270, img_format='png', fallback='poster')
        content = response.content
        if len(content) > 2_000_000:
            return None
        with Image.open(BytesIO(content)) as picture:
            if picture.width * picture.height > 2_000_000:
                return None
            picture.verify()
        return content
    except (TautulliError, OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return None
