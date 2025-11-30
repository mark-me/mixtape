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

app = Flask(__name__)

class MusicCollection:
    def __init__(self, path_music: Path, path_db: Path):
        self.path_music = path_music.resolve()
        self.path_index = path_db / "music_index"
        self.path_db = path_db / "music.db"
        self.supporter_extensions = {".mp3", ".flac", ".ogg", ".oga", ".m4a", ".mp4", ".wav", ".wma"}

        self.schema = Schema(
            path=ID(stored=True, unique=True),
            artist=TEXT(stored=True, phrase=False),
            album=TEXT(stored=True, phrase=False),
            title=TEXT(stored=True, phrase=False),
        )

        self.ix = None
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
        ix = create_in(self.path_index, self.schema)
        writer = ix.writer()
        conn = self.get_db_connection()
        conn.execute("DELETE FROM tracks")

        count = 0
        for filepath in self.path_music.rglob("*.*"):
            if filepath.is_file() and filepath.suffix.lower() in self.supporter_extensions:
                self._index_single_track(writer, conn, filepath)
                count += 1
                if count % 1000 == 0:
                    print(f"   Indexed {count} tracks...")
                    conn.commit()

        writer.commit()
        conn.commit()
        conn.close()
        print(f"Indexing complete: {count} tracks")

    def _reset_indexes(self):
        import shutil
        if self.path_index.exists():
            shutil.rmtree(self.path_index)
        self.path_index.mkdir(parents=True)

    def _index_single_track(self, writer, conn, filepath: Path):
        try:
            tag = TinyTag.get(filepath, tags=True, duration=True)
        except:
            tag = None

        artist = (tag.artist or tag.albumartist or filepath.parent.parent.name or "Unknown").strip()
        album = (tag.album or filepath.parent.name or "Unknown").strip()
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
            getattr(tag, 'albumartist', None),
            getattr(tag, 'genre', None),
            tag.year if tag else None,
            tag.duration if tag else None,
        ))

    def ensure_index(self):
        with self._lock:
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

            self.ix = open_dir(self.path_index)

    def search(self, query: str, limit: int = 200):
        if not query.strip():
            return []

        with self.ix.searcher() as searcher:
            parser = MultifieldParser(["artist", "album", "title"], self.ix.schema)
            parser.add_plugin(FuzzyTermPlugin())
            q = parser.parse(f"{query}~1")

            results = searcher.search(q, limit=limit)
            conn = self.get_db_connection()
            hits = []
            for hit in results:
                if row := conn.execute(
                    "SELECT artist, album, title, path FROM tracks WHERE path = ?",
                    (hit['path'],),
                ).fetchone():
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
        if self.observer is not None:
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
            writer = self.ix.writer()
            conn = self.get_db_connection()
            self._index_single_track(writer, conn, filepath)
            writer.commit()
            conn.commit()
            conn.close()

    def _delete_file(self, filepath: Path):
        with self._lock:
            writer = self.ix.writer()
            writer.delete_by_term('path', str(filepath))
            writer.commit()

            conn = self.get_db_connection()
            conn.execute("DELETE FROM tracks WHERE path = ?", (str(filepath),))
            conn.commit()
            conn.close()

# Global collection instance
collection = MusicCollection(MUSIC_ROOT, DB_PATH)

@app.route("/")
def index():
    return render_template("app_search.html")

@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    start = time.time()
    results = collection.search(query, limit=500)
    duration = time.time() - start
    return jsonify({
        "results": results,
        "count": len(results),
        "time": round(duration, 3)
    })

@app.before_first_request
def startup():
    def run():
        collection.ensure_index()
        collection.start_watching()
    threading.Thread(target=run, daemon=True).start()

if __name__ == "__main__":
    # For development
    app.run(debug=True, host="0.0.0.0", port=5000)