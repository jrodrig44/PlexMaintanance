# Plex Control Center (Streamlit)

A separate management dashboard for remote Plex and Tautulli services:

`Remote Plex Server -> Remote Tautulli -> LAN -> PlexMaintanance`

PlexMaintanance can run on a different machine from both services. Tautulli remains the authoritative source of current activity and viewing history. This app does not install, restart, or manage either remote service.

## Pages

- **Overview:** Tautulli connection status, Plex name/version, active streams, unique active users, Direct Play / Direct Stream / Transcode counts, and available total/local/remote bandwidth.
- **Now Playing:** stream cards with titles, episode information, user, player, platform/product, local/remote status, quality, stream decisions, progress, elapsed/duration, bandwidth, and available transcode speed. IP addresses are omitted. Optional artwork is fetched through Tautulli; the API key is never placed in browser image URLs.
- **History:** date, user, media-type and title filters, playback/watch-time summaries, daily charts, and a recent playback table.
- **Users:** user directory, rankings over the selected range, and individual playback/content/platform drilldowns.
- **Maintenance:** existing library and recursive disk scanning, watched/unwatched determination, Not in Plex results, Keep overrides, deletion queue, preview/confirmation, status editing, filtering, and SQLite persistence.

Overview, Now Playing, History, and Users need LAN access to Tautulli's API but **do not need access to Plex media storage or the maintenance database**. An unavailable media share does not block live pages. A Tautulli outage shows a degraded state; saved Maintenance results can still be opened.

## Configuration and security

Set these environment variables before launching (PowerShell example):

```powershell
$env:TAUTULLI_URL = 'http://PlexServer:8181'
$env:TAUTULLI_API_KEY = '<your-rotated-api-key>'
```

The URL defaults to `http://PlexServer:8181`. Hostnames, explicit ports, HTTPS, and reverse-proxy base paths are supported. The sidebar allows runtime URL and password-masked API-key overrides. Enable API access in Tautulli and use its API key. `.env` files are ignored but are not automatically loaded; set the process environment or use the sidebar.

**Rotate/regenerate the previously committed Tautulli API key in Tautulli.** The exposed key has been removed from the current Python and legacy PowerShell source, but remains in historical Git commits. Removing it from the current tree does not revoke it. No replacement secret is committed. API credentials are held in process/browser-session memory, never written to SQLite or application logs. Remote error bodies and request URLs are excluded from API errors.

## Run

Use Python 3.12 or later:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py --server.port 8503
```

Alternatively, double-click `Start-PlexMaintanance-Dashboard.bat`, which invokes `Run-Dashboard.ps1`, installs dependencies, and selects an available port starting at 8503.

## Maintenance storage

Only Maintenance disk scanning and deletion require media paths accessible to the dashboard machine, such as `Z:\Plex Movies`. Mapped drives must be available to the account running the app. A missing configured folder produces a scan error; existing saved results remain available.

Maintenance state is stored locally in `scan_history.db`. Existing rows are upserted per media item, without per-scan duplication. Saved state loads on entry to Maintenance, and the reload control remains available. Disk-only files are marked `Not in Plex` and are not added to the scan deletion queue. Keep overrides and preview/confirmation behavior are preserved. As before, a new scan replaces stored statuses with scan results, including prior Keep overrides; review the queue before confirming deletion. While scanning on Windows/macOS, the existing sleep prevention remains active.

Live activity and viewing history are not copied into SQLite. Local databases, environment files, virtual environments, and caches are ignored by Git.

## Live reads and refresh

The existing HTTP abstraction now lives in `tautulli_client.py`; both Maintenance and `dashboard.py` use it. Live payload normalization is separate from Streamlit rendering.

All API requests use the configured Tautulli base URL plus `/api/v2`:

- `get_activity`: current sessions and bandwidth totals.
- `get_server_info`: Plex server name/version.
- `pms_image_proxy`: optional artwork, with constrained Plex metadata paths and thumbnail dimensions.
- Existing Maintenance commands: `get_library_media_info`, `get_metadata`, `get_history`, and `get_server_info` for connection testing.

Commands and fields follow the [Tautulli API reference](https://docs.tautulli.com/extending-tautulli/api-reference).

Activity is cached per Streamlit session for 30 seconds, including failed attempts; a normal rerun fetches again only after expiry. Server info is reused for five minutes. **Refresh** immediately retries both and displays the last successful activity timestamp in UTC. There is no timer, background collector, external refresh package, or infinite loop. Maintenance never initiates live activity reads. Changing connection settings invalidates the session cache.

Artwork is off by default. When enabled, successes and failures are cached in memory for ten minutes, with at most 50 entries per session. Missing/invalid artwork falls back to text. Credentials never appear in image URLs sent to the browser.

Missing optional fields show `Unavailable`. Decision counts require known decisions for all sessions, and unique users require user identifiers. Bandwidth is displayed in Mbps from Tautulli's kbps values; it is Tautulli's reported stream bandwidth, not a host network measurement. Inconsistent or malformed session lists produce a degraded state instead of invented zeroes. Live TV/music or older Tautulli versions may omit episode details, duration, quality, speed, or artwork. A successful API connection is not a claim about Plex host health.

## Tests

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app.py dashboard.py tautulli_client.py analytics.py analytics_service.py analytics_pages.py tests
```

