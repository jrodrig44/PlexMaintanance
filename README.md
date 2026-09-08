# Plex Control Center (Streamlit)

A separate management dashboard for remote Plex and Tautulli services:

`Remote Plex Server -> Remote Tautulli -> LAN -> PlexMaintanance`

PlexMaintanance can run on a different machine from both services. Tautulli remains the authoritative source of current activity and viewing history. This app does not install, restart, or manage either remote service.

## Pages

- **Overview:** Tautulli connection status, Plex name/version, active streams, unique active users, Direct Play / Direct Stream / Transcode counts, and available total/local/remote bandwidth.
- **Now Playing:** stream cards with titles, episode information, user, player, platform/product, local/remote status, quality, stream decisions, progress, elapsed/duration, bandwidth, and available transcode speed. IP addresses are omitted. Optional artwork is fetched through Tautulli; the API key is never placed in browser image URLs.
- **Maintenance:** existing library and recursive disk scanning, watched/unwatched determination, Not in Plex results, Keep overrides, deletion queue, preview/confirmation, status editing, filtering, and SQLite persistence.

Overview and Now Playing need LAN access to Tautulli's API but **do not need access to Plex media storage or the maintenance database**. An unavailable media share does not block live pages. A Tautulli outage shows a degraded state; saved Maintenance results can still be opened.

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
python -m compileall -q app.py dashboard.py tautulli_client.py tests
```

Tests mock HTTP calls and use temporary maintenance databases/files. They cover normalization, counts, missing fields, safe failures, credentials, refresh caching, Streamlit page navigation, unavailable media isolation, persistence/Keep upserts, played/unwatched scanning, disk-only protection, preview, and deletion. No running Plex or Tautulli service is required.
