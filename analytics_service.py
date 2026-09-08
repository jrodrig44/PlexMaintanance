"""Bounded, session-owned analytics cache. Stores only normalized public fields."""
import hashlib
import time
from datetime import datetime, timezone
from analytics import normalize_history, normalize_user
from tautulli_client import tautulli_get, normalize_base_url, TautulliError

CACHE_SECONDS = 180
HISTORY_SCHEMA_VERSION = 4  # Do not reuse older normalized rows after a hot reload.


def connection_cache(state, base_url, api_key):
    base_url = normalize_base_url(base_url)
    identity = hashlib.sha256((base_url + '\0' + api_key).encode()).hexdigest()
    if state.get('identity') != identity:
        state.clear()
        state['identity'] = identity
        state['entries'] = {}
    return base_url, state['entries']


def cached_read(state, base_url, api_key, key, loader, refresh=False):
    base_url, entries = connection_cache(state, base_url, api_key)
    entry = entries.get(key)
    now = time.monotonic()
    if entry is None or refresh or now - entry['attempt'] >= CACHE_SECONDS:
        entry = {'attempt': now}
        try:
            entry['data'] = loader(base_url)
            entry['success'] = datetime.now(timezone.utc)
        except TautulliError as ex:
            # Failed refresh replaces the previous entry: stale data is not rendered.
            entry['error'] = str(ex)
        if len(entries) >= 8 and key not in entries:
            entries.pop(next(iter(entries)))
        entries[key] = entry
    return entry


def read_history(state, base_url, api_key, query, refresh=False):
    if not 1 <= query.limit <= 2000:
        raise TautulliError('History row limit must be between 1 and 2000.')
    def load(url):
        return normalize_history(tautulli_get(url, api_key, 'get_history', **query.api_params()), query.limit)
    return cached_read(state, base_url, api_key, ('history', query, HISTORY_SCHEMA_VERSION), load, refresh)


def read_users(state, base_url, api_key, refresh=False):
    def load(url):
        data = tautulli_get(url, api_key, 'get_users')
        if any(not isinstance(user, dict) for user in data):
            raise TautulliError('Tautulli returned an invalid user list.')
        return [normalize_user(user) for user in data]
    return cached_read(state, base_url, api_key, ('users',), load, refresh)