Tests mock HTTP calls and use temporary maintenance databases/files. They cover normalization, counts, missing fields, safe failures, credentials, refresh caching, Streamlit page navigation, unavailable media isolation, persistence/Keep upserts, played/unwatched scanning, disk-only protection, preview, and deletion. No running Plex or Tautulli service is required.

## History and user analytics

Tautulli remains authoritative for viewing history. Historical records are read on demand through the existing HTTP client and held only in the current Streamlit session. History and Users do not read media files, open the Maintenance database, or duplicate history into SQLite. No background collector or per-user/per-row API calls are used.

The shared client now accepts a list response specifically for `get_users`; existing dictionary-returning commands retain their validation. New analytics commands at `/api/v2` are:

- `get_users`: one directory read, normalized immediately to user ID, username/display name, active status and optional last-seen timestamp. Tokens, email, IP addresses and other private fields are discarded.
- `get_history`: one bounded request using `start=0`, `length=500`, `1000` (default), or `2000`; newest first, `grouping=0`, and `include_activity=0`. Date bounds use `after`/`before`, with `user_id`, `media_type`, and `search` when selected. Exact title and date filtering is applied locally as well.

Commands and duration semantics were verified against the current [Tautulli API implementation](https://github.com/Tautulli/Tautulli/blob/master/plexpy/webserve.py) and [history normalization](https://github.com/Tautulli/Tautulli/blob/master/plexpy/datafactory.py). No separate HTTP client was added. `analytics.py` contains pure normalization and calculations, `analytics_service.py` owns bounded reads/cache behavior, and `analytics_pages.py` renders the pages. Overview, Now Playing, and Maintenance retain their Phase 1 implementations.

### Date ranges and timezone

Both pages default to **Last 30 days**. Presets include Last 7/30/90 days, This year, All available, and Custom. The trailing-day presets include today and the preceding N−1 calendar days. Choose a preset and optional custom dates, then press **Apply filters**; custom inputs are used only for Custom.

The explicit display timezone defaults to `America/New_York`. Set `PLEX_DASHBOARD_TIMEZONE` to another IANA zone (for example `Europe/London` or `UTC`) before launching. The timezone is displayed on each analytics page. `tzdata` provides timezone rules on Windows.

Unix timestamps are normalized to aware UTC datetimes. Date filters, daily charts, and displayed dates use the configured zone, including daylight-saving transitions; displayed timestamps include the zone abbreviation and UTC offset. The start date is inclusive at local midnight and the end boundary is exclusive at midnight following the selected end date. A session is assigned to its **start date**; its recorded duration is not split across midnight or clipped at the range boundary.

Tautulli interprets API date bounds in its own host timezone. To avoid losing boundary records when the two servers use different timezones, the API query is widened (two days before the start and two days after the exclusive end), followed by exact local filtering. This also handles differences in API boundary inclusivity. The candidate count reported by Tautulli can therefore exceed the exact-range count. `All available` removes date bounds but still obeys the row cap.

### Watch time and counts

Watch time uses **`play_duration` in seconds**, falling back to the legacy history **`duration` in seconds only when `play_duration` is absent**. Tautulli derives this from stopped minus started minus paused time. Pauses are not subtracted again, and media runtime is never used as watched time. Partial and repeated playback contribute their actual recorded duration. `grouping=0` means each distinct history row counts as a playback record, including partial/resumed sessions; movie/episode counts are playback counts, not counts of completed titles.

Missing, negative, nonnumeric, boolean, or nonfinite durations are excluded. If any duration is unknown, the total shows **Unavailable**, accompanied by a known-duration subtotal and a missing-duration count. Watch-time charts use known durations and say so. Completion uses Tautulli's valid `percent_complete` field; it is not guessed from elapsed watch time.

Unique users use user ID, falling back to username when no ID exists. Unique titles use rating key, then GUID, then media type/title/year. Unknown identities make the corresponding unique metric unavailable. Repeated plays remain separate; duplicate history row IDs are removed and flagged. Deleted or absent directory users still appear when identified in loaded history. Unidentified rows share an explicitly unknown-user bucket; they are not counted as a known unique user.

User totals, top content/shows/movies by plays, most-used platform/player, and local/remote and Direct Play/Direct Stream/Transcode counts are calculated locally. Ties for a favorite are displayed together. A breakdown with missing decision/location fields is unavailable. Last activity means the latest valid start timestamp **within the selected loaded history**, not lifetime last activity. A directory user with zero loaded plays is not assumed inactive; Active/Inactive comes only from Tautulli's `is_active` field.

Charts use Streamlit-native bar charts: plays and known watch minutes by day, top users by known watch time, selected-user trends, and selected-user top content/shows/movies by plays. Default tables exclude IP addresses and raw API fields.

### Bounds, cache, and limitations

All displayed analytics describe **loaded history only**. If `recordsFiltered` exceeds the returned count, is missing, or duplicate rows are found, a limited-dataset warning is shown. Narrow filters or raise the cap to load a more useful sample. The cap applies to date-padded candidates, so there may be fewer matching rows after exact filtering. No metric claims totals for unseen records. User drilldown filters the loaded range locally and does not initiate additional API requests.

Each browser session has a three-minute analytics cache, separate from the unchanged 30-second live cache. Cache identity covers normalized Tautulli URL and a credential digest; history entries also include the date range, timezone, user, media type, title search, and row cap. At most eight cache entries are retained. Filters are submitted together to avoid requests per keystroke. **Refresh** bypasses both history and user-directory caches. Failures are cached briefly too; a failed refresh replaces the previous entry and does not render stale results. Connection changes clear the analytics cache. Credentials are never stored in SQLite or visible cache keys.

Tautulli payloads vary by version: older installations can omit location, decisions, completion, usernames, or timestamps, and may not support `after`/`before`. Exact local filtering still applies, but an older server ignoring date filters can yield fewer matching rows within the cap. Invalid timestamps are excluded from date-filtered results and daily charts (retained as undated rows under All available). Missing directory data degrades separately from history. Grouped responses despite `grouping=0`, oversized responses despite `length`, and inconsistent pagination counts produce safe errors. The API offers no transactional history snapshot; refresh may reflect new or deleted records.

For local manual QA with synthetic data, run:

```powershell
python -m streamlit run tests/manual_dashboard.py --server.port 8517
```

Use its Active, Empty, and Offline scenarios and navigate to History/Users. This harness mocks Tautulli HTTP calls and uses a temporary maintenance database. Automated tests additionally exercise duration semantics, DST/date filters, aggregation, malformed payloads, cache identity/expiry/refresh, failure isolation, and both new Streamlit pages without media/database access, alongside the Phase 1 regressions.
