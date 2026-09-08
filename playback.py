"""Pure playback field normalization shared by live and historical data."""
import hashlib
import ipaddress
import json
import math
import re


def normalize_bandwidth(value):
    """Tautulli bandwidth fields are decimal kilobits/second, not media bitrate.

    Current values are Plex Streaming Brain reserved/required estimates, not
    measured network throughput. Stock get_history omits the bandwidth field.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (ValueError, OverflowError):
        return None


def bandwidth_mbps(value):
    value = normalize_bandwidth(value)
    return None if value is None else value / 1000


def format_bandwidth(value, adaptive=True):
    mbps = bandwidth_mbps(value)
    if mbps is None:
        return 'Unavailable'
    if adaptive and mbps >= 1000:
        return f'{mbps / 1000:,.2f} Gbps'
    if adaptive and 0 < mbps < 1:
        return f'{mbps * 1000:,.2f} Kbps'
    return f'{mbps:,.2f} Mbps'


def public_text(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    value = str(value).strip()
    return value or None


def normalize_decision(value):
    return {'direct play': 'Direct Play', 'copy': 'Direct Stream', 'direct stream': 'Direct Stream',
            'transcode': 'Transcode'}.get((public_text(value) or '').lower())


def normalize_resolution(value):
    value = (public_text(value) or '').lower().strip()
    if value in ('4k', 'uhd', '4k uhd', 'uhd 4k', '2160', '2160p'):
        return '4K'
    match = re.fullmatch(r'(240|360|480|576|720|1080|1440|4320)([pi]?)', value)
    if match:
        return match[1] + (match[2] or 'p')
    return {'sd': 'SD', 'hd': 'HD', '8k': '8K'}.get(value)


def is_4k(resolution):
    value = normalize_resolution(resolution)
    if value is None or value == 'HD':
        return None
    return value == '4K'


def normalize_codec(value):
    value = (public_text(value) or '').lower()
    # Restrict to codec/container-like tokens, not paths or arbitrary payloads.
    if not re.fullmatch(r'[a-z0-9][a-z0-9._+-]{0,30}', value):
        return None
    return {'h265': 'hevc', 'h.265': 'hevc', 'h.264': 'h264', 'avc': 'h264'}.get(value, value)


def device_identity(raw):
    machine = public_text(raw.get('machine_id'))
    if machine:
        try:
            ipaddress.ip_address(machine)
            machine = None
        except ValueError:
            pass
    player, platform, product = (public_text(raw.get(k)) for k in ('player', 'platform', 'product'))
    if machine:
        scope, parts = 'Identified client', ['machine', machine]
    elif player:
        scope, parts = 'Player label group', ['player', player, product, platform]
    elif platform or product:
        scope, parts = 'Platform/product group', ['platform', platform, product]
    else:
        return {'device_key': None, 'device_scope': 'Unknown', 'device_name': 'Unknown device'}
    # The raw identifier is neither retained nor sent to Streamlit.
    key = hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()
    return {'device_key': key, 'device_scope': scope, 'device_name': player or product or platform or 'Unnamed client'}


def normalize_playback_fields(raw):
    """Only consume named Tautulli fields actually present in the payload.

    Stock get_history supplies identity/product but omits detailed stream fields.
    The optional names below are documented by get_activity/get_stream_data;
    they remain None for ordinary history. No enrichment requests are made.
    """
    def decision(primary, fallback):
        return normalize_decision(raw.get(primary) if primary in raw else raw.get(fallback))
    return {
        'bandwidth_kbps': normalize_bandwidth(raw.get('bandwidth')),
        **device_identity(raw), 'product': public_text(raw.get('product')),
        'source_resolution': normalize_resolution(raw.get('video_resolution')),
        'stream_resolution': normalize_resolution(raw.get('stream_video_resolution')),
        'video_decision': decision('stream_video_decision', 'video_decision'),
        'audio_decision': decision('stream_audio_decision', 'audio_decision'),
        'subtitle_decision': decision('stream_subtitle_decision', 'subtitle_decision'),
        'video_codec': normalize_codec(raw.get('video_codec')),
        'audio_codec': normalize_codec(raw.get('audio_codec')),
        'container': normalize_codec(raw.get('container')),
        'stream_video_codec': normalize_codec(raw.get('stream_video_codec')),
        'stream_audio_codec': normalize_codec(raw.get('stream_audio_codec')),
        'stream_container': normalize_codec(raw.get('stream_container')),
    }
