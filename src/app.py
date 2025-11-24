from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    send_from_directory,
    jsonify,
    Response,
)
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    login_required,
    logout_user,
    current_user,
)
import os
import json
import datetime
import mutagen
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "your_secret_key"  # Verander dit in productie!

# Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


class User(UserMixin):
    def __init__(self, id):
        self.id = id


@login_manager.user_loader
def load_user(user_id):
    return User(user_id)


# Hardcoded admin (voor demo; gebruik hashing in productie)
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "password"

# Directories
MIXTAPE_DIR = "mixtapes"
MUSIC_DIR = "music"
COVER_DIR = "covers"

os.makedirs(MIXTAPE_DIR, exist_ok=True)
os.makedirs(MUSIC_DIR, exist_ok=True)
os.makedirs(COVER_DIR, exist_ok=True)


# Helper om mixtapes te laden
def load_mixtapes(sort_by="alpha"):
    mixtapes = []
    for filename in os.listdir(MIXTAPE_DIR):
        if filename.endswith(".json"):
            with open(os.path.join(MIXTAPE_DIR, filename), "r") as f:
                data = json.load(f)
                data["filename"] = filename
                mixtapes.append(data)
    if sort_by == "alpha":
        mixtapes.sort(key=lambda x: x["title"].lower())
    elif sort_by == "created":
        mixtapes.sort(key=lambda x: x["created"], reverse=True)
    elif sort_by == "modified":
        mixtapes.sort(key=lambda x: x.get("modified", x["created"]), reverse=True)
    return mixtapes


@app.route("/")
def index():
    mixtapes = load_mixtapes(sort_by="modified")  # Nieuwste bovenaan
    return render_template("index.html", mixtapes=mixtapes)


# Login route
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if (
            request.form["username"] == ADMIN_USERNAME
            and request.form["password"] == ADMIN_PASSWORD
        ):
            user = User(1)
            login_user(user)
            return redirect(url_for("admin"))
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# Admin pagina
@app.route("/admin", methods=["GET", "POST"])
@login_required
def admin():
    sort_by = request.args.get("sort", "alpha")
    mixtapes = load_mixtapes(sort_by)
    return render_template("admin.html", mixtapes=mixtapes, sort_by=sort_by)


# Nieuwe mixtape aanmaken
@app.route("/create_mixtape", methods=["POST"])
@login_required
def create_mixtape():
    title = request.form["title"]
    if not title:
        return "Titel vereist", 400
    filename = secure_filename(title + ".json")
    if os.path.exists(os.path.join(MIXTAPE_DIR, filename)):
        return "Titel bestaat al", 400

    data = {
        "title": title,
        "created": datetime.datetime.now().isoformat(),
        "modified": datetime.datetime.now().isoformat(),
        "tracks": [],  # Lijst van file paths
        "cover": None,  # Path naar cover art
    }
    with open(os.path.join(MIXTAPE_DIR, filename), "w") as f:
        json.dump(data, f)
    return redirect(url_for("admin"))


# Mixtape clonen
@app.route("/clone_mixtape/<title>", methods=["POST"])
@login_required
def clone_mixtape(title):
    old_filename = secure_filename(title + ".json")
    old_path = os.path.join(MIXTAPE_DIR, old_filename)
    if not os.path.exists(old_path):
        return "Niet gevonden", 404

    with open(old_path, "r") as f:
        data = json.load(f)

    new_title = title + "_clone"
    new_filename = secure_filename(new_title + ".json")
    data["title"] = new_title
    data["created"] = datetime.datetime.now().isoformat()
    data["modified"] = datetime.datetime.now().isoformat()

    with open(os.path.join(MIXTAPE_DIR, new_filename), "w") as f:
        json.dump(data, f)
    return redirect(url_for("admin"))


# Mixtape verwijderen
@app.route("/delete_mixtape/<title>", methods=["POST"])
@login_required
def delete_mixtape(title):
    filename = secure_filename(title + ".json")
    path = os.path.join(MIXTAPE_DIR, filename)
    if os.path.exists(path):
        os.remove(path)
    return redirect(url_for("admin"))


