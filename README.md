# Plex Control Center (Streamlit)

A separate management dashboard for remote Plex and Tautulli services:

`Remote Plex Server -> Remote Tautulli -> LAN -> PlexMaintanance`

PlexMaintanance can run on a different machine from both services. Tautulli remains the authoritative source of current activity and viewing history. This app does not install, restart, or manage either remote service.

## Pages

- **Overview:** Tautulli connection status, Plex name/version, active streams, unique active users, Direct Play / Direct Stream / Transcode counts, and available total/local/remote bandwidth.
- **Now Playing:** stream cards with titles, episode information, user, player, platform/product, local/remote status, quality, stream decisions, progress, elapsed/duration, bandwidth, and available transcode speed. IP addresses are omitted. Optional artwork is fetched through Tautulli; the API key is never placed in browser image URLs.
- **History:** date, user, media-type and title filters, playback/watch-time summaries, daily charts, and a recent playback table.
- **Users:** user directory, rankings over the selected range, and individual playback/content/platform drilldowns.
- **Devices:** identified client and player/platform groups, known-subset playback rates, rankings and device/group drilldowns.
- **Transcoding:** decision coverage, transcode rankings, conditional component/4K analysis and recent transcodes.
- **Bandwidth:** current bandwidth estimates and stream ranking, plus conditional historical bandwidth calculations with explicit coverage and stock-history limitations.
- **Maintenance:** existing library and recursive disk scanning, watched/unwatched determination, Not in Plex results, Keep overrides, deletion queue, preview/confirmation, status editing, filtering, and SQLite persistence.

Overview, Now Playing, History, Users, Devices, Transcoding, and Bandwidth need LAN access to Tautulli's API but **do not need access to Plex media storage or the maintenance database**. An unavailable media share does not block live pages. A Tautulli outage shows a degraded state; saved Maintenance results can still be opened.

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
python -m compileall -q app.py dashboard.py tautulli_client.py analytics.py analytics_service.py analytics_pages.py playback.py device_analytics.py device_pages.py tests
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

## Devices and Transcoding

The Phase 3 pages use the same remote architecture and in-memory, bounded history as History/Users. No new API endpoints, history database, media filesystem access, collectors, or polling were introduced. The shared client still calls `get_history` and optionally the cached `get_users` directory; all device/transcode rankings and drilldowns are local calculations. `get_activity` remains limited to the existing live pages.

Both new pages offer the Phase 2 date presets (Last 30 days by default), optional user/media/title filters and the 500/1,000/2,000 candidate-row cap. Exact timezone filtering, missing-date warnings and limited-history warnings are unchanged. All statistics refer to loaded history, not lifetime or unseen records.

### Device identity and privacy

Identity follows a deterministic hierarchy:

1. A nonempty `machine_id` identifies a Tautulli **client**, with a SHA-256 digest used only internally. The raw identifier is discarded, never displayed or written to SQLite. A literal IP address is rejected as an identifier.
2. Without a machine ID, player name + product + platform form a **Player label group**. Different players on the same platform remain separate. Identical labels can represent multiple clients; these groups are not counted as uniquely identified devices.
3. Without a player, available platform/product form a **Platform/product group**.
4. Without any identity fields, records go into **Unknown device**, which does not increase the identified-device count.

The identified-device metric counts distinct client identifiers, not proven physical hardware. An identifier stays together after a player rename; different identifiers remain separate even when their names and platform match. Group numbers in tables/controls distinguish identical display labels without revealing identifiers. Sorting uses descending plays, then name, scope and internal key. IP addresses, tokens and unrelated network fields are discarded; default tables never show them. Digests and histories remain session-memory data, not a new local database.

Devices shows coverage, playback/location rates, device/group and platform charts, a ranking table, and a separate expandable playback breakdown. Drilldowns include known watch time, unique users/titles, playback/location counts, daily trends, selectable top users/content/shows/movies and recent activity. A chart selector switches top devices between plays and known watch minutes. Rankings and drilldowns always state the actual identity level.

### Decisions, percentages and categories

`playback.py` centralizes the existing live/history convention: `direct play` → Direct Play, `copy`/`direct stream` → Direct Stream, and `transcode` → Transcode. Unknown or missing values stay unknown. The Phase 1 live cache and rendering are unchanged.

Phase 3 reports both known and unknown decision counts. Rates use **only known decisions**: 60 Direct Play, 10 Direct Stream, 20 Transcode and 10 unknown means 66.7% Direct Play and 22.2% Transcode among 90 known—not percentages of all 100. Location rates independently use known Local/Remote records; unknown locations are not Local. Unique known platforms and identified clients have explicit missing/identity coverage. Phase 2's existing all-or-unavailable summary semantics remain unchanged.

Component classification requires overall Transcode plus explicit video and audio decisions:

- Both Transcode: Video + audio transcode.
- Video Transcode and audio Direct Play/Direct Stream: Video-only transcode.
- Audio Transcode and video Direct Play/Direct Stream: Audio-only transcode.
- Missing, contradictory or insufficient components: Unknown.

