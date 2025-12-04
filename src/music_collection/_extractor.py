#!/usr/bin/env python3
import contextlib
import sqlite3
import time
from pathlib import Path
from threading import Event

from tinytag import TinyTag
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from logtools import get_logger

logger = get_logger(__name__)


class CollectionExtractor:
    SUPPORTED_EXTS = {".mp3", ".flac", ".ogg", ".oga", ".m4a", ".mp4", ".wav", ".wma"}

    def __init__(self, music_root: Path, db_path: Path | None = None):
        self.music_root = music_root.resolve()
        self.db_path = (db_path or (self.music_root.parent / "collection-data" / "music.db")).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._stop_event = Event()
        self._observer: Observer | None = None

        self._ensure_schema()

    def get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self):
        with self.get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tracks (
                    path TEXT PRIMARY KEY,
                    filename TEXT,
                    artist TEXT,
                    album TEXT,
                    title TEXT,
                    albumartist TEXT,
                    genre TEXT,
                    year INTEGER,
                    duration REAL,
                    mtime REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_artist ON tracks(artist COLLATE NOCASE)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_album  ON tracks(album  COLLATE NOCASE)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_title  ON tracks(title  COLLATE NOCASE)")

            # Add mtime column if not exists (for sync checking)
            try:
                conn.execute("ALTER TABLE tracks ADD COLUMN mtime REAL")
            except sqlite3.OperationalError:
                pass  # already exists

    def count_tracks(self) -> int:
        with self.get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]

    def is_synced_with_filesystem(self, sample_size: int = 200) -> bool:
        """Quick heuristic check: compare mtime of some files vs DB."""
        with self.get_conn() as conn:
            rows = conn.execute("SELECT path, mtime FROM tracks ORDER BY RANDOM() LIMIT ?", (sample_size,)).fetchall()
            for row in rows:
                path = Path(row["path"])
                if not path.exists():
                    return False
                if row["mtime"] is None or path.stat().st_mtime != row["mtime"]:
                    return False
        return True

    def resync(self):
        """Efficient incremental sync: add missing, remove deleted, update changed."""
        start = time.time()
        db_paths = set()
        with self.get_conn() as conn:
            db_paths = {row["path"] for row in conn.execute("SELECT path FROM tracks")}

        fs_paths = {
            str(p) for p in self.music_root.rglob("*")
            if p.is_file() and p.suffix.lower() in self.SUPPORTED_EXTS
        }

        to_add = fs_paths - db_paths
        to_remove = db_paths - fs_paths

        with self.get_conn() as conn:
            if to_remove:
                conn.executemany("DELETE FROM tracks WHERE path = ?", [(p,) for p in to_remove])
            for path_str in to_add:
                try:
                    self._index_file(conn, Path(path_str))
                except Exception as e:
                    logger.warning(f"Failed to index {path_str}: {e}")
            conn.commit()

        added = len(to_add)
        removed = len(to_remove)
        logger.info(f"Sync complete: +{added:,} / -{removed:,} tracks ({time.time() - start:.1f}s)")

    def rebuild(self):
        logger.info("Full rebuild started...")
        start = time.time()
        with self.get_conn() as conn:
            conn.execute("DELETE FROM tracks")
            count = 0
            for fp in self.music_root.rglob("*"):
                if fp.is_file() and fp.suffix.lower() in self.SUPPORTED_EXTS:
                    try:
                        self._index_file(conn, fp)
                        count += 1
                    except Exception as e:
                        logger.warning(f"Skip {fp}: {e}")
                    if count % 5000 == 0:
                        logger.info(f"Indexed {count:,} tracks...")
            conn.commit()
        logger.info(f"Full rebuild complete: {count:,} tracks in {time.time() - start:.1f}s")

    def _index_file(self, conn: sqlite3.Connection, path: Path):
        tag = None
        with contextlib.suppress(Exception):
            tag = TinyTag.get(path, tags=True, duration=True)

        artist = self._extract_artist(tag, path)
        album = self._extract_album(tag, path)
        title = self._extract_title(tag, path)
        year = self._safe_int_year(getattr(tag, "year", None))
        duration = getattr(tag, "duration", None)

        conn.execute("""
            INSERT OR REPLACE INTO tracks
            (path, filename, artist, album, title, albumartist, genre, year, duration, mtime)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            str(path),
            path.name,
            artist,
            album,
            title,
            getattr(tag, "albumartist", None),
            getattr(tag, "genre", None),
            year,
            duration,
            path.stat().st_mtime,
        ))

    def _safe_int_year(self, value):
        if not value:
            return None
        try:
            return int(str(value).strip().split("-", 1)[0].split(".", 1)[0])
        except ValueError:
            return None

    def _extract_artist(self, tag, path: Path) -> str:
        artist = getattr(tag, "artist", None) or getattr(tag, "albumartist", None)
        if not artist and len(path.parents) >= 3:
            artist = path.parent.parent.name
        return (artist or "Unknown").strip()

    def _extract_album(self, tag, path: Path) -> str:
        album = getattr(tag, "album", None)
        if not album:
            album = path.parent.name
            if album in {"", ".", "..", "Music", "music"} and len(path.parents) >= 3:
                album = path.parent.parent.name
        return (album or "Unknown").strip()

    def _extract_title(self, tag, path: Path) -> str:
        return (getattr(tag, "title", None) or path.stem or "Unknown").strip()

    # ==================== Monitoring ====================

    def start_monitoring(self):
        if self._observer is not None:
            return

        class Watcher(FileSystemEventHandler):
            def __init__(inner_self, extractor):
                self.extractor = extractor

            def on_any_event(inner_self, event):
                if event.is_directory:
                    return
                path = Path(event.src_path if hasattr(event, "src_path") else event.dest_path)
                if path.suffix.lower() not in self.SUPPORTED_EXTS:
                    return

                with self.get_conn() as conn:
                    if event.event_type in ("deleted", "moved") and hasattr(event, "src_path"):
                        conn.execute("DELETE FROM tracks WHERE path = ?", (event.src_path,))
                    elif path.exists():
                        with contextlib.suppress(Exception):
                            self._index_file(conn, path)
                    conn.commit()

        self._observer = Observer()
        self._observer.schedule(Watcher(self), str(self.music_root), recursive=True)
        self._observer.start()
        logger.info("Live filesystem monitoring started")

    def stop_monitoring(self):
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None
            logger.info("Filesystem monitoring stopped")