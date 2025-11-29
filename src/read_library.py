#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import time
from pathlib import Path
from tinytag import TinyTag
from whoosh.index import create_in, open_dir, exists_in
from whoosh.fields import Schema, TEXT, ID
from whoosh.qparser import MultifieldParser, FuzzyTermPlugin
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ===================== CONFIG =====================
MUSIC_ROOT = Path("/home/mark/Music")  # WIJZIG DIT
INDEX_DIR = Path("./music_index")
DB_PATH = Path("./music.db")

EXTENSIONS = {".mp3", ".flac", ".ogg", ".oga", ".m4a", ".mp4", ".wav", ".wma"}

# ===================== SCHEMA =====================
schema = Schema(
    path=ID(stored=True, unique=True),
    artist=TEXT(stored=True, phrase=False),
    album=TEXT(stored=True, phrase=False),
    title=TEXT(stored=True, phrase=False),
)


# ===================== SQLite helpers =====================
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            path          TEXT UNIQUE NOT NULL,
            filename      TEXT,
            artist        TEXT,
            album         TEXT,
            title         TEXT,
            albumartist   TEXT,
            genre         TEXT,
            year          INTEGER,
            track         INTEGER,
            duration      REAL,
            bitrate       REAL,
            filesize      INTEGER,
            last_modified REAL,
            date_added    REAL DEFAULT (strftime('%s','now'))
        )
    """)
    for idx in ["path", "artist", "album", "title"]:
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{idx} ON tracks({idx})")
    conn.commit()
    conn.close()


def get_metadata(filepath: Path) -> dict:
    try:
        tag = TinyTag.get(filepath, tags=True, duration=True, image=False)
    except:
        tag = None

    stat = filepath.stat()
    artist = (
        tag.artist or tag.albumartist or filepath.parent.parent.name or "Unknown Artist"
    ).strip()
    album = (tag.album or filepath.parent.name or "Unknown Album").strip()
    title = (tag.title or filepath.stem).strip()

    return {
        "path": str(filepath),
        "filename": filepath.name,
        "artist": artist,
        "album": album,
        "title": title,
        "albumartist": tag.albumartist if tag else None,
        "genre": tag.genre if tag else None,
        "year": tag.year if tag else None,
        "track": tag.track if tag else None,
        "duration": tag.duration if tag else 0,
        "bitrate": tag.bitrate if tag else 0,
        "filesize": stat.st_size,
        "last_modified": stat.st_mtime,
    }


# ===================== Index opbouwen =====================
def build_indexes():
    print("Volledige indexering (Whoosh + SQLite)...")
    if INDEX_DIR.exists():
        import shutil

        shutil.rmtree(INDEX_DIR)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    ix = create_in(INDEX_DIR, schema)
    writer = ix.writer()

    conn = get_db_connection()
    conn.execute("DELETE FROM tracks")
    count = 0

    for filepath in MUSIC_ROOT.rglob("*"):
        if filepath.suffix.lower() in EXTENSIONS and filepath.is_file():
            data = get_metadata(filepath)

            # Whoosh
            writer.add_document(
                path=data["path"],
                artist=data["artist"].lower(),
                album=data["album"].lower(),
                title=data["title"].lower(),
            )

            # SQLite
            conn.execute(
                """
                INSERT OR REPLACE INTO tracks
                (path,filename,artist,album,title,albumartist,genre,year,track,duration,bitrate,filesize,last_modified)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
                (
                    data["path"],
                    data["filename"],
                    data["artist"],
                    data["album"],
                    data["title"],
                    data["albumartist"],
                    data["genre"],
                    data["year"],
                    data["track"],
                    data["duration"],
                    data["bitrate"],
                    data["filesize"],
                    data["last_modified"],
                ),
            )

            count += 1
            if count % 2000 == 0:
                print(f"  → {count} tracks...")
                conn.commit()

    writer.commit()
    conn.commit()
    conn.close()
    print(f"Klaar! {count} tracks geïndexeerd.\n")