Direct Play and Direct Stream retain their overall categories unless an explicit component contradicts them. No codec incompatibility, subtitle-burning cause, or other transcode reason is inferred. Video, audio-only and remote transcode counts describe their known/classifiable subsets, with coverage captions; unavailable detail does not become zero.

### Fields and current Tautulli limitations

Verified standard `get_history` fields used for this phase are `machine_id`, `player`, `platform`, `product`, `transcode_decision` and `location`, alongside existing history fields. The [current Tautulli history implementation](https://github.com/Tautulli/Tautulli/blob/master/plexpy/datafactory.py) **does not return source/stream resolution, codecs, container, or video/audio/subtitle decisions**. Consequently, standard history supports device and overall decision analytics, but detailed historical classification and 4K analytics normally show **Unavailable**.

The shared normalizer conditionally understands these established Tautulli stream-field names **only if explicitly present** in a supplied payload:

| Raw Tautulli fields | Normalized fields |
| --- | --- |
| `machine_id`, `player`, `platform`, `product` | `device_key` (digest), `device_scope`, `device_name`, `product` |
| `video_resolution`, `stream_video_resolution` | `source_resolution`, `stream_resolution` |
| `stream_video_decision` / `video_decision` | `video_decision` |
| `stream_audio_decision` / `audio_decision` | `audio_decision` |
| `stream_subtitle_decision` / `subtitle_decision` | `subtitle_decision` |
| `video_codec`, `audio_codec`, `container` | same-named source fields |
| `stream_video_codec`, `stream_audio_codec`, `stream_container` | same-named stream fields |

These detail names are documented for [Tautulli activity and stream details](https://docs.tautulli.com/extending-tautulli/api-reference). They are not assumed to exist in stock history, and no per-row `get_stream_data` calls are made. Optional technical columns appear only when the loaded payload actually contains usable values. Unknown subtitle decisions remain unknown; no reason field is invented. Older versions may also omit product, machine ID or location, reducing identity/coverage accordingly.

Resolution normalization accepts explicit `4k`, `uhd`, `4k uhd`, `uhd 4k`, `2160`, and `2160p` as 4K. Known lower resolutions and 8K remain distinct; unrecognized/missing values are unknown. Only **source** resolution determines 4K playback/transcodes/Direct Play. Bitrate, codecs, and output/stream resolution are never used to guess the source. Counts state source/decision coverage, and the recent-4K list includes only explicit 4K-source transcodes.

Transcoding provides two compact decision/category charts, a selectable ranking table for devices/groups, users, titles, shows, platforms, source resolutions and source video codecs, local/remote counts, a recent-transcode table, and an expandable recent-4K table. Empty or unsupported rankings show an unavailable state.

### Cache reuse and validation

Devices and Transcoding reuse the existing three-minute historical cache, including across pages with identical query values. Cache identity retains URL, credential digest, dates, timezone, user/media/title filters and row cap; a normalized-schema version prevents reuse of older rows after an app reload. No competing cache was added. Manual Refresh bypasses the current history/directory entries as before, failures do not render stale data, and drilldowns make no API calls. The 30-second live cache is unchanged.

The manual harness now includes **Detailed synthetic** in addition to Active/Empty/Offline. Active represents ordinary history without detailed stream fields; Detailed synthetic deliberately adds documented optional stream fields to exercise conditional classification/4K rendering. It does not claim that stock `get_history` supplies those fields. Both scenarios include Direct Play, Direct Stream, Transcode and unknown examples.

The Phase 3 tests cover identity/grouping/privacy, deterministic rankings, known-subset rates, component classifications, conservative 4K detection, optional-field handling, cache reuse/schema upgrade, limited datasets, page failures/empty states, refresh and media/database isolation. All Phase 1/2 tests remain part of the full suite.

## Phase 4: Bandwidth

Navigation is now Overview, Now Playing, History, Users, Devices, Transcoding, **Bandwidth**, Maintenance. The Bandwidth page combines an unfiltered **current** snapshot with a separately filtered historical section. Plex and Tautulli remain remote; PlexMaintanance uses Tautulli as its authority. There is no background collector, persistent bandwidth/history database, measured network-interface traffic, or new monitoring/deployment service. Maintenance SQLite remains Maintenance-only.

### Sources and units

The existing `/api/v2` transport uses `get_activity` for `total_bandwidth`, `lan_bandwidth`, `wan_bandwidth`, and each session's `bandwidth`. These are decimal **kilobits per second (kbps)**. Current bandwidth is Plex Streaming Brain's estimate of reserved/required streaming bandwidth, **not measured throughput** and not necessarily the media's bitrate. See the [Tautulli API reference](https://docs.tautulli.com/extending-tautulli/api-reference), [bandwidth explanation](https://docs.tautulli.com/support/frequently-asked-questions#history-q12), and [notification parameter definitions](https://github.com/Tautulli/Tautulli/blob/master/plexpy/common.py) (`stream_bandwidth`).

`playback.normalize_bandwidth` accepts finite nonnegative numbers/numeric strings, rejects booleans, negatives, NaN/infinity, containers, missing values and unit-bearing strings, and returns a numeric `bandwidth_kbps` or `None`. Live sessions retain the existing `bandwidth` alias for compatibility. Activity aggregates keep their existing normalized metric names. `bandwidth_mbps` and `format_bandwidth` centralize display conversion: 1,000 Kbps = 1 Mbps; 1,000 Mbps = 1 Gbps. Normal values use Mbps, small nonzero values Kbps, very large values Gbps. Existing live pages retain their Mbps formatting. No bytes/sec, media bitrate, quality label or watched duration is silently substituted.

### Current snapshot

Current cards show total/local/remote bandwidth, active/remote/transcode stream counts, and the highest known session. Tautulli aggregates take precedence even when session detail is incomplete. If an aggregate is absent, a session sum is used only when every relevant bandwidth is known; local/remote fallback also requires every session's location to be known. Empty session lists produce derived zero. Unknown location never becomes Local; incomplete current location/decision counts show Unavailable. The page labels each total's source and session/location coverage.

The session table sorts bandwidth descending, puts unknown values last, and includes public title/user/player/platform, location, playback decision, bandwidth, quality, resolution, speed and a high-bandwidth flag. The chart shows the highest 20 known sessions. The numeric threshold defaults to **20 Mbps**, and a session is high only when its known bandwidth is **strictly greater** than the threshold. Unknown is not classified as low. Changing the threshold is a local calculation, with no alerting or notifications.

### Historical limitations and conditional detail

**Stock `get_history` does not return reliable per-playback bandwidth.** The [history implementation](https://github.com/Tautulli/Tautulli/blob/master/plexpy/datafactory.py) and documented `get_home_stats` outputs were reviewed: history/home statistics provide playback counts, durations, rankings and concurrency, not a usable bounded bandwidth aggregate. No additional endpoint or per-row enrichment call was added. With ordinary history, Bandwidth explicitly shows historical bandwidth unavailable and omits unsupported rankings/trends. Current values are never persisted or turned into historical samples. Existing History/Devices/Transcoding remain available for supported playback metrics; watch time is not a substitute for bandwidth.

For payloads that explicitly supply the established Tautulli `bandwidth` field in kbps, conditional calculations use **known playback values only**. This is compatibility handling, exercised with synthetic payloads, not a claim that stock history exposes the field. Mean and median are per-playback and unweighted; peak is the maximum known playback value, not a simultaneous network peak. No GB-transfer estimate or bitrate-times-duration calculation is included.

The section exposes bandwidth coverage and missing/invalid counts; joint bandwidth/location coverage; local/remote counts and means; highest remote playback; and Remote % whose denominator contains only playbacks with both bandwidth and location. Rankings switch between users, devices/groups and titles, sorted by mean, peak or high-bandwidth count. Device rows reuse Phase 3 identity scopes and include remote/transcode means, their sample counts and location/decision coverage. Unknown identities are excluded and coverage is shown; raw machine IDs and IP fields are discarded. Daily bars show average and remote average Mbps and high-bandwidth counts in the configured timezone, with a coverage table. Days without supplied values remain unavailable; dates outside loaded rows are not filled in as zero.

### Filters, caches and failure handling

Bandwidth reuses the same HistoryQuery, Last 7/30/90 days, This year, All available and Custom presets (default Last 30 days), timezone boundaries, user/media/title filters, row cap, deduplication and loaded-history warnings. These filters apply only to history. Identical queries across historical pages reuse the same three-minute cache; normalized history schema version 4 prevents obsolete cached rows surviving a hot reload. Current data shares `live_dashboard` and the 30-second activity cache with Overview/Now Playing. There is no second cache or N+1 request pattern.

A cold Bandwidth page uses one `get_activity`, one bounded `get_history`, plus the existing cached `get_server_info` and `get_users` reads. The shared directory TTL is three minutes and server-info TTL five minutes. Manual Refresh bypasses the relevant live/history and supporting entries once each. Fresh reruns, threshold/ranking changes and compatible page changes make no extra requests. Current and historical errors are independent: a failed source does not hide the other section, and stale results from a failed refresh are not displayed. No media-filesystem or Maintenance-database access is needed.

### Validation

`tests/test_bandwidth.py` adds mocked normalization, units, complete/partial aggregate fallbacks, sorting, threshold, historical known-subset calculations, identities, daily timezone behavior, filtering, cache TTL/refresh/connection isolation, API efficiency, privacy, outages/auth/malformed payloads and Streamlit page tests. All earlier phase tests remain in the full suite; their navigation fixtures include Bandwidth. Run `python -m unittest discover -s tests`.

The local mock harness (`streamlit run tests/manual_dashboard.py`) adds **Bandwidth synthetic**: 8 Mbps Local Direct Play and 25 Mbps Remote Transcode current sessions, plus deliberately supplied historical bandwidth with partial coverage. Active retains stock-style history with no bandwidth. Empty and Offline exercise the respective states. These scenarios never require real Plex/Tautulli services or production Maintenance data.
