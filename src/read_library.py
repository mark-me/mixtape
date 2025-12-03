#!/usr/bin/env python3
import contextlib
import sqlite3
import time
from pathlib import Path
from typing import Any

from tinytag import TinyTag
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from logtools import get_logger

logger = get_logger(__name__)

# ==================== CONFIG ====================
MUSIC_ROOT = Path("/home/mark/Music")
DB_PATH = Path(__file__).parent.parent / "collection-data" / "music.db"
# ===============================================


class MusicCollection:
    def __init__(self, music_root: Path, db_path: Path):
        """Initializes a MusicCollection instance for managing a music library.

        Sets up the music root directory, database path, and supported file types, and ensures the database schema is ready for use.

        Args:
            music_root: The root directory containing music files.
            db_path: The path to the SQLite database file.
        """
        self.music_root = music_root.resolve()
        self.db_path = db_path.resolve()
        self.supported = {
            ".mp3",
            ".flac",
            ".ogg",
            ".oga",
            ".m4a",
            ".mp4",
            ".wav",
            ".wma",
        }
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def get_conn(self) -> sqlite3.Connection:
        """Creates and returns a new SQLite database connection.

        Opens a connection to the music collection database and sets the row factory for named access. Returns the connection object for use in database operations.

        Returns:
            sqlite3.Connection: A connection object to the music collection database.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self):
        """Ensures the database schema for the music collection exists.

        Creates the tracks table and necessary indexes if they do not already exist in the database.

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
                    duration REAL
                )
            """)
            for idx in [
                "CREATE INDEX IF NOT EXISTS idx_artist ON tracks(artist COLLATE NOCASE)",
                "CREATE INDEX IF NOT EXISTS idx_album  ON tracks(album  COLLATE NOCASE)",
                "CREATE INDEX IF NOT EXISTS idx_title  ON tracks(title  COLLATE NOCASE)",
            ]:
                conn.execute(idx)

    def db_ready(self):
        """Checks if the music database exists and contains at least one track.

        Returns True if the database file exists and there is at least one track indexed. Otherwise, returns False.

        Returns:
            bool: True if the database is ready for use, False otherwise.
        """
        return self.db_path.exists() and self.count_tracks() > 0

    def count_tracks(self):
        """Returns the total number of tracks in the music collection.

        Counts and returns the number of track records currently stored in the database.

        Returns:
            int: The total number of tracks in the collection.
        """
        with self.get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]

    def rebuild(self):
        """Scans and reindexes the entire music collection.

        Removes all existing track records and rebuilds the index from the music root directory. Prints progress and summary information to the console.

        Returns:
            None
        """
        logger.info("Scanning and indexing your music collection...")
        self._ensure_schema()
        with self.get_conn() as conn:
            conn.execute("DELETE FROM tracks")
            count = 0
            start = time.time()
            for fp in self.music_root.rglob("*"):
                if fp.is_file() and fp.suffix.lower() in self.supported:
                    try:
                        self._index_file(conn, fp)
                    except Exception as e:
                        logger.warning(f"Skipping {fp.name}: {e}")
                    count += 1
                    if count % 3000 == 0:
                        logger.info(f"Processed {count:,} files...")
            conn.commit()
        logger.info(f"Done! Indexed {count:,} files in {time.time() - start:.1f}s\n")

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
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value) if value == int(value) else None
        s = str(value).strip().split("-", 1)[0].split(".", 1)[0]
        return int(s) if s.isdigit() else None

    def _index_file(self, conn: sqlite3.Connection, path: Path):
        """Indexes a single music file and updates the database with its metadata.

        Extracts metadata from the given file and inserts or updates the corresponding record in the tracks table. If metadata extraction fails, the file is skipped.

        Args:
            conn: The SQLite database connection.
            path: The path to the music file to index.

        Returns:
            None
        """
        tag = None
        with contextlib.suppress(Exception):
            tag = TinyTag.get(path, tags=True, duration=True)
        artist = self._extract_artist(tag, path)
        album = self._extract_album(tag, path)
        title = self._extract_title(tag, path)
        year = self._safe_int_year(getattr(tag, "year", None))
        duration = self._extract_duration(tag)

        conn.execute(
            """INSERT OR REPLACE INTO tracks
            (path, filename, artist, album, title, albumartist, genre, year, duration)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                str(path),
                path.name,
                artist,
                album,
                title,
                getattr(tag, "albumartist", None),
                getattr(tag, "genre", None),
                year,
                duration,
            ),
        )

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
        if not artist:
            # Check if the path is nested at least two levels deep
            if len(path.parents) >= 3:
                parent_dir = path.parent.parent.name
                artist = parent_dir or "Unknown"
            else:
                artist = "Unknown"
        return artist.strip() if artist else "Unknown"

    def _extract_album(self, tag, path: Path) -> str:
        """Extracts the album name from tag or path.

        Returns the album name as a string, using tag metadata or directory names as fallback. If no album is found, returns 'Unknown'.

        Args:
            tag: The metadata tag object from TinyTag.
            path: The path to the music file.

        Returns:
            str: The extracted album name.
        """
        album = getattr(tag, "album", None)
        if not album:
            parent_dir = path.parent.name
            # Validate parent_dir: avoid generic or root directory names
            invalid_album_names = {"", ".", "..", "Music", "music", "Unknown"}
            if parent_dir in invalid_album_names:
                # Try grandparent directory as a fallback
                grandparent_dir = path.parent.parent.name
                album = (
                    grandparent_dir
                    if grandparent_dir not in invalid_album_names
                    else "Unknown"
                )
            else:
                album = parent_dir
        return album.strip() if album else "Unknown"

    def _extract_title(self, tag, path: Path) -> str:
        """Extracts the track title from tag or path.

        Returns the track title as a string, using tag metadata or the file stem as fallback. If no title is found, returns 'Unknown'.

        Args:
            tag: The metadata tag object from TinyTag.
            path: The path to the music file.

        Returns:
            str: The extracted track title.
        """
        title = getattr(tag, "title", None) or path.stem
        return title.strip() if title else "Unknown"

    def _extract_duration(self, tag) -> float:
        """Extracts the duration from the tag metadata.

        Returns the duration as a float if available, otherwise returns None.

        Args:
            tag: The metadata tag object from TinyTag.

        Returns:
            float or None: The extracted duration in seconds, or None if not available.
        """
        duration = None
        if has_duration := tag and getattr(tag, "duration", None):
            with contextlib.suppress(Exception):
                duration = float(tag.duration)
        return duration

    def search_grouped(self, query: str, limit: int = 20):
        """Searches for artists, albums, and tracks matching the given query.

        Returns a dictionary grouping the search results into artists, albums, and tracks. The number of results in each group is limited by the specified limit.

        Args:
            query: The search string to match against artists, albums, and tracks.
            limit: The maximum number of results to return for each group.

        Returns:
            dict: A dictionary with keys 'artists', 'albums', and 'tracks', each containing a list of matching results.
        """
        if not (q := query.strip()):
            return {"artists": [], "albums": [], "tracks": []}

        like_pat = f"%{q}%"
        starts_pat = f"{q}%"

        with self.get_conn() as conn:
            result = {
                "albums": [],
                "tracks": [],
                "artists": self._search_artists(conn, starts_pat, limit),
            }
            result["albums"] = self._search_albums(
                conn, like_pat, starts_pat, limit, result["artists"]
            )
            result["tracks"] = self._search_tracks(
                conn, like_pat, starts_pat, limit, result["artists"], result["albums"]
            )
        return result

    def _search_artists(
        self, conn: sqlite3.Connection, starts_pat: str, limit: int
    ) -> list[dict[str, Any]]:
        """Searches for artists whose names match the given pattern.

        Returns a list of artist dictionaries matching the search criteria. The search is case-insensitive and limited to the specified number of results.

        Args:
            conn: The SQLite database connection.
            starts_pat: The pattern to match artist names that start with the query.
            limit: The maximum number of results to return.

        Returns:
            List[dict]: A list of dictionaries, each containing an artist name.
        """
        cur = conn.execute(
            """
            SELECT DISTINCT artist FROM tracks
            WHERE artist LIKE ? COLLATE NOCASE
            ORDER BY artist LIKE ? DESC, artist COLLATE NOCASE
            LIMIT ?
        """,
            (starts_pat, starts_pat, limit),
        )
        artists = [{"artist": r["artist"]} for r in cur]
        for artist in artists:
            artist.update(
                {
                    "albums": self._search_artist_albums(
                        conn=conn, artist=artist["artist"]
                    )
                }
            )
        return artists

    def _search_artist_albums(self, conn: sqlite3.Connection, artist: str):
        sql = """
                SELECT DISTINCT artist, album FROM tracks
                WHERE artist = ?
                ORDER BY album
            """
        cur = conn.execute(sql, (artist,))
        albums = [{"album": r["album"]} for r in cur]
        for album in albums:
            album.update(
                {
                    "tracks": self._search_album_tracks(
                        conn=conn, artist=artist, album=album["album"]
                    )
                }
            )
        return albums

    def _search_album_tracks(self, conn: sqlite3.Connection, artist: str, album: str):
        sql = """
            SELECT DISTINCT path, filename, title FROM tracks
            WHERE artist = ? AND album = ?
            ORDER BY album;
            """
        cur = conn.execute(sql, (artist, album))
        tracks = [{"track": r} for r in cur]
        return tracks

    def _search_albums(
        self,
        conn: sqlite3.Connection,
        like_pat: str,
        starts_pat: str,
        limit: int,
        artists: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Searches for albums whose names match the given pattern.

        Returns a list of album dictionaries matching the search criteria, excluding artists already found in previous searches. The search is case-insensitive and limited to the specified number of results.

        Args:
            conn: The SQLite database connection.
            like_pat: The pattern to match album names.
            starts_pat: The pattern to match album names that start with the query.
            limit: The maximum number of results to return.
            artists: A list of artist dictionaries to exclude from the search.

        Returns:
            List[dict]: A list of dictionaries, each containing artist and album name.
        """
        if skip := {a["artist"].lower() for a in artists}:
            placeholders = ",".join("?" for _ in skip)
            sql = f"""
                SELECT DISTINCT artist, album FROM tracks
                WHERE album LIKE ? COLLATE NOCASE
                    AND lower(artist) NOT IN ({placeholders})
                ORDER BY album LIKE ? DESC, album COLLATE NOCASE
                LIMIT ?
            """
            params = (like_pat, starts_pat, *skip, limit)
        else:
            sql = """
                SELECT DISTINCT artist, album FROM tracks
                WHERE album LIKE ? COLLATE NOCASE
                ORDER BY album LIKE ? DESC, album COLLATE NOCASE
                LIMIT ?
            """
            params = (like_pat, starts_pat, limit)
        cur = conn.execute(sql, params)
        return [{"artist": r["artist"], "album": r["album"]} for r in cur]

    def _search_tracks(
        self,
        conn: sqlite3.Connection,
        like_pat: str,
        starts_pat: str,
        limit: int,
        artists: list[dict[str, Any]],
        albums: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Searches for tracks whose titles match the given pattern.

        Returns a list of track dictionaries matching the search criteria, excluding artists already found in previous searches. The search is case-insensitive and limited to the specified number of results.

        Args:
            conn: The SQLite database connection.
            like_pat: The pattern to match track titles.
            starts_pat: The pattern to match track titles that start with the query.
            limit: The maximum number of results to return.
            artists: A list of artist dictionaries to exclude from the search.
            albums: A list of album dictionaries whose artists should also be excluded.

        Returns:
            List[dict]: A list of dictionaries, each containing artist, album, and track name.
        """
        skip = {a["artist"].lower() for a in artists}
        skip.update(a["artist"].lower() for a in albums)
        if skip:
            placeholders = ",".join("?" for _ in skip)
            sql = f"""
                SELECT artist, album, title AS track FROM tracks
                WHERE title LIKE ? COLLATE NOCASE
                    AND lower(artist) NOT IN ({placeholders})
                ORDER BY title LIKE ? DESC, title COLLATE NOCASE
                LIMIT ?
            """
            cur = conn.execute(sql, (like_pat, starts_pat, *skip, limit))
        else:
            cur = conn.execute(
                """
                SELECT artist, album, title AS track FROM tracks
                WHERE title LIKE ? COLLATE NOCASE
                ORDER BY title LIKE ? DESC, title COLLATE NOCASE
                LIMIT ?
            """,
                (like_pat, starts_pat, limit),
            )
        return [
            {"artist": r["artist"], "album": r["album"], "track": r["track"]}
            for r in cur
        ]


class MusicWatcher(FileSystemEventHandler):
    """Watches for file system events in a music collection and updates the database.

    Monitors the specified music directory for changes and ensures the music database stays in sync with file additions, deletions, and modifications.
    """

    def __init__(self, collection: MusicCollection):
        """Initializes a MusicWatcher to monitor file system events for a music collection.

        Associates the watcher with a MusicCollection instance to keep the database in sync with file changes.

        Args:
            collection: The MusicCollection instance to monitor and update.
        """
        self.collection = collection

    def on_any_event(self, event: FileSystemEvent):
        """Handles any file system event for the music collection.

        Responds to file creation, modification, movement, or deletion events by updating the music database accordingly. Skips directories and unsupported file types.

        Args:
            event: The file system event to handle.

        Returns:
            None
        """
        if event.is_directory:
            return
        path = Path(
            getattr(event, "src_path", None) or getattr(event, "dest_path", None) or ""
        )
        if not path.exists() or path.suffix.lower() not in self.collection.supported:
            return
        with self.collection.get_conn() as conn:
            if event.event_type == "deleted":
                conn.execute("DELETE FROM tracks WHERE path = ?", (str(path),))
            else:
                with contextlib.suppress(Exception):
                    self.collection._index_file(conn, path)
            conn.commit()


# ==================== MAIN ====================
if __name__ == "__main__":
    if not MUSIC_ROOT.exists():
        logger.error(f"ERROR: Music folder not found: {MUSIC_ROOT}")
        exit(1)

    collection = MusicCollection(MUSIC_ROOT, DB_PATH)
    logger.info(f"Music folder : {MUSIC_ROOT}")
    logger.info(f"Database     : {DB_PATH}\n")

    if not collection.db_ready():
        collection.rebuild()
    else:
        logger.info(f"Library ready — {collection.count_tracks():,} tracks loaded\n")

    observer = Observer()
    observer.schedule(MusicWatcher(collection), str(MUSIC_ROOT), recursive=True)
    observer.start()
    print("Live monitoring active — type to search (q to quit)\n")

    try:
        while True:
            q = input("> ").strip()
            if q.lower() in {"q", "quit", "exit"}:
                break
            if not q:
                continue

            t0 = time.time()
            result = collection.search_grouped(q, limit=20)
            t = time.time() - t0

            print(f"\nResults in {t:.3f}s\n")

            if result["artists"]:
                print("ARTISTS:")
                for a in result["artists"][:10]:
                    print(f"  • {a['artist']}")
                if len(result["artists"]) > 10:
                    print(f"  ... +{len(result['artists']) - 10} more")

            if result["albums"]:
                print("\nALBUMS:")
                for a in result["albums"][:10]:
                    print(f"  • {a['artist']} — {a['album']}")
                if len(result["albums"]) > 10:
                    print(f"  ... +{len(result['albums']) - 10} more")

            if result["tracks"]:
                print("\nTRACKS:")
                for t in result["tracks"][:15]:
                    print(f"  • {t['artist']} — {t['album']} — {t['track']}")
                if len(result["tracks"]) > 15:
                    print(f"  ... +{len(result['tracks']) - 15} more")

            if not any(result.values()):
                print("  No results found.")

            print("\n" + "─" * 50)

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        observer.stop()
        observer.join()
        print("Bye!")
