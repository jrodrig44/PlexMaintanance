# Plex Media Dashboard (Streamlit)

This project provides a Streamlit dashboard for scanning a Plex library through the Tautulli API.

## Features

- Scan media in a library section using Tautulli
- Recursively scan a media folder for `.avi`, `.mkv`, `.mp4`, `.mpg`, and `.rmvb` files
- Identify video files on disk that Plex has not indexed; these are never added to the deletion queue
- Show counts for:
  - Total media
  - Scanned media
  - Played media
  - Unwatched media
  - Queued-for-delete media
  - Deleted media
- Search, filter, and sort scan results
- Toggle deletion on or off
- Optional preview + confirmation before deleting files

## Run

### One-click start (recommended)

Double-click [Start-Dashboard.bat](Start-Dashboard.bat) in the project folder.

This will:

- Move to the project directory
- Ensure required Python packages are installed
- Launch Streamlit for [app.py](app.py)

### Manual start

1. Create and activate a virtual environment:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

2. Install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Start the dashboard:

   ```powershell
   python -m streamlit run app.py --server.port 8503
   ```

4. Open the local URL shown by Streamlit in your browser.

## Notes

- Enter hostnames like `PlexServer` or full URLs like `http://PlexServer:8181`.
- The app normalizes host input to a valid Tautulli base URL.
- Deletion is performed from the machine running this app, so file paths must be accessible from that machine.
- Scan history is persisted in a local SQLite database file: `scan_history.db`.
- The database stores one upserted row per media item (no per-scan snapshot duplication).
- During scans, existing media rows are updated and only net-new media are inserted.
- On startup, the app auto-loads media state from the database.
- While a scan is active on Windows or macOS, the app keeps the computer awake. The display may turn off normally, and the usual power settings resume when the scan finishes or fails. Closing a MacBook's lid can still put it to sleep.
