from flask import Flask, render_template, request, jsonify
import json

app = Flask(__name__)

# Dummy muziekbibliotheek (in een echte app komt dit uit een DB)
LIBRARY = [
    {"artist": "Daft Punk", "album": "Random Access Memories", "tracks": [
        {"title": "Get Lucky", "duration": "4:08"},
        {"title": "Lose Yourself to Dance", "duration": "5:53"},
        {"title": "Instant Crush", "duration": "5:37"}
    ]},
    {"artist": "Daft Punk", "album": "Discovery", "tracks": [
        {"title": "One More Time", "duration": "5:20"},
        {"title": "Harder, Better, Faster, Stronger", "duration": "3:45"},
        {"title": "Digital Love", "duration": "4:58"}
    ]},
    {"artist": "The Weeknd", "album": "After Hours", "tracks": [
        {"title": "Blinding Lights", "duration": "3:20"},
        {"title": "Save Your Tears", "duration": "3:35"},
        {"title": "In Your Eyes", "duration": "3:57"}
    ]},
    {"artist": "Arctic Monkeys", "album": "AM", "tracks": [
        {"title": "Do I Wanna Know?", "duration": "4:32"},
        {"title": "R U Mine?", "duration": "3:21"},
        {"title": "Why'd You Only Call Me When You're High?", "duration": "2:41"}
    ]}
]

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/search")
def search():
    query = request.args.get("q", "").lower().strip()
    if not query:
        return jsonify([])

    results = []
    for entry in LIBRARY:
        artist_lower = entry["artist"].lower()
        album_lower = entry["album"].lower()

        artist_match = query in artist_lower
        album_match = query in album_lower

        track_matches = []
        track_partial = []
        for track in entry["tracks"]:
            track_lower = track["title"].lower()
            if query in track_lower:
                track_matches.append(track)
                # Zoek de exacte positie voor highlighting
                pos = track_lower.find(query)
                before = track["title"][:pos]
                match = track["title"][pos:pos+len(query)]
                after = track["title"][pos+len(query):]
                track_partial.append({
                    "original": track,
                    "highlighted": f"{before}<mark>{match}</mark>{after}",
                    "match_type": "track"
                })

        # Bepaal waarom dit resultaat getoond wordt
        reasons = []
        if artist_match:
            reasons.append({"type": "artist", "text": entry["artist"]})
        if album_match:
            reasons.append({"type": "album", "text": entry["album"]})
        if track_matches:
            reasons.append({"type": "track", "text": f"{len(track_matches)} nummer(s)"})

        if reasons:
            # Toon alle tracks als er een artiest- of albummatch is
            displayed_tracks = entry["tracks"] if (artist_match or album_match) else [t["original"] for t in track_partial]

            results.append({
                "artist": entry["artist"],
                "album": entry["album"],
                "reasons": reasons,
                "tracks": displayed_tracks,
                "highlighted_tracks": track_partial if track_partial else None
            })

    return jsonify(results)

if __name__ == "__main__":
    app.run(debug=True)