def get_index():
    init_db()
    if not INDEX_DIR.exists() or not exists_in(INDEX_DIR):
        build_indexes()
    else:
        print("Bestaande indexen geladen.\n")
    return open_dir(INDEX_DIR)


# ===================== ZOEKEN (nu correct!) =====================
def search(query: str, limit: int = 100):
    ix = open_dir(INDEX_DIR)
    with ix.searcher() as searcher:
        parser = MultifieldParser(["artist", "album", "title"], ix.schema)
        parser.add_plugin(FuzzyTermPlugin())
        q = parser.parse(query.strip() + "~1")

        results = searcher.search(q, limit=limit)

        hits = []
        conn = get_db_connection()
        for hit in results:
            row = conn.execute(
                "SELECT artist, album, title, path FROM tracks WHERE path = ?",
                (hit["path"],),
            ).fetchone()
            if row:
                hits.append(dict(row))
        conn.close()
        return hits


# ===================== Watchdog handler =====================
class MusicHandler(FileSystemEventHandler):
    def __init__(self, ix):
        self.ix = ix

    def _update_file(self, path_str: str):
        p = Path(path_str)
        if p.suffix.lower() not in EXTENSIONS or not p.is_file():
            return
        return p

    def process(self, path_str: str):
        p = self._update_file(path_str)
        if not p:
            return

        data = get_metadata(p)

        # Whoosh update
        writer = self.ix.writer()
        writer.delete_by_term("path", data["path"])
        writer.add_document(
            path=data["path"],
            artist=data["artist"].lower(),
            album=data["album"].lower(),
            title=data["title"].lower(),
        )
        writer.commit()

        # SQLite update
        conn = get_db_connection()
        conn.execute(
            """
            INSERT OR REPLACE INTO tracks
            (path,filename,artist,album,title,albumartist,genre,year,track,duration,bitrate,filesize,last_modified)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
            (
                data["path"],
                data["filename"],
                data["artist"],
                data["album"],
                data["title"],
                data["albumartist"],
                data["genre"],
                data["year"],
                data["track"],
                data["duration"],
                data["bitrate"],
                data["filesize"],
                data["last_modified"],
            ),
        )
        conn.commit()
        conn.close()

        print(f"Updated: {data['artist']} – {data['title']}")

    def on_created(self, event):
        self.process(event.src_path)

    def on_modified(self, event):
        self.process(event.src_path)

    def on_moved(self, event):
        if self._update_file(event.src_path):
            # verwijder oude entry
            conn = get_db_connection()
            conn.execute("DELETE FROM tracks WHERE path = ?", (event.src_path,))
            conn.commit()
            conn.close()
            self.ix.writer().delete_by_term("path", event.src_path)
            self.ix.writer().commit()
        self.process(event.dest_path)

    def on_deleted(self, event):
        p = event.src_path
        conn = get_db_connection()
        conn.execute("DELETE FROM tracks WHERE path = ?", (p,))
        conn.commit()
        conn.close()
        self.ix.writer().delete_by_term("path", p)
        self.ix.writer().commit()


# ===================== Main =====================
def main():
    ix = get_index()

    observer = Observer()
    observer.schedule(MusicHandler(ix), str(MUSIC_ROOT), recursive=True)
    observer.start()
    print(f"Watching {MUSIC_ROOT}")
    print("Typ een zoekterm (of 'quit' om te stoppen):\n")

    try:
        while True:
            q = input("> ").strip()
            if q.lower() in {"quit", "exit", "q"}:
                break
            if not q:
                continue

            t0 = time.time()
            results = search(q)
            print(f"\n{len(results)} resultaten in {time.time() - t0:.3f}s\n")
            for r in results[:50]:
                print(f"{r['artist']} — {r['album']} — {r['title']}")
            if len(results) > 50:
                print(f"   … en nog {len(results) - 50} meer")
            print()
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
        print("\nAfgesloten.")


if __name__ == "__main__":
    main()
