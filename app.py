import os
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from tautulli_client import normalize_base_url, tautulli_get
from dashboard import render_live_dashboard
from analytics_pages import render_analytics
import streamlit as st


DEFAULT_BASE_URL = os.environ.get("TAUTULLI_URL", "http://PlexServer:8181")
DEFAULT_API_KEY = os.environ.get("TAUTULLI_API_KEY", "")
DEFAULT_LIBRARY_ID = "1"
DEFAULT_MEDIA_FOLDER = r"Z:\Plex Movies"
VIDEO_EXTENSIONS = {".avi", ".mkv", ".mp4", ".mpg", ".rmvb"}
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "scan_history.db")


@contextmanager
def prevent_system_sleep():
    """Keep the computer awake for the duration of a long-running operation."""
    if sys.platform == "darwin":
        import subprocess

        try:
            caffeinate_process = subprocess.Popen(
                ["caffeinate", "-i"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            # Do not prevent a scan if the system sleep utility is unavailable.
            yield
            return

        try:
            yield
        finally:
            caffeinate_process.terminate()
            try:
                caffeinate_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                caffeinate_process.kill()
                caffeinate_process.wait()
        return

    if os.name != "nt":
        yield
        return

    import ctypes

    es_continuous = 0x80000000
    es_system_required = 0x00000001
    set_execution_state = ctypes.windll.kernel32.SetThreadExecutionState

    # Keep the computer awake without forcing the display to remain on.
    sleep_prevention_enabled = bool(
        set_execution_state(es_continuous | es_system_required)
    )
    try:
        yield
    finally:
        if sleep_prevention_enabled:
            set_execution_state(es_continuous)


@dataclass
class ScanMetrics:
    total_media: int = 0
    scanned_media: int = 0
    played_media: int = 0
    unwatched_media: int = 0
    queued_for_delete: int = 0
    deleted_media: int = 0
    disk_only_media: int = 0
    errors: int = 0


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS media_state (
                media_key TEXT PRIMARY KEY,
                rating_key TEXT,
                title TEXT NOT NULL,
                file_path TEXT,
                total_plays INTEGER NOT NULL,
                server_status TEXT NOT NULL,
                status TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )
            """
        )

        table_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(media_state)").fetchall()
        }
        if "server_status" not in table_columns:
            conn.execute("ALTER TABLE media_state ADD COLUMN server_status TEXT NOT NULL DEFAULT ''")

        media_count_row = conn.execute("SELECT COUNT(*) AS item_count FROM media_state").fetchone()
        media_count = int(media_count_row["item_count"]) if media_count_row else 0

        has_legacy_scans = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'scans'"
        ).fetchone()
        has_legacy_results = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'media_results'"
        ).fetchone()

        if media_count == 0 and has_legacy_scans and has_legacy_results:
            latest_scan_row = conn.execute("SELECT id FROM scans ORDER BY id DESC LIMIT 1").fetchone()
            if latest_scan_row:
                latest_scan_id = int(latest_scan_row["id"])
                now = datetime.now(timezone.utc).isoformat()
                legacy_rows = conn.execute(
                    """
                    SELECT title, file_path, total_plays, status
                    FROM media_results
                    WHERE scan_id = ?
                    ORDER BY row_index ASC
                    """,
                    (latest_scan_id,),
                ).fetchall()

                for row in legacy_rows:
                    title = str(row["title"] or "")
                    file_path = str(row["file_path"] or "")
                    total_plays = int(row["total_plays"] or 0)
                    status = str(row["status"] or "")
                    media_key = f"file:{file_path.lower()}" if file_path else f"title:{title.lower()}"

                    conn.execute(
                        """
                        INSERT INTO media_state (
                            media_key, rating_key, title, file_path, total_plays, server_status, status, first_seen_at, last_seen_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(media_key) DO UPDATE SET
                            title = excluded.title,
                            file_path = excluded.file_path,
                            total_plays = excluded.total_plays,
                            server_status = excluded.server_status,
                            status = excluded.status,
                            last_seen_at = excluded.last_seen_at
                        """,
                        (media_key, "", title, file_path, total_plays, status, status, now, now),
                    )

        normalize_media_state(conn)

        conn.commit()
    finally:
        conn.close()


def normalize_media_state(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT media_key, rating_key, title, file_path, total_plays, server_status, status, first_seen_at, last_seen_at
        FROM media_state
        ORDER BY last_seen_at DESC
        """
    ).fetchall()

    if not rows:
        return

    canonical: Dict[str, Dict[str, Any]] = {}
    file_to_key: Dict[str, str] = {}
    title_to_key: Dict[str, str] = {}

    # First pass: seed canonical keys from rows that already have rating_key.
    for row in rows:
        rating_key = str(row["rating_key"] or "").strip()
        if not rating_key:
            continue

        file_path = str(row["file_path"] or "").strip()
        title = str(row["title"] or "").strip()
        key = f"rating:{rating_key}|file:{file_path.lower()}" if file_path else f"rating:{rating_key}"
        if key in canonical:
            continue

        canonical[key] = {
            "media_key": key,
            "rating_key": rating_key,
            "title": title,
            "file_path": file_path,
            "total_plays": int(row["total_plays"] or 0),
            "server_status": str(row["server_status"] or ""),
            "status": str(row["status"] or ""),
            "first_seen_at": str(row["first_seen_at"] or ""),
            "last_seen_at": str(row["last_seen_at"] or ""),
        }

        if file_path:
            file_to_key[file_path.lower()] = key
        if title:
            title_to_key[title.lower()] = key

    # Second pass: fold rows without rating_key into the seeded canonical mapping when possible.
    for row in rows:
        rating_key = str(row["rating_key"] or "").strip()
        file_path = str(row["file_path"] or "").strip()
        title = str(row["title"] or "").strip()

        key: str
        if rating_key and file_path:
            key = f"rating:{rating_key}|file:{file_path.lower()}"
        elif rating_key:
            key = f"rating:{rating_key}"
        elif file_path and file_path.lower() in file_to_key:
            key = file_to_key[file_path.lower()]
        elif title and title.lower() in title_to_key:
            key = title_to_key[title.lower()]
        elif file_path:
            key = f"file:{file_path.lower()}"
        else:
            key = f"title:{title.lower()}"

        if key in canonical:
            continue

        canonical[key] = {
            "media_key": key,
            "rating_key": rating_key,
            "title": title,
            "file_path": file_path,
            "total_plays": int(row["total_plays"] or 0),
            "server_status": str(row["server_status"] or ""),
            "status": str(row["status"] or ""),
            "first_seen_at": str(row["first_seen_at"] or ""),
            "last_seen_at": str(row["last_seen_at"] or ""),
        }

        if file_path and file_path.lower() not in file_to_key:
            file_to_key[file_path.lower()] = key
        if title and title.lower() not in title_to_key:
            title_to_key[title.lower()] = key

    conn.execute("DELETE FROM media_state")
    for record in canonical.values():
        conn.execute(
            """
            INSERT INTO media_state (
                media_key, rating_key, title, file_path, total_plays, server_status, status, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["media_key"],
                record["rating_key"],
                record["title"],
                record["file_path"],
                record["total_plays"],
                record["server_status"],
                record["status"],
                record["first_seen_at"],
                record["last_seen_at"],
            ),
        )


def resolve_existing_media_key(conn: sqlite3.Connection, record: Dict[str, Any]) -> Optional[str]:
    rating_key = str(record.get("rating_key", "") or "").strip()
    file_path = str(record.get("file_path", "") or "").strip()
    title = str(record.get("title", "") or "").strip()

    if file_path:
        row = conn.execute("SELECT media_key FROM media_state WHERE lower(file_path) = lower(?) LIMIT 1", (file_path,)).fetchone()
        if row:
            return str(row["media_key"])

    if rating_key and not file_path:
        row = conn.execute("SELECT media_key FROM media_state WHERE rating_key = ? LIMIT 1", (rating_key,)).fetchone()
        if row:
            return str(row["media_key"])

    if title:
        row = conn.execute("SELECT media_key FROM media_state WHERE lower(title) = lower(?) LIMIT 1", (title,)).fetchone()
        if row:
            return str(row["media_key"])

    return None


def get_media_key(row: Dict[str, Any]) -> str:
    rating_key = str(row.get("RatingKey", "") or "").strip()
    file_path = str(row.get("FilePath", "") or "").strip()
    title = str(row.get("Title", "") or "").strip()

    if rating_key and file_path:
        return f"rating:{rating_key}|file:{file_path.lower()}"
    if rating_key:
        return f"rating:{rating_key}"
    if file_path:
        return f"file:{file_path.lower()}"
    return f"title:{title.lower()}"


def dataframe_to_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for _, row in df.reset_index(drop=True).iterrows():
        payload = {
            "RatingKey": str(row.get("RatingKey", "") or ""),
            "Title": str(row.get("Title", "")),
            "FilePath": str(row.get("FilePath", "") or ""),
            "TotalPlays": int(row.get("TotalPlays", 0) or 0),
            "ServerStatus": str(row.get("ServerStatus", "") or ""),
            "Status": str(row.get("Status", "")),
        }
        if not payload["ServerStatus"]:
            payload["ServerStatus"] = "Played" if payload["TotalPlays"] > 0 else "Unwatched"
        records.append(
            {
                "media_key": get_media_key(payload),
                "rating_key": payload["RatingKey"],
                "title": payload["Title"],
                "file_path": payload["FilePath"],
                "total_plays": payload["TotalPlays"],
                "server_status": payload["ServerStatus"],
                "status": payload["Status"],
            }
        )
    return records


def calculate_metrics_from_df(df: pd.DataFrame) -> ScanMetrics:
    if df.empty:
        return ScanMetrics()

    status_series = df["Status"].fillna("").astype(str)
    total_plays = pd.to_numeric(df["TotalPlays"], errors="coerce").fillna(0).astype(int)

    played_statuses = {"Played"}
    unwatched_statuses = {"Unwatched", "Queued for delete", "Keep"}
    disk_only_statuses = {"Not in Plex"}

    played_by_status = status_series.isin(played_statuses)
    unwatched_by_status = status_series.isin(unwatched_statuses)
    disk_only_by_status = status_series.isin(disk_only_statuses)
    undecided = ~(played_by_status | unwatched_by_status | disk_only_by_status)

    total_media = len(df)
    scanned_media = int((~status_series.str.startswith("Error:") & ~disk_only_by_status).sum())
    played_media = int((played_by_status | (undecided & (total_plays > 0))).sum())
    unwatched_media = int((unwatched_by_status | (undecided & (total_plays == 0))).sum())
    queued_for_delete = int((status_series == "Queued for delete").sum())
    deleted_media = int((status_series == "Deleted").sum())
    errors = int(
        (
            status_series.str.startswith("Error:")
            | status_series.isin(["Delete failed", "Not found"])
        ).sum()
    )

    return ScanMetrics(
        total_media=total_media,
        scanned_media=scanned_media,
        played_media=played_media,
        unwatched_media=unwatched_media,
        queued_for_delete=queued_for_delete,
        deleted_media=deleted_media,
        disk_only_media=int(disk_only_by_status.sum()),
        errors=errors,
    )


def upsert_media_state(results_df: pd.DataFrame) -> None:
    conn = get_db_connection()
    now = datetime.now(timezone.utc).isoformat()

    try:
        for record in dataframe_to_records(results_df):
            desired_key = str(record["media_key"])
            existing_key = resolve_existing_media_key(conn, record)
            if existing_key and existing_key != desired_key:
                conn.execute(
                    """
                    UPDATE media_state
                    SET media_key = ?
                    WHERE media_key = ?
                    """,
                    (desired_key, existing_key),
                )

            conn.execute(
                """
                INSERT INTO media_state (
                    media_key, rating_key, title, file_path, total_plays, server_status, status, first_seen_at, last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(media_key) DO UPDATE SET
                    rating_key = excluded.rating_key,
                    title = excluded.title,
                    file_path = excluded.file_path,
                    total_plays = excluded.total_plays,
                    server_status = excluded.server_status,
                    status = excluded.status,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    desired_key,
                    record["rating_key"],
                    record["title"],
                    record["file_path"],
                    record["total_plays"],
                    record["server_status"],
                    record["status"],
                    now,
                    now,
                ),
            )

        normalize_media_state(conn)
        conn.commit()
    finally:
        conn.close()


def save_scan_result(
    base_url: str,
    library_id: str,
    delete_unwatched: bool,
    preview_delete: bool,
    metrics: ScanMetrics,
    results_df: pd.DataFrame,
) -> int:
    _ = (base_url, library_id, delete_unwatched, preview_delete, metrics)
    upsert_media_state(results_df)
    return 1


def update_scan_result(scan_id: int, metrics: ScanMetrics, results_df: pd.DataFrame) -> None:
    _ = (scan_id, metrics)
    upsert_media_state(results_df)


def log_delete_actions(scan_id: int, actions: List[Dict[str, str]]) -> None:
    _ = (scan_id, actions)


def get_latest_scan_id() -> Optional[int]:
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT COUNT(*) AS item_count FROM media_state").fetchone()
        if not row:
            return None
        return 1 if int(row["item_count"]) > 0 else None
    finally:
        conn.close()


def load_latest_scan_into_session() -> Tuple[bool, str]:
    latest_scan_id = get_latest_scan_id()
    if latest_scan_id is None:
        return False, "No scans were found in the database yet."

    loaded_df, loaded_metrics, loaded_queue = load_scan_result(latest_scan_id)
    st.session_state.results_df = loaded_df
    st.session_state.metrics = loaded_metrics
    st.session_state.delete_queue = loaded_queue
    st.session_state.current_scan_id = 1
    st.session_state.last_scan_ok = True
    return True, f"Loaded media state from {DB_PATH}."


def load_scan_result(scan_id: int) -> Tuple[pd.DataFrame, ScanMetrics, List[Dict[str, Any]]]:
    _ = scan_id
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT rating_key, title, file_path, total_plays, server_status, status
            FROM media_state
            ORDER BY title ASC
            """,
        ).fetchall()

        df_rows = [
            {
                "RatingKey": row["rating_key"],
                "Title": row["title"],
                "FilePath": row["file_path"],
                "TotalPlays": row["total_plays"],
                "ServerStatus": row["server_status"],
                "Status": row["status"],
            }
            for row in rows
        ]
        df = pd.DataFrame(df_rows, columns=["RatingKey", "Title", "FilePath", "TotalPlays", "ServerStatus", "Status"])
        metrics = calculate_metrics_from_df(df)

        queue: List[Dict[str, Any]] = []
        for idx, row in enumerate(df_rows):
            if row.get("Status") == "Queued for delete":
                queue.append(
                    {
                        "Title": row.get("Title", ""),
                        "FilePath": row.get("FilePath", ""),
                        "RowIndex": idx,
                    }
                )

        return df, metrics, queue
    finally:
        conn.close()


def build_delete_queue_from_df(df: pd.DataFrame) -> List[Dict[str, Any]]:
    queue: List[Dict[str, Any]] = []

    if df.empty:
        return queue

    for idx, row in df.reset_index(drop=True).iterrows():
        if str(row.get("Status", "")) == "Queued for delete":
            queue.append(
                {
                    "Title": str(row.get("Title", "")),
                    "FilePath": str(row.get("FilePath", "") or ""),
                    "RowIndex": int(idx),
                }
            )

    return queue


def add_override_column(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        out = df.copy()
        out["Override"] = []
        return out

    out = df.copy()
    server = out["ServerStatus"].fillna("").astype(str).str.strip()
    local = out["Status"].fillna("").astype(str).str.strip()
    out["Override"] = (server != "") & (local != "") & (server != local)
    return out


def test_connection(base_url: str, api_key: str) -> Dict[str, Any]:
    return tautulli_get(base_url, api_key, "get_server_info")


def get_file_paths(meta: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    top_level_path = str(meta.get("file") or "").strip()
    if top_level_path:
        paths.append(top_level_path)

    for media in meta.get("media_info") or []:
        for part in (media or {}).get("parts") or []:
            path = str((part or {}).get("file") or "").strip()
            if path:
                paths.append(path)

    return list(dict.fromkeys(paths))


def normalize_file_path(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def get_disk_video_paths(folder: str) -> List[str]:
    folder = (folder or "").strip()
    if not folder:
        return []
    if not os.path.isdir(folder):
        raise ValueError(f"Media folder is unavailable: {folder}")

    paths: List[str] = []
    for root, _, files in os.walk(folder):
        for filename in files:
            if os.path.splitext(filename)[1].lower() in VIDEO_EXTENSIONS:
                paths.append(os.path.join(root, filename))
    return paths


def get_all_items(base_url: str, api_key: str, section_id: str) -> List[Dict[str, Any]]:
    data = tautulli_get(
        base_url,
        api_key,
        "get_library_media_info",
        section_id=section_id,
        length=-1,
    )

    # Tautulli returns list under response.data.data for this command.
    return data.get("data", []) or []


def scan_library(
    base_url: str,
    api_key: str,
    section_id: str,
    media_folder: str,
    delete_unwatched: bool,
    preview_delete: bool,
) -> Tuple[pd.DataFrame, ScanMetrics, List[Dict[str, Any]]]:
    all_items = get_all_items(base_url, api_key, section_id)
    total = len(all_items)

    progress = st.progress(0, text="Starting scan...")
    status_placeholder = st.empty()

    rows: List[Dict[str, Any]] = []
    queue: List[Dict[str, Any]] = []
    metrics = ScanMetrics(total_media=total)

    for idx, item in enumerate(all_items, start=1):
        rating_key = item.get("rating_key")
        fallback_title = item.get("title", "Unknown title")

        try:
            meta = tautulli_get(base_url, api_key, "get_metadata", rating_key=rating_key)
            title = meta.get("title") or fallback_title
            file_paths = get_file_paths(meta)

            if not file_paths:
                rows.append(
                    {
                        "RatingKey": str(rating_key or ""),
                        "Title": title,
                        "FilePath": "",
                        "TotalPlays": 0,
                        "ServerStatus": "Unknown",
                        "Status": "Skipped: missing file path",
                    }
                )
                continue

            history = tautulli_get(base_url, api_key, "get_history", rating_key=rating_key)
            history_rows = history.get("data") or []
            total_plays = len(history_rows)

            media_file_count = len(file_paths)
            metrics.scanned_media += media_file_count

            if total_plays > 0:
                metrics.played_media += media_file_count
                server_status = "Played"
            else:
                metrics.unwatched_media += media_file_count
                server_status = "Unwatched"

            for file_path in file_paths:
                status = "Played" if total_plays > 0 else "Unwatched"
                if total_plays == 0 and delete_unwatched and preview_delete:
                    status = "Queued for delete"
                    queue.append(
                        {
                            "Title": title,
                            "FilePath": file_path,
                            "RowIndex": len(rows),
                        }
                    )
                    metrics.queued_for_delete += 1
                elif total_plays == 0 and delete_unwatched:
                    if os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                            status = "Deleted"
                            metrics.deleted_media += 1
                        except OSError:
                            status = "Delete failed"
                            metrics.errors += 1
                    else:
                        status = "Not found"
                        metrics.errors += 1

                rows.append(
                    {
                        "RatingKey": str(rating_key or ""),
                        "Title": title,
                        "FilePath": file_path,
                        "TotalPlays": total_plays,
                        "ServerStatus": server_status,
                        "Status": status,
                    }
                )
        except Exception as ex:
            metrics.errors += 1
            rows.append(
                {
                    "RatingKey": str(rating_key or ""),
                    "Title": fallback_title,
                    "FilePath": "",
                    "TotalPlays": 0,
                    "ServerStatus": "Unknown",
                    "Status": f"Error: {ex}",
                }
            )

        percent = int((idx / total) * 100) if total else 100
        progress.progress(percent, text=f"Scanning item {idx} / {total}")
        status_placeholder.info(
            f"Scanned: {metrics.scanned_media} | Played: {metrics.played_media} | "
            f"Unwatched: {metrics.unwatched_media} | Queued: {metrics.queued_for_delete}"
        )

    progress.progress(100, text="Scan complete")
    status_placeholder.success("Scan finished.")

    known_paths = {
        normalize_file_path(str(row["FilePath"]))
        for row in rows
        if row["FilePath"]
    }
    for file_path in get_disk_video_paths(media_folder):
        if normalize_file_path(file_path) not in known_paths:
            rows.append(
                {
                    "RatingKey": "",
                    "Title": os.path.splitext(os.path.basename(file_path))[0],
                    "FilePath": file_path,
                    "TotalPlays": 0,
                    "ServerStatus": "Not in Plex",
                    "Status": "Not in Plex",
                }
            )
            metrics.disk_only_media += 1

    metrics.total_media = sum(1 for row in rows if row["FilePath"])
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(by=["Title"], ascending=True, na_position="last").reset_index(drop=True)

    return df, metrics, queue


def delete_queued_files(df: pd.DataFrame, queue: List[Dict[str, Any]]) -> Tuple[pd.DataFrame, int, int, List[Dict[str, str]]]:
    deleted = 0
    failed = 0
    actions: List[Dict[str, str]] = []

    for item in queue:
        path = item.get("FilePath", "")
        row_index = item.get("RowIndex")
        status = "Deleted"

        try:
            if path and os.path.exists(path):
                os.remove(path)
                deleted += 1
            else:
                status = "Not found"
                failed += 1
        except OSError:
            status = "Delete failed"
            failed += 1

        if row_index is not None and 0 <= row_index < len(df):
            df.at[row_index, "Status"] = status

        actions.append(
            {
                "Title": str(item.get("Title", "")),
                "FilePath": str(path),
                "Result": status,
            }
        )

    return df, deleted, failed, actions


def apply_custom_style() -> None:
    st.markdown(
        """
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap');

            :root {
                --bg-soft: #f3f5f9;
                --card-bg: #ffffff;
                --ink: #111827;
                --muted: #334155;
                --accent: #0f766e;
                --accent-2: #0ea5e9;
                --warn: #f59e0b;
            }

            @media (prefers-color-scheme: dark) {
                :root {
                    --bg-soft: #0b1220;
                    --card-bg: #0f172a;
                    --ink: #e5e7eb;
                    --muted: #cbd5e1;
                    --accent: #14b8a6;
                    --accent-2: #38bdf8;
                    --warn: #fbbf24;
                }
            }

            .stApp {
                background: radial-gradient(circle at 10% 10%, #e0f2fe 0%, #f8fafc 45%, #ecfeff 100%);
                color: var(--ink);
                font-family: 'Space Grotesk', 'Segoe UI', sans-serif;
            }

            @media (prefers-color-scheme: dark) {
                .stApp {
                    background: radial-gradient(circle at 10% 10%, #0f172a 0%, #0b1220 50%, #07121d 100%);
                }
            }

            .stApp,
            .stApp p,
            .stApp label,
            .stApp h1,
            .stApp h2,
            .stApp h3,
            .stApp h4,
            .stApp h5,
            .stApp h6,
            .stCaption {
                color: var(--ink);
            }

            [data-testid='stMetricValue'],
            [data-testid='stMetricLabel'],
            .stDataFrame,
            .stTable,
            .stMarkdown,
            .stAlert {
                color: var(--ink);
            }

            .hero {
                padding: 1.1rem 1.25rem;
                border-radius: 14px;
                background: linear-gradient(120deg, #0f766e 0%, #0ea5e9 100%);
                color: white;
                box-shadow: 0 12px 30px rgba(14, 165, 233, 0.18);
                margin-bottom: 0.8rem;
            }

            .hero h2 {
                margin: 0;
                font-size: 1.5rem;
            }

            .hero p {
                margin: 0.35rem 0 0 0;
                opacity: 0.95;
            }

            .hint {
                color: var(--muted);
                font-size: 0.95rem;
                margin-top: 0.15rem;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def init_state() -> None:
    defaults = {
        "results_df": pd.DataFrame(columns=["RatingKey", "Title", "FilePath", "TotalPlays", "ServerStatus", "Status"]),
        "metrics": ScanMetrics(),
        "delete_queue": [],
        "last_scan_ok": False,
        "current_scan_id": None,
        "db_bootstrap_error": None,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def render_maintenance(raw_url, api_key):
    init_state()
    try:
        init_db()
    except sqlite3.Error:
        st.error("Maintenance database unavailable. Check local permissions and database integrity.")
        return
    should_reload_from_db = st.session_state.results_df.empty

    if should_reload_from_db:
        try:
            ok, message = load_latest_scan_into_session()
            st.session_state.db_bootstrap_error = None if ok else message
        except Exception as ex:
            # Keep app usable even if saved history is unavailable/corrupt.
            st.session_state.db_bootstrap_error = str(ex)

    st.title("Maintenance")
    if st.session_state.db_bootstrap_error:
        st.warning("Could not load saved maintenance state. Check the local SQLite database.")
    with st.sidebar:
        library_id = st.text_input("Library Section ID", value=DEFAULT_LIBRARY_ID)
        media_folder = st.text_input("Media folder", value=DEFAULT_MEDIA_FOLDER, help="Recursively scans AVI, MKV, MP4, MPG, and RMVB files. Files not in Plex are shown but cannot be deleted.")

        st.header("Scan Options")
        delete_unwatched = st.toggle("Delete unwatched files", value=False)
        preview_delete = st.toggle("Preview deletions before removing", value=True, disabled=not delete_unwatched)
        reload_latest_clicked = st.button("Reload latest from database", width="stretch")

        test_clicked = st.button("Test Connection", width="stretch")
        scan_clicked = st.button("Scan Library", width="stretch", type="primary")

    if reload_latest_clicked:
        try:
            ok, message = load_latest_scan_into_session()
            if ok:
                st.success(message)
            else:
                st.info(message)
        except Exception as ex:
            st.error(f"Failed to reload from database: {ex}")

    if test_clicked:
        try:
            base_url = normalize_base_url(raw_url)
            info = test_connection(base_url, api_key)
            tautulli_version = (
                info.get("tautulli_version")
                or info.get("version")
                or info.get("tautulli_branch")
                or "Unavailable"
            )
            st.success(f"Connected to Tautulli at {base_url}")
            st.caption(f"Server: {tautulli_version}")
        except Exception as ex:
            st.error(f"Connection failed: {ex}")

    if scan_clicked:
        try:
            base_url = normalize_base_url(raw_url)
            test_connection(base_url, api_key)
            with prevent_system_sleep():
                df, metrics, queue = scan_library(
                    base_url=base_url,
                    api_key=api_key,
                    section_id=library_id,
                    media_folder=media_folder,
                    delete_unwatched=delete_unwatched,
                    preview_delete=preview_delete,
                )

            st.session_state.results_df = df
            st.session_state.metrics = metrics
            st.session_state.delete_queue = queue
            st.session_state.current_scan_id = save_scan_result(
                base_url=base_url,
                library_id=library_id,
                delete_unwatched=delete_unwatched,
                preview_delete=preview_delete,
                metrics=metrics,
                results_df=df,
            )
            st.session_state.last_scan_ok = True
        except Exception as ex:
            st.session_state.last_scan_ok = False
            st.error(f"Scan failed: {ex}")

    metrics: ScanMetrics = st.session_state.metrics
    col1, col2, col3, col4, col5, col6, col7 = st.columns(7)
    col1.metric("Total media", metrics.total_media)
    col2.metric("Scanned media", metrics.scanned_media)
    col3.metric("Played media", metrics.played_media)
    col4.metric("Unwatched media", metrics.unwatched_media)
    col5.metric("Queued for delete", metrics.queued_for_delete)
    col6.metric("Deleted", metrics.deleted_media)
    col7.metric("Not in Plex", metrics.disk_only_media)

    st.markdown(
        "<p class='hint'>Total media and played media are expected to be different unless every item has been watched.</p>",
        unsafe_allow_html=True,
    )

    if st.session_state.delete_queue and delete_unwatched and preview_delete:
        st.warning(f"{len(st.session_state.delete_queue)} file(s) are queued for deletion from the last scan.")
        if st.button("Confirm Delete Queued Files", type="secondary"):
            updated_df, deleted, failed, actions = delete_queued_files(
                st.session_state.results_df.copy(),
                st.session_state.delete_queue,
            )
            st.session_state.results_df = updated_df
            st.session_state.delete_queue = []
            st.session_state.metrics = calculate_metrics_from_df(st.session_state.results_df)
            update_scan_result(1, st.session_state.metrics, st.session_state.results_df)
            log_delete_actions(1, actions)
            st.success(f"Delete complete. Deleted {deleted} file(s). Failed or missing: {failed}.")

    st.subheader("Results")

    results_df: pd.DataFrame = st.session_state.results_df.copy()
    if results_df.empty:
        st.info("Run a scan to view results.")
    else:
        status_options = sorted(
            set(results_df["Status"].dropna().astype(str).tolist())
            | {"Played", "Unwatched", "Queued for delete", "Deleted", "Keep", "Not found", "Delete failed"}
        )

        st.caption("You can manually edit Status. Set a row to 'Keep' (or anything except 'Queued for delete') to prevent deletion.")
        display_df = add_override_column(results_df)
        editable_df = st.data_editor(
            display_df,
            width="stretch",
            hide_index=True,
            disabled=["RatingKey", "Title", "FilePath", "TotalPlays", "ServerStatus", "Override"],
            column_config={
                "RatingKey": None,
                "ServerStatus": st.column_config.TextColumn("ServerStatus", help="Derived from Plex/Tautulli scan data."),
                "Override": st.column_config.CheckboxColumn("Override", help="True means local Status differs from ServerStatus."),
                "Status": st.column_config.SelectboxColumn(
                    "Status",
                    options=status_options,
                    required=True,
                )
            },
            key="status_editor",
        )

        if st.button("Apply Status Changes", type="secondary"):
            updated_df = editable_df.drop(columns=["Override"], errors="ignore").copy().reset_index(drop=True)
            st.session_state.results_df = updated_df
            st.session_state.delete_queue = build_delete_queue_from_df(updated_df)
            st.session_state.metrics = calculate_metrics_from_df(st.session_state.results_df)

            update_scan_result(1, st.session_state.metrics, st.session_state.results_df)

            st.success("Status updates applied.")

        results_df = st.session_state.results_df.copy()

        filter_col1, filter_col2, filter_col3 = st.columns([2, 2, 2])
        search = filter_col1.text_input("Search title/path/status", value="")
        all_statuses = sorted(results_df["Status"].dropna().unique().tolist())
        chosen_statuses = filter_col2.multiselect("Filter status", options=all_statuses, default=all_statuses)
        sort_by = filter_col3.selectbox("Sort by", options=["Title A-Z", "Title Z-A", "Plays High-Low", "Plays Low-High", "Status"])

        if search.strip():
            pattern = search.strip().lower()
            results_df = results_df[
                results_df["Title"].fillna("").str.lower().str.contains(pattern)
                | results_df["FilePath"].fillna("").str.lower().str.contains(pattern)
                | results_df["Status"].fillna("").str.lower().str.contains(pattern)
            ]

        if chosen_statuses:
            results_df = results_df[results_df["Status"].isin(chosen_statuses)]

        if sort_by == "Title A-Z":
            results_df = results_df.sort_values(by=["Title"], ascending=True)
        elif sort_by == "Title Z-A":
            results_df = results_df.sort_values(by=["Title"], ascending=False)
        elif sort_by == "Plays High-Low":
            results_df = results_df.sort_values(by=["TotalPlays", "Title"], ascending=[False, True])
        elif sort_by == "Plays Low-High":
            results_df = results_df.sort_values(by=["TotalPlays", "Title"], ascending=[True, True])
        elif sort_by == "Status":
            results_df = results_df.sort_values(by=["Status", "Title"], ascending=[True, True])

        st.dataframe(
            add_override_column(results_df),
            width="stretch",
            hide_index=True,
            column_config={
                "RatingKey": None,
                "Override": st.column_config.CheckboxColumn("Override"),
            },
        )

        chart_data = pd.DataFrame(
            {
                "Category": ["Played", "Unwatched"],
                "Count": [metrics.played_media, metrics.unwatched_media],
            }
        )
        st.bar_chart(chart_data.set_index("Category"))


def main():
    st.set_page_config(page_title="Plex Control Center", page_icon=":film_projector:", layout="wide")
    apply_custom_style()
    with st.sidebar:
        page = st.radio("Navigation", ["Overview", "Now Playing", "History", "Users", "Devices", "Transcoding", "Bandwidth", "Maintenance"])
        st.header("Tautulli Connection")
        raw_url = st.text_input("Tautulli URL or Host", value=DEFAULT_BASE_URL)
        api_key = st.text_input("API Key", value=DEFAULT_API_KEY, type="password")
    if page == "Maintenance":
        render_maintenance(raw_url, api_key)
    elif page in ('History', 'Users', 'Devices', 'Transcoding', 'Bandwidth'):
        render_analytics(page, raw_url, api_key)
    else:
        render_live_dashboard(page, raw_url, api_key)


if __name__ == "__main__":
    main()
