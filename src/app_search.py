# app.py
from flask import Flask, render_template, request, jsonify
from pathlib import Path
import sqlite3
import time
import threading
from tinytag import TinyTag
from whoosh.index import create_in, open_dir, exists_in
from whoosh.fields import Schema, TEXT, ID
from whoosh.qparser import MultifieldParser, FuzzyTermPlugin
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ====================== CONFIG ======================
MUSIC_ROOT = Path("/home/mark/Music")  # ← CHANGE THIS TO YOUR MUSIC FOLDER
DB_PATH = Path(__file__).parent
# ====================================================

# Global collection instance - initialize early (before Flask app)
collection = None

class MusicCollection:
    def __init__(self, path_music: Path, path_db: Path):
        self.path_music = path_music.resolve()
        self.path_index = path_db / "music_index"
        self.path_db = path_db / "music.db"
        self.supporter_extensions = {".mp3", ".flac", ".ogg", ".oga", ".m4a", ".mp4", ".wav", ".wma"}

        self.schema_whoosh = Schema(
            path=ID(stored=True, unique=True),
            artist=TEXT(stored=True, phrase=False),
            album=TEXT(stored=True, phrase=False),
            title=TEXT(stored=True, phrase=False),
        )

        self.index_whoosh = None
        self.observer = None
        self._lock = threading.Lock()

    def get_db_connection(self):
        conn = sqlite3.connect(self.path_db)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        self.path_db.parent.mkdir(parents=True, exist_ok=True)
        conn = self.get_db_connection()
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
        conn.commit()
        conn.close()

    def build_indexes(self):
        print("Building full index...")
        self._reset_indexes()
        ix = create_in(self.path_index, self.schema_whoosh)
        writer = ix.writer()
        conn = self.get_db_connection()
        conn.execute("DELETE FROM tracks")

        count = 0
        for filepath in self.path_music.rglob("*"):
            if filepath.is_file() and filepath.suffix.lower() in self.supporter_extensions:
                self._index_single_track(writer, conn, filepath)
                count += 1
                if count % 1000 == 0:
                    print(f"   Indexed {count} tracks...")
                    writer.commit()  # Commit Whoosh more frequently to avoid memory issues
                    conn.commit()

        writer.commit()
        conn.commit()
        conn.close()
        print(f"Indexing complete: {count} tracks")

    def _reset_indexes(self):
        import shutil
        if self.path_index.exists():
            shutil.rmtree(self.path_index)
        self.path_index.mkdir(parents=True, exist_ok=True)

    def _index_single_track(self, writer, conn, filepath: Path):
        try:
            tag = TinyTag.get(filepath, tags=True, duration=True)
        except Exception:
            tag = None

        artist = (tag.artist or tag.albumartist or str(filepath.parent.parent.name) or "Unknown").strip()
        album = (tag.album or str(filepath.parent.name) or "Unknown").strip()
        title = (tag.title or filepath.stem).strip()

        writer.add_document(
            path=str(filepath),
            artist=artist.lower(),
            album=album.lower(),
            title=title.lower(),
        )

        conn.execute("""
            INSERT OR REPLACE INTO tracks
            (path, filename, artist, album, title, albumartist, genre, year, duration)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(filepath),
            filepath.name,
            artist,
            album,
            title,
            getattr(tag, 'albumartist', None) if tag else None,
            getattr(tag, 'genre', None) if tag else None,
            getattr(tag, 'year', None) if tag else None,
            getattr(tag, 'duration', None) if tag else None,
        ))

    def ensure_index(self):
        self.init_db()
        if not self.path_music.exists():
            raise FileNotFoundError(f"Music folder not found: {self.path_music}")

        need_reindex = not exists_in(self.path_index)
        if not need_reindex:
            conn = self.get_db_connection()
            count = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            conn.close()
            need_reindex = count == 0

        if need_reindex:
            print("No index found → building full index...")
            self.build_indexes()

        self.index_whoosh = open_dir(self.path_index)

    def search(self, query: str, limit: int = 200):
        if not query.strip():
            return []

        results = self._whoosh_search(query, limit)
        return self._collect_search_hits(results)

    def _whoosh_search(self, query, limit):
        with self.index_whoosh.searcher() as searcher:
            parser = MultifieldParser(["artist", "album", "title"], self.index_whoosh.schema)
            parser.add_plugin(FuzzyTermPlugin())
            q = parser.parse(f"{query}~1")
            return list(searcher.search(q, limit=limit))

    def _collect_search_hits(self, results):
        conn = self.get_db_connection()
        hits = []
        for hit in results:
            row = conn.execute(
                "SELECT artist, album, title, path FROM tracks WHERE path = ?",
                (hit['path'],)
            ).fetchone()
            if row:
                hits.append(dict(row))
            else:
                hits.append({
                    'artist': hit['artist'].title(),
                    'album': hit['album'].title(),
                    'title': hit['title'].title(),
                    'path': hit['path']
                })
        conn.close()
        return hits

    def start_watching(self):
        if self.observer is not None and self.observer.is_alive():
            return

        class Watcher(FileSystemEventHandler):
            def __init__(self, collection):
                self.collection = collection

            def on_created(self, event):
                if event.is_directory:
                    return
                path = Path(event.src_path)
                if path.suffix.lower() in self.collection.supporter_extensions:
                    self.collection._update_single_file(path)

            def on_modified(self, event):
                self.on_created(event)

            def on_moved(self, event):
                if event.is_directory:
                    return
                path = Path(event.dest_path)
                if path.suffix.lower() in self.collection.supporter_extensions:
                    self.collection._update_single_file(path)

            def on_deleted(self, event):
                if event.is_directory:
                    return
                path = Path(event.src_path)
                self.collection._delete_file(path)

        self.observer = Observer()
        self.observer.schedule(Watcher(self), str(self.path_music), recursive=True)
        self.observer.start()
        print("Live file monitoring started")

    def _update_single_file(self, filepath: Path):
        with self._lock:
            try:
                writer = self.index_whoosh.writer()
                conn = self.get_db_connection()
                self._index_single_track(writer, conn, filepath)
                writer.commit()
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"Error updating {filepath}: {e}")

    def _delete_file(self, filepath: Path):
        with self._lock:
            try:
                writer = self.index_whoosh.writer()
                writer.delete_by_term('path', str(filepath))
                writer.commit()

                conn = self.get_db_connection()
                conn.execute("DELETE FROM tracks WHERE path = ?", (str(filepath),))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"Error deleting {filepath}: {e}")

# Initialize collection manually (runs on module import)
collection = MusicCollection(MUSIC_ROOT, DB_PATH)
try:
    collection.ensure_index()
    collection.start_watching()
    print("MusicCollection initialized successfully!")
except Exception as e:
    print(f"Initialization error: {e}")

# Now create Flask app
app = Flask(__name__)

@app.route("/")
def index():
    return render_template("app_search.html")

@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({
            "artists": [],
            "albums": [],
            "tracks": [],
            "count": 0,
            "time": 0
        })

    start = time.time()
    raw_results = collection.search(query, limit=1000)  # Get more for good grouping
    duration = time.time() - start

    # Group by artist and extract unique artists/albums
    artists_seen = set()
    albums_seen = set()
    artist_matches = []
    album_matches = []

    for track in raw_results:
        artist = track['artist']
        album_key = (artist, track['album'])  # unique album = artist + album name

        if artist not in artists_seen:
            artists_seen.add(artist)
            artist_matches.append({
                "name": artist,
                "track_count": 1,
                "sample_tracks": [track['title']]
            })
        else:
            # Update existing artist entry
            for a in artist_matches:
                if a['name'] == artist:
                    a['track_count'] += 1
                if len(a['sample_tracks']) < 3:
                    a['sample_tracks'].append(track['title'])

        if album_key not in albums_seen:
            albums_seen.add(album_key)
            album_matches.append({
                "artist": artist,
                "name": track['album'],
                "track_count": 1,
                "sample_tracks": [track['title']]
            })
        else:
            for al in album_matches:
                if al['artist'] == artist and al['name'] == track['album']:
                    al['track_count'] += 1
                    if len(al['sample_tracks']) < 3:
                        al['sample_tracks'].append(track['title'])

    # Sort artists/albums by relevance (number of matching tracks)
    artist_matches.sort(key=lambda x: x['track_count'], reverse=True)
    album_matches.sort(key=lambda x: x['track_count'], reverse=True)

    return jsonify({
        "artists": artist_matches[:20],        # Top 20 artists
        "albums": album_matches[:30],          # Top 30 albums
        "tracks": raw_results[:200],           # Top 200 tracks
        "total_tracks": len(raw_results),
        "query": query,
        "time": round(duration, 3)
    })

if __name__ == "__main__":
    # For development - app is already initialized
    app.run(debug=True, host="0.0.0.0", port=5000)