@app.route("/edit/<title>", methods=["GET", "POST"])
@login_required
def edit_mixtape(title):
    filename = secure_filename(title + ".json")
    path = os.path.join(MIXTAPE_DIR, filename)

    if not os.path.exists(path):
        flash("Mixtape niet gevonden", "danger")
        return redirect(url_for("admin"))

    with open(path, "r") as f:
        data = json.load(f)

    # Haal alle beschikbare muziekbestanden op
    available_tracks = [
        f
        for f in os.listdir(MUSIC_DIR)
        if f.lower().endswith((".mp3", ".flac", ".ogg", ".oga"))
    ]

    # Haal huidige tracks met tags
    current_tracks = []
    for track_path in data.get("tracks", []):
        full_path = os.path.join(
            MUSIC_DIR, track_path.split("/")[-1]
        )  # compatibiliteit
        try:
            audio = mutagen.File(full_path)
            tags = {
                "title": str(audio.get("TIT2", [os.path.basename(track_path)])[0]),
                "artist": str(audio.get("TPE1", ["Onbekend"])[0]),
                "album": str(audio.get("TALB", [""])[0]),
            }
        except:
            tags = {
                "title": os.path.basename(track_path),
                "artist": "Onbekend",
                "album": "",
            }
        current_tracks.append(
            {"path": track_path, "filename": os.path.basename(track_path), "tags": tags}
        )

    if request.method == "POST":
        action = request.form.get("action")

        if action == "update_title":
            new_title = request.form["title"].strip()
            if not new_title:
                flash("Titel mag niet leeg zijn", "danger")
            elif new_title != title and os.path.exists(
                os.path.join(MIXTAPE_DIR, secure_filename(new_title + ".json"))
            ):
                flash("Er bestaat al een mixtape met deze titel", "danger")
            else:
                # Hernoem JSON + eventuele cover
                new_filename = secure_filename(new_title + ".json")
                os.rename(path, os.path.join(MIXTAPE_DIR, new_filename))
                if data.get("cover"):
                    old_cover = data["cover"]
                    new_cover = os.path.join(
                        COVER_DIR, secure_filename(new_title + ".jpg")
                    )
                    if os.path.exists(old_cover):
                        os.rename(old_cover, new_cover)
                    data["cover"] = new_cover

                data["title"] = new_title
                data["modified"] = datetime.datetime.now().isoformat()
                with open(os.path.join(MIXTAPE_DIR, new_filename), "w") as f:
                    json.dump(data, f, indent=2)
                flash("Titel bijgewerkt!", "success")
                return redirect(url_for("edit_mixtape", title=new_title))

        elif action == "add_tracks":
            selected = request.form.getlist("new_tracks")
            added = 0
            for track in selected:
                track_path = os.path.join(MUSIC_DIR, track)
                if os.path.exists(track_path) and track_path not in data["tracks"]:
                    data["tracks"].append(track_path)
                    added += 1
            if added:
                data["modified"] = datetime.datetime.now().isoformat()
                with open(path, "w") as f:
                    json.dump(data, f, indent=2)
                flash(f"{added} track(s) toegevoegd", "success")
            else:
                flash("Geen nieuwe tracks geselecteerd", "info")

        elif action == "remove_track":
            track_to_remove = request.form["track_path"]
            if track_to_remove in data["tracks"]:
                data["tracks"].remove(track_to_remove)
                data["modified"] = datetime.datetime.now().isoformat()
                with open(path, "w") as f:
                    json.dump(data, f, indent=2)
                flash("Track verwijderd", "success")

        elif "cover" in request.files and request.files["cover"].filename:
            file = request.files["cover"]
            ext = os.path.splitext(file.filename)[1].lower()
            if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
                flash("Alleen JPG/PNG/WebP toegestaan", "danger")
            else:
                cover_path = os.path.join(COVER_DIR, secure_filename(title + ".jpg"))
                file.save(cover_path)
                data["cover"] = cover_path
                data["modified"] = datetime.datetime.now().isoformat()
                with open(path, "w") as f:
                    json.dump(data, f, indent=2)
                flash("Cover bijgewerkt!", "success")

        return redirect(url_for("edit_mixtape", title=title))

    return render_template(
        "edit.html",
        mixtape=data,
        current_tracks=current_tracks,
        available_tracks=available_tracks,
    )


