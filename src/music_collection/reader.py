from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection
from typing import Iterator, Dict, Any

from ._extractor import CollectionExtractor  # internal implementation


class MusicCollection:
    """
    High-level interface to a music library.

    As long as an instance exists:
    - The database is kept in sync with the filesystem
    - Live file monitoring is active
    - At startup, any drift is automatically repaired

    Usage:
        collection = MusicCollection("/home/user/Music")
        for track in collection.search(artist="Radiohead"):
            print(track["title"])
    """

    def __init__(
        self,
        music_root: Path | str,
        db_path: Path | str | None = None,
    ):
        self._extractor = CollectionExtractor(
            music_root=Path(music_root),
            db_path=Path(db_path) if db_path else None,
        )

        # Start background monitoring (will stop on __del__ or close())
        self._extractor.start_monitoring()

        # Ensure DB is in sync with filesystem on startup
        if not self._extractor.is_synced_with_filesystem():
            print("Database out of sync with filesystem — repairing...")
            self._extractor.resync()

    # ====================== Query API ======================

    def count(self) -> int:
        """Return total number of tracks."""
        return self._extractor.count_tracks()

    def search(
        self,
        artist: str | None = None,
        album: str | None = None,
        title: str | None = None,
        genre: str | None = None,
        year: int | None = None,
    ) -> Iterator[Dict[str, Any]]:
        """
        Search tracks with optional filters (case-insensitive partial matches).
        """
        query = "SELECT * FROM tracks WHERE 1=1"
        params: list[Any] = []

        if artist:
            query += " AND artist LIKE ?"
            params.append(f"%{artist}%")
        if album:
            query += " AND album LIKE ?"
            params.append(f"%{album}%")
        if title:
            query += " AND title LIKE ?"
            params.append(f"%{title}%")
        if genre:
            query += " AND genre LIKE ?"
            params.append(f"%{genre}%")
        if year:
            query += " AND year = ?"
            params.append(year)

        query += " ORDER BY artist COLLATE NOCASE, year, album, title"

        with self._extractor.get_conn() as conn:
            for row in conn.execute(query, params):
                yield dict(row)

    def all_tracks(self) -> Iterator[Dict[str, Any]]:
        """Yield all tracks."""
        with self._extractor.get_conn() as conn:
            for row in conn.execute("SELECT * FROM tracks ORDER BY path"):
                yield dict(row)

    def get_by_path(self, path: str | Path) -> Dict[str, Any] | None:
        """Get a single track by full file path."""
        with self._extractor.get_conn() as conn:
            row = conn.execute("SELECT * FROM tracks WHERE path = ?", (str(path),)).fetchone()
            return dict(row) if row else None


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

        with self._extractor.get_conn() as conn:
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
        self, conn: Connection, starts_pat: str, limit: int
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

    def _search_artist_albums(self, conn: Connection, artist: str):
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

    def _search_album_tracks(self, conn: Connection, artist: str, album: str):
        sql = """
            SELECT DISTINCT title as track, path, filename
            FROM tracks
            WHERE artist = ? AND album = ?
            ORDER BY album;
            """
        cur = conn.execute(sql, (artist, album))
        tracks = [
            {"track": r["track"], "filename": r["filename"], "path": r["path"]} for r in cur
        ]
        return tracks

    def _search_albums(
        self,
        conn: Connection,
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
        albums = [{"artist": r["artist"], "album": r["album"]} for r in cur]
        for album in albums:
            album.update(
                {
                    "tracks": self._search_album_tracks(
                        conn=conn, artist=album["artist"], album=album["album"]
                    )
                }
            )
        return albums

    def _search_tracks(
        self,
        conn: Connection,
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
                SELECT artist, album, title AS track, path as file
                FROM tracks
                WHERE title LIKE ? COLLATE NOCASE
                    AND lower(artist) NOT IN ({placeholders})
                ORDER BY title LIKE ? DESC, title COLLATE NOCASE
                LIMIT ?
            """
            cur = conn.execute(sql, (like_pat, starts_pat, *skip, limit))
        else:
            cur = conn.execute(
                """
                SELECT artist, album, title AS track, path as file
                FROM tracks
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

    # ====================== Maintenance ======================

    def rebuild(self) -> None:
        """Force full reindex (useful if metadata changed a lot)."""
        self._extractor.rebuild()

    def close(self) -> None:
        """Stop background monitoring. Call explicitly if needed."""
        self._extractor.stop_monitoring()

    def __del__(self):
        # Best effort to stop observer
        try:
            self._extractor.stop_monitoring()
        except:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()