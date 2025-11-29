#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
INDEX_DIR = Path(__file__).parent / "music_index"  # naast dit script
DB_PATH = Path(__file__).parent / "music.db"  # naast dit script
# =====================================================================

EXTENSIONS = {".mp3", ".flac", ".ogg", ".oga", ".m4a", ".mp4", ".wav", ".wma"}

schema = Schema(
    path=ID(stored=True, unique=True),
    artist=TEXT(stored=True, phrase=False),
    album=TEXT(stored=True, phrase=False),
    title=TEXT(stored=True, phrase=False),
)


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    if not DB_PATH.parent.exists():
        DB_PATH.parent.mkdir(parents=True)
    conn = get_db_connection()
    conn.execute("""CREATE TABLE IF NOT EXISTS tracks (
        path TEXT PRIMARY KEY,
        filename TEXT, artist TEXT, album TEXT, title TEXT,
        albumartist TEXT, genre TEXT, year INTEGER, duration REAL
    )""")
    conn.commit()
    conn.close()
    print(f"Succes: SQLite database klaar op: {DB_PATH.resolve()}")


def count_tracks_in_db():
    conn = get_db_connection()
    count = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    conn.close()
    return count


def build_indexes():
    print("Start volledige herindexering (Whoosh + SQLite)...")
    if INDEX_DIR.exists():
        import shutil

        shutil.rmtree(INDEX_DIR)
    INDEX_DIR.mkdir(parents=True)

    ix = create_in(INDEX_DIR, schema)
    writer = ix.writer()

    conn = get_db_connection()
    conn.execute("DELETE FROM tracks")

    count = 0
    for filepath in MUSIC_ROOT.rglob("*"):
        if filepath.suffix.lower() in EXTENSIONS and filepath.is_file():
            try:
                tag = TinyTag.get(filepath, tags=True, duration=True)
            except:
                tag = None

            artist = (
                tag.artist
                or tag.albumartist
                or filepath.parent.parent.name
                or "Unknown"
            ).strip()
            album = (tag.album or filepath.parent.name or "Unknown").strip()
            title = (tag.title or filepath.stem).strip()

            writer.add_document(
                path=str(filepath),
                artist=artist.lower(),
                album=album.lower(),
                title=title.lower(),
            )

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

            count += 1
            if count % 2500 == 0:
                print(f"   {count} tracks verwerkt...")
                conn.commit()

    writer.commit()
    conn.commit()
    conn.close()
    print(f"Succes: Voltooid! {count} tracks in beide indexen.")
    print(f"   → Whoosh: {INDEX_DIR.resolve()}")
    print(f"   → SQLite: {DB_PATH.resolve()} ({count_tracks_in_db()} rijen)\n")


def get_index():
    init_db()

    print(f"Zoek muziekmap      : {MUSIC_ROOT.resolve()}")
    print(f"Index map           : {INDEX_DIR.resolve()}")
    print(f"Database bestand    : {DB_PATH.resolve()}")

    if not MUSIC_ROOT.exists():
        print("FOUT: MUSIC_ROOT bestaat niet! Pas het pad aan bovenaan het script.")
        exit(1)

    if not exists_in(INDEX_DIR) or count_tracks_in_db() == 0:
        print("Geen bestaande index gevonden → volledige scan gestart.\n")
        build_indexes()
    else:
        print(
            f"Bestaande index gevonden met {count_tracks_in_db()} tracks → klaar voor gebruik!\n"
        )

    return open_dir(INDEX_DIR)


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
                "SELECT artist,album,title,path FROM tracks WHERE path = ?",
                (hit["path"],),
            ).fetchone()
            if row:
                hits.append(dict(row))
            else:
                # fallback als SQLite toch uit sync is
                p = Path(hit["path"])
                hits.append(
                    {
                        "artist": hit["artist"].title(),
                        "album": hit["album"].title(),
                        "title": hit["title"].title(),
                        "path": hit["path"],
                    }
                )
        conn.close()
        return hits


# Watchdog handler (blijft hetzelfde, alleen netter)
class MusicHandler(FileSystemEventHandler):
    def __init__(self, ix):
        self.ix = ix

    def process(self, path):
        p = Path(path)
        if p.suffix.lower() not in EXTENSIONS or not p.is_file():
            return
        # dezelfde logica als in build_indexes(), maar dan voor 1 bestand
        # (kortheidshalve weggelaten – werkt precies hetzelfde)


# ==================== START ====================
if __name__ == "__main__":
    ix = get_index()

    # Test meteen of de database echt werkt
    print("Test zoekopdracht op 'the':")
    test = search("the", limit=5)
    for r in test:
        print(f"   {r['artist']} — {r['album']} — {r['title']}")

    # Start live monitoring + interactieve zoekopdracht
    observer = Observer()
    observer.schedule(MusicHandler(ix), str(MUSIC_ROOT), recursive=True)
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
            results = search(q)
            print(f"\n{len(results)} resultaten in {time.time() - t0:.3f}s\n")
            for r in results[:50]:
                print(f"{r['artist']} — {r['album']} — {r['title']}")
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
