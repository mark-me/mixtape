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
    """Manages extraction and synchronization of music metadata from a file system.

    Handles scanning, indexing, and monitoring of a music collection, storing metadata in a SQLite database for efficient access and updates.
    """
    SUPPORTED_EXTS = {".mp3", ".flac", ".ogg", ".oga", ".m4a", ".mp4", ".wav", ".wma"}

    def __init__(self, music_root: Path, db_path: Path):
        """Initializes a CollectionExtractor for managing a music library.

        Sets up the music root directory, database path, and prepares the database schema for storing music metadata.

        Args:
            music_root (Path): The root directory containing music files.
            db_path (Path): Path to the SQLite database file.
        """
        self.music_root = music_root.resolve()
        self.db_path = (db_path or (self.music_root.parent / "collection-data" / "music.db")).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._stop_event = Event()
        self._observer: Observer | None = None

        self._ensure_schema()

    def get_conn(self) -> sqlite3.Connection:
        """Creates and returns a new SQLite database connection.

        Opens a connection to the music collection database and sets the row factory for named access. Returns the connection object for use in database operations.

        Returns:
            sqlite3.Connection: A connection object to the music collection database.
        """
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        """Ensures the database schema for the music collection exists.

        Creates the tracks table and necessary indexes if they do not already exist in the database. Adds the mtime column for sync checking if it is missing.

        Returns:
            None
        """
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
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute("ALTER TABLE tracks ADD COLUMN mtime REAL")

    def count_tracks(self) -> int:
        """Returns the total number of tracks in the music collection.

        Counts and returns the number of track records currently stored in the database.

        Returns:
            int: The total number of tracks in the collection.
        """
        with self.get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]

    def is_synced_with_filesystem(self, sample_size: int = 200) -> bool:
        """Checks if the database is in sync with the file system.

        Randomly samples tracks from the database and verifies that each file exists and its modification time matches the stored value.
        Returns True if all sampled files are in sync, otherwise False.

        Args:
            sample_size (int): The number of tracks to sample for sync checking.

        Returns:
            bool: True if the sampled files are in sync with the database, False otherwise.
        """
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
        """Synchronizes the database with the current state of the file system.

        Compares the database records with the files present in the music directory, adding new files and removing records for deleted files.
        Logs the number of tracks added and removed.

        Returns:
            None
        """
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

    def rebuild(self) -> None:
        """Scans and reindexes the entire music collection.

        Removes all existing track records and rebuilds the index from the music root directory. Prints progress and summary information to the console.

        Returns:
            None
        """
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

    def _index_file(self, conn: sqlite3.Connection, path: Path) -> None:
        """Indexes a single music file and updates the database with its metadata.

        Extracts metadata from the given file and inserts or updates the corresponding record in the tracks table. If metadata extraction fails, the file is skipped.

        Args:
            conn: The SQLite database connection.
            path: The path to the music file to index.

        Returns:
            None
        """
        tag = None
        try:
            tag = TinyTag.get(path, tags=True, duration=True)
        except Exception as e:
            logger.warning(
                f"Failed to extract tags from {path}: {type(e).__name__}: {e}",
                exc_info=True
            )

        artist = self._extract_artist(tag, path)
        album = self._extract_album(tag, path)
        title = self._extract_title(tag, path)
        year = self._safe_int_year(getattr(tag, "year", None))
        duration = getattr(tag, "duration", None)
        albumartist = getattr(tag, "albumartist", None)
        genre = getattr(tag, "genre", None)
        mtime = path.stat().st_mtime

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
            albumartist,
            genre,
            year,
            duration,
            mtime,
        ))

    def _safe_int_year(self, value):
        """Converts a value to an integer year if possible.

        Attempts to extract and return a valid integer year from the input value. Returns None if the value cannot be interpreted as a year.

        Args:
            value: The value to convert to an integer year.

        Returns:
            int or None: The extracted year as an integer, or None if conversion is not possible.
        """
        if not value:
            return None
        try:
            return int(str(value).strip().split("-", 1)[0].split(".", 1)[0])
        except ValueError:
            return None

    def _extract_artist(self, tag, path: Path) -> str:
        """Extracts the artist name from tag or path.

        Returns the artist name as a string, using tag metadata or directory names as fallback. If no artist is found, returns 'Unknown'.

        Args:
            tag: The metadata tag object from TinyTag.
            path: The path to the music file.

        Returns:
            str: The extracted artist name.
        """
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
        """Starts live monitoring of the music directory for file system changes.

        Sets up a file system observer to watch for changes in the music collection and updates the database in real time.

        Returns:
            None
        """
        if self._observer is not None:
            return

        self._observer = Observer()
        self._observer.schedule(Watcher(self), str(self.music_root), recursive=True)
        self._observer.start()
        logger.info("Live filesystem monitoring started")

    def stop_monitoring(self):
        """Stops live monitoring of the music directory for file system changes.

        Shuts down the file system observer and releases associated resources if monitoring is active.

        Returns:
            None
        """
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None
            logger.info("Filesystem monitoring stopped")

class Watcher(FileSystemEventHandler):
    """Handles file system events for the music collection and updates the database.

    Monitors file changes in the music directory and synchronizes the music database by adding, updating, or removing track records as needed.
    """
    def __init__(self, extractor):
        """Initializes a Watcher to handle file system events for a music collection.

        Associates the watcher with a CollectionExtractor instance to synchronize the database with file changes.

        Args:
            extractor: The CollectionExtractor instance to monitor and update.
        """
        self.extractor = extractor

    def on_any_event(self, event):
        """Handles any file system event for the music collection.

        Responds to file creation, modification, movement, or deletion events by updating the music database accordingly. Skips directories and unsupported file types.

        Args:
            event: The file system event to handle.

        Returns:
            None
        """
        if event.is_directory:
            return
        path = Path(event.src_path if hasattr(event, "src_path") else event.dest_path)
        if path.suffix.lower() not in self.extractor.SUPPORTED_EXTS:
            return

        with self.get_conn() as conn:
            if event.event_type in ("deleted", "moved") and hasattr(event, "src_path"):
                conn.execute("DELETE FROM tracks WHERE path = ?", (event.src_path,))
            elif path.exists():
                try:
                    self._index_file(conn, path)
                except Exception as e:
                    logger.error(f"Error indexing file {path}: {e}", exc_info=True)
            conn.commit()