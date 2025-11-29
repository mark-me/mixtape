import sqlite3
import time
from pathlib import Path
from tinytag import TinyTag
from whoosh.index import create_in, open_dir, exists_in
from whoosh.fields import Schema, TEXT, ID
from whoosh.qparser import MultifieldParser, FuzzyTermPlugin
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ==================== CONFIG — WIJZIG ALLEEN DEZE ====================
MUSIC_ROOT = Path("/home/mark/Music")  # ← ABSOLUTE pad naar je muziek!
DB_PATH = Path(__file__).parent
# =====================================================================


class MusicCollection:
    def __init__(self, path_music: Path, path_db: Path):
        """Initializes the MusicCollection with paths and supported extensions.

        This constructor sets up the music directory, database path, supported file extensions, and the Whoosh schema for indexing.

        Args:
            path_music (Path): The path to the music directory.
            path_db (Path): The path to the SQLite database file.
        """
        self.supporter_extensions = {
            ".mp3",
            ".flac",
            ".ogg",
            ".oga",
            ".m4a",
            ".mp4",
            ".wav",
            ".wma",
        }
        self.path_music = path_music
        self.path_index = path_db / "music_index"
        self.path_db = path_db / "music.db"
        self.schema = Schema(
            path=ID(stored=True, unique=True),
            artist=TEXT(stored=True, phrase=False),
            album=TEXT(stored=True, phrase=False),
            title=TEXT(stored=True, phrase=False),
        )

    def get_db_connection(self):
        """Returns a SQLite database connection for the music collection.

        This method creates and returns a connection to the SQLite database, setting the row factory for dictionary-like access.

        Returns:
            sqlite3.Connection: The SQLite database connection.
        """
        conn = sqlite3.connect(self.path_db)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        """Initializes the SQLite database for the music collection.

        This method creates the database directory if needed and ensures the tracks table exists.

        Returns:
            None
        """
        if not self.path_db.parent.exists():
            self.path_db.parent.mkdir(parents=True)
        conn = self.get_db_connection()
        conn.execute(
            """
                CREATE TABLE IF NOT EXISTS tracks (
                    path TEXT PRIMARY KEY,
                    filename TEXT,
                    artist TEXT,
                    album TEXT,
                    title TEXT,
                    albumartist TEXT,
                    genre TEXT,
                    year INTEGER,
                    duration REAL )"""
        )
        conn.commit()
        conn.close()
        print(f"Succes: SQLite database klaar op: {self.path_db.resolve()}")

    def count_tracks_in_db(self):
        """Returns the number of tracks currently stored in the SQLite database.

        This method queries the tracks table and returns the total count of track entries.

        Returns:
            int: The number of tracks in the database.
        """
        conn = self.get_db_connection()
        count = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        conn.close()
        return count

    def build_indexes(self):
        """Builds both the Whoosh and SQLite indexes for the music collection.

        This method resets the index directories, initializes the indexes, and processes all tracks using smaller helper methods.

        Returns:
            None
        """
        print("Start volledige herindexering (Whoosh + SQLite)...")
        self._reset_indexes()
        ix, writer = self._init_whoosh_index()
        conn = self._init_sqlite_index()

        count = self._index_tracks(writer, conn)

        writer.commit()
        conn.commit()
        conn.close()
        print(f"Succes: Voltooid! {count} tracks in beide indexen.")
        print(f"   → Whoosh: {self.path_index.resolve()}")
        print(
            f"   → SQLite: {self.path_db.resolve()} ({self.count_tracks_in_db()} rijen)\n"
        )

    def _reset_indexes(self):
        """Resets the Whoosh index directory by deleting and recreating it.

        This method removes the existing index directory and creates a new, empty one for reindexing.
        """
        if self.path_index.exists():
            import shutil

            shutil.rmtree(self.path_index)
        self.path_index.mkdir(parents=True)

    def _init_whoosh_index(self):
        """Initializes the Whoosh index for the music collection.

        This method creates a new Whoosh index in the index directory and returns the index and writer objects.

        Returns:
            tuple: A tuple containing the Whoosh index and its writer.
        """
        ix = create_in(self.path_index, self.schema)
        writer = ix.writer()
        return ix, writer

    def _init_sqlite_index(self):
        """Initializes the SQLite index by clearing the tracks table.

        This method returns a database connection after deleting all existing track entries.

        Returns:
            sqlite3.Connection: The SQLite database connection.
        """
        conn = self.get_db_connection()
        conn.execute("DELETE FROM tracks")
        return conn

    def _index_tracks(self, writer, conn):
        """Indexes all supported audio files in the music root directory.

        This method iterates through all files, extracts metadata, and adds entries to both the Whoosh and SQLite indexes using helper methods.

        Args:
            writer: The Whoosh index writer.
            conn: The SQLite database connection.

        Returns:
            int: The total number of tracks indexed.
        """
        count = 0
        count = 0
        # Only traverse files, not directories, and filter by supported extensions
        for ext in self.SUPPORTED_EXTENSIONS:
            for filepath in self.path_music.rglob(f"*{ext}"):
                if filepath.is_file() and self._is_supported_file(filepath):
                    self._index_single_track(writer, conn, filepath)
                    count += 1
                    if count % 2500 == 0:
                        print(f"   {count} tracks verwerkt...")
                        conn.commit()
        return count

    def _index_single_track(self, writer, conn, filepath):
        """Indexes a single audio file in both Whoosh and SQLite indexes.

        This method extracts metadata and adds the track to both indexes.

        Args:
            writer: The Whoosh index writer.
            conn: The SQLite database connection.
            filepath (Path): The path to the audio file.
        """
        tag = self._get_tag(filepath)
        artist, album, title = self._extract_metadata(tag, filepath)
        self._add_to_whoosh(writer, filepath, artist, album, title)
        self._add_to_sqlite(conn, filepath, tag, artist, album, title)

    def _is_supported_file(self, filepath):
        """Checks if the given file is a supported audio file.

        Args:
            filepath (Path): The file path to check.

        Returns:
            bool: True if the file is supported and is a file, False otherwise.
        """
        return (
            filepath.suffix.lower() in self.supporter_extensions and filepath.is_file()
        )

    def _get_tag(self, filepath):
        """Retrieves tag metadata from an audio file using TinyTag.

        This method attempts to extract tags and duration from the given file, returning None if extraction fails.

        Args:
            filepath (Path): The path to the audio file.

        Returns:
            TinyTag or None: The extracted tag object, or None if extraction fails.
        """
        try:
            return TinyTag.get(filepath, tags=True, duration=True)
        except Exception:
            return None

    def _extract_metadata(self, tag, filepath):
        """Extracts artist, album, and title metadata from a tag object and file path.

        This method provides fallback values based on the file path if the tag is missing or incomplete.

        Args:
            tag (TinyTag or None): The tag object containing metadata, or None.
            filepath (Path): The path to the audio file.

        Returns:
            tuple: A tuple containing artist, album, and title strings.
        """
        artist = (
            tag.artist or tag.albumartist or filepath.parent.parent.name or "Unknown"
        ).strip()
        album = (tag.album or filepath.parent.name or "Unknown").strip()
        title = (tag.title or filepath.stem).strip()
        return artist, album, title

    def _add_to_whoosh(self, writer, filepath, artist, album, title):
        """Adds or updates a track entry in the Whoosh index.

        This method inserts the track metadata for the given file into the Whoosh index.

        Args:
            writer: The Whoosh index writer.
            filepath (Path): The path to the audio file.
            artist (str): The artist name.
            album (str): The album name.
            title (str): The track title.
        """
        writer.add_document(
            path=str(filepath),
            artist=artist.lower(),
            album=album.lower(),
            title=title.lower(),
        )

    def _add_to_sqlite(self, conn, filepath, tag, artist, album, title):
        """Adds or updates a track entry in the SQLite database.

        This method inserts or replaces the track metadata for the given file in the tracks table.

        Args:
            conn: The SQLite database connection.
            filepath (Path): The path to the audio file.
            tag (TinyTag or None): The tag object containing metadata, or None.
            artist (str): The artist name.
            album (str): The album name.
            title (str): The track title.
        """
        conn.execute(
            """INSERT OR REPLACE INTO tracks
            (path,filename,artist,album,title,albumartist,genre,year,duration)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                str(filepath),
                filepath.name,
                artist,
                album,
                title,
                tag.albumartist if tag else None,
                tag.genre if tag else None,
                tag.year,
                tag.duration,
            ),
        )

    def get_index(self):
        """Initializes and returns the Whoosh index for the music collection.

        This method ensures the database and index directories exist, builds indexes if needed, and returns the Whoosh index object.

        Returns:
            whoosh.index.Index: The Whoosh index object for the music collection.
        """
        self.init_db()

        self._print_paths()

        if not self.path_music.exists():
            print(f"FOUT: {self.path_music} bestaat niet! Pas het pad aan bovenaan het script.")
            exit(1)

        if not exists_in(self.path_index) or self.count_tracks_in_db() == 0:
            print("Geen bestaande index gevonden → volledige scan gestart.\n")
            self.build_indexes()
        else:
            print(
                f"Bestaande index gevonden met {self.count_tracks_in_db()} tracks → klaar voor gebruik!\n"
            )

        return open_dir(self.path_index)

    def _print_paths(self):
        """Prints the paths for the music directory, index directory, and database file.

        This helper method outputs the relevant paths for debugging and setup.
        """
        print(f"Zoek muziekmap      : {self.path_music.resolve()}")
        print(f"Index map           : {self.path_index.resolve()}")
        print(f"Database bestand    : {self.path_db.resolve()}")

    def search(self, query: str, limit: int = 100):
        """Searches the music collection for tracks matching the query string.

        This method performs a fuzzy search across artist, album, and title fields, returning a list of matching tracks with their metadata.

        Args:
            query (str): The search query string.
            limit (int, optional): The maximum number of results to return. Defaults to 100.

        Returns:
            list: A list of dictionaries containing artist, album, title, and path for each matching track.
        """
        ix = open_dir(self.path_index)
        with ix.searcher() as searcher:
            parser = MultifieldParser(["artist", "album", "title"], ix.schema)
            parser.add_plugin(FuzzyTermPlugin())
            q = parser.parse(f"{query.strip()}~1")
            results = searcher.search(q, limit=limit)

            conn = self.get_db_connection()
            hits = [self._get_hit_metadata(conn, hit) for hit in results]
            conn.close()
            return hits

    def _get_hit_metadata(self, conn, hit):
        """Retrieves metadata for a search hit from the database, with fallback to index data.

        Args:
            conn: The SQLite database connection.
            hit: The Whoosh search result hit.

        Returns:
            dict: A dictionary containing artist, album, title, and path for the track.
        """
        row = conn.execute(
            "SELECT artist,album,title,path FROM tracks WHERE path = ?",
            (hit["path"],),
        ).fetchone()
        if row:
            return dict(row)
        else:
            return {
                "artist": hit["artist"].title(),
                "album": hit["album"].title(),
                "title": hit["title"].title(),
                "path": hit["path"],
            }


# Watchdog handler (blijft hetzelfde, alleen netter)
class MusicWatcher(FileSystemEventHandler):
    def __init__(self, ix):
        self.ix = ix
        self.supporter_extensions = {
            ".mp3",
            ".flac",
            ".ogg",
            ".oga",
            ".m4a",
            ".mp4",
            ".wav",
            ".wma",
        }

    def process(self, path):
        p = Path(path)
        if p.suffix.lower() not in self.supporter_extensions or not p.is_file():
            return


# ==================== START ====================
if __name__ == "__main__":
    collection = MusicCollection(path_music=MUSIC_ROOT, path_db=DB_PATH)
    ix = collection.get_index()

    # Test meteen of de database echt werkt
    print("Test zoekopdracht op 'the':")
    test = collection.search("the", limit=5)
    for r in test:
        print(f"   {r['artist']} — {r['album']} — {r['title']}")

    # Start live monitoring + interactieve zoekopdracht
    observer = Observer()
    observer.schedule(MusicWatcher(ix), str(MUSIC_ROOT), recursive=True)
    observer.start()
    print("\nLive monitoring actief. Typ een zoekterm (quit om te stoppen):\n")

    try:
        while True:
            q = input("> ").strip()
            if q.lower() in {"quit", "q", "exit"}:
                break
            if not q:
                continue
            t0 = time.time()
            results = collection.search(q)
            print(f"\n{len(results)} resultaten in {time.time() - t0:.3f}s\n")
            for r in results[:50]:
                print(f"{r['artist']} — {r['album']} — {r['title']}")
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