# Tracks toevoegen aan mixtape (via form, selecteer uit MUSIC_DIR)
@app.route("/add_tracks/<title>", methods=["POST"])
@login_required
def add_tracks(title):
    filename = secure_filename(title + ".json")
    path = os.path.join(MIXTAPE_DIR, filename)
    with open(path, "r") as f:
        data = json.load(f)

    selected_tracks = request.form.getlist("tracks")  # Meerdere selecties
    for track in selected_tracks:
        track_path = os.path.join(MUSIC_DIR, secure_filename(track))
        if os.path.exists(track_path) and track_path not in data["tracks"]:
            data["tracks"].append(track_path)

    data["modified"] = datetime.datetime.now().isoformat()
    with open(path, "w") as f:
        json.dump(data, f)
    return redirect(url_for("admin"))


@app.route("/reorder_tracks/<title>", methods=["POST"])
@login_required
def reorder_tracks(title):
    import json

    data = request.get_json()
    new_order = data.get("tracks", [])

    filename = secure_filename(title + ".json")
    path = os.path.join(MIXTAPE_DIR, filename)

    if not os.path.exists(path):
        return jsonify(success=False), 404

    with open(path, "r") as f:
        mixtape = json.load(f)

    # Behoud alleen tracks die nog bestaan
    valid_paths = [p for p in new_order if os.path.exists(p)]
    mixtape["tracks"] = valid_paths
    mixtape["modified"] = datetime.datetime.now().isoformat()

    with open(path, "w") as f:
        json.dump(mixtape, f, indent=2)

    return jsonify(success=True)


# Cover art uploaden
@app.route("/upload_cover/<title>", methods=["POST"])
@login_required
def upload_cover(title):
    if "cover" not in request.files:
        return "Geen file", 400
    file = request.files["cover"]
    if file.filename == "":
        return "Geen file geselecteerd", 400

    filename = secure_filename(title + ".jpg")  # Bijv. JPG
    path = os.path.join(COVER_DIR, filename)
    file.save(path)

    json_filename = secure_filename(title + ".json")
    json_path = os.path.join(MIXTAPE_DIR, json_filename)
    with open(json_path, "r") as f:
        data = json.load(f)
    data["cover"] = path
    data["modified"] = datetime.datetime.now().isoformat()
    with open(json_path, "w") as f:
        json.dump(data, f)
    return redirect(url_for("admin"))


# Mixtape weergeven (publiek, deelbaar via link)
@app.route("/mixtape/<title>")
def mixtape(title):
    filename = secure_filename(title + ".json")
    path = os.path.join(MIXTAPE_DIR, filename)
    if not os.path.exists(path):
        return "Niet gevonden", 404

    with open(path, "r") as f:
        data = json.load(f)

    # Haal tags op voor playlist weergave
    playlist = []
    for track_path in data["tracks"]:
        try:
            audio = mutagen.File(track_path)
            tags = {
                "title": audio.get("TIT2", ["Unknown"])[0],
                "artist": audio.get("TPE1", ["Unknown"])[0],
                "album": audio.get("TALB", ["Unknown"])[0],
            }
        except:
            tags = {
                "title": os.path.basename(track_path),
                "artist": "Unknown",
                "album": "Unknown",
            }
        playlist.append({"path": track_path, "tags": tags})

    share_link = url_for("mixtape", title=title, _external=True)
    return render_template(
        "mixtape.html", data=data, playlist=playlist, share_link=share_link
    )


# Audio streamen
@app.route("/stream/<path:track_path>")
def stream(track_path):
    full_path = os.path.join(MUSIC_DIR, track_path)
    if not os.path.exists(full_path):
        return "Niet gevonden", 404

    def generate():
        with open(full_path, "rb") as f:
            while chunk := f.read(4096):
                yield chunk

    mimetype = (
        "audio/mpeg"
        if track_path.endswith(".mp3")
        else "audio/flac"
        if track_path.endswith(".flac")
        else "audio/ogg"
    )
    return Response(generate(), mimetype=mimetype)


# Lijst van beschikbare tracks voor admin (om toe te voegen)
@app.route("/available_tracks")
@login_required
def available_tracks():
    tracks = [f for f in os.listdir(MUSIC_DIR) if f.endswith((".mp3", ".flac", ".ogg"))]
    return jsonify(tracks)


# Serve cover images
@app.route("/covers/<filename>")
def covers(filename):
    return send_from_directory(COVER_DIR, filename)


if __name__ == "__main__":
    app.run(debug=True)
