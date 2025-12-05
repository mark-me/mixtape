import json
import os
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required
from werkzeug.utils import secure_filename

MIXTAPE_DIR = "mixtapes"
COVER_DIR = "covers"

editor = Blueprint("editor", __name__)


@editor.route("/<title>", methods=["GET", "POST"])
@login_required
def edit_mixtape(title):
    """Edits the details and tracks of a specific mixtape.

    This endpoint allows updating the mixtape's title, adding or removing tracks, and changing the cover art.
    It handles both GET and POST requests for editing mixtape metadata and contents.

    Args:
        title (str): The title of the mixtape to edit.

    Returns:
        Response: Renders the edit page for GET requests, or redirects after processing POST actions.
    """
    filename = secure_filename(f"{title}.json")
    path = os.path.join(MIXTAPE_DIR, filename)

    if not os.path.exists(path):
        flash("Mixtape niet gevonden", "danger")
        return redirect(url_for("admin"))

    with open(path, "r") as f:
        data = json.load(f)

    available_tracks = _get_available_tracks()
    current_tracks = _get_current_tracks(data)

    if request.method == "POST":
        return _handle_edit_post_request(title, path, data)
    return render_template(
        "edit.html",
        mixtape=data,
        current_tracks=current_tracks,
        available_tracks=available_tracks,
    )


def _handle_edit_post_request(title, path, data):
    """Handles POST requests for editing a mixtape.

    This function processes form and JSON requests to update the mixtape's title, add or remove tracks, or update the cover art.

    Args:
        title (str): The title of the mixtape being edited.
        path (str): The file path to the mixtape JSON.
        data (dict): The mixtape data.

    Returns:
        Response: A redirect or JSON response based on the action performed.
    """
    action = request.form.get("action")
    if request.content_type.is_json:
        data_json = request.get_json()
        if data_json.get("action") == "add_tracks":
            return _add_tracks_json(data_json, data, path)
    if action == "update_title":
        return _update_title(title, path, data)
    elif action == "add_tracks":
        return _add_tracks_form(data, path)
    elif action == "remove_track":
        return _remove_track(data, path)
    elif "cover" in request.files and request.files["cover"].filename:
        return _update_cover(title, data, path)
    return redirect(url_for("edit_mixtape", title=title))


def _update_title(title, path, data):
    """Updates the title of a mixtape and handles renaming associated files.

    This function processes a form request to change the mixtape's title, renames the mixtape JSON and cover files if needed, and updates the mixtape metadata.

    Args:
        title (str): The current title of the mixtape.
        path (str): The file path to the current mixtape JSON.
        data (dict): The mixtape data to update.

    Returns:
        Response: Redirects to the edit page for the new or current title.
    """
    new_title = request.form["title"].strip()
    if not new_title:
        flash("Titel mag niet leeg zijn", "danger")
    elif new_title != title and os.path.exists(
        os.path.join(MIXTAPE_DIR, secure_filename(new_title + ".json"))
    ):
        flash("Er bestaat al een mixtape met deze titel", "danger")
    else:
        new_filename = secure_filename(new_title + ".json")
        os.rename(path, os.path.join(MIXTAPE_DIR, new_filename))
        if data.get("cover"):
            old_cover = data["cover"]
            new_cover = os.path.join(COVER_DIR, secure_filename(new_title + ".jpg"))
            if os.path.exists(old_cover):
                os.rename(old_cover, new_cover)
            data["cover"] = new_cover

        data["title"] = new_title
        data["modified"] = datetime.datetime.now().isoformat()
        with open(os.path.join(MIXTAPE_DIR, new_filename), "w") as f:
            json.dump(data, f, indent=2)
        flash("Titel bijgewerkt!", "success")
        return redirect(url_for("edit_mixtape", title=new_title))
    return redirect(url_for("edit_mixtape", title=title))

def _get_available_tracks():
    """Returns a list of available music tracks in the music directory.

    This function scans the music directory and returns all files with supported audio extensions.

    Returns:
        list: A list of filenames for available music tracks.
    """
    return [
        f
        for f in os.listdir(MUSIC_DIR)
        if f.lower().endswith((".mp3", ".flac", ".ogg", ".oga"))
    ]


def _get_current_tracks(data):
    """Returns a list of current tracks with metadata for a mixtape.

    This function retrieves the tracks from the mixtape data and extracts their tags such as title, artist, and album.

    Args:
        data (dict): The mixtape data containing track paths.

    Returns:
        list: A list of dictionaries with track path, filename, and tags.
    """
    current_tracks = []
    for track_path in data.get("tracks", []):
        full_path = os.path.join(MUSIC_DIR, track_path.split("/")[-1])
        try:
            audio = mutagen.File(full_path)
            tags = {
                "title": str(audio.get("TIT2", [os.path.basename(track_path)])[0]),
                "artist": str(audio.get("TPE1", ["Onbekend"])[0]),
                "album": str(audio.get("TALB", [""])[0]),
            }
        except (mutagen.MutagenError, FileNotFoundError, AttributeError, TypeError):
            tags = {
                "title": os.path.basename(track_path),
                "artist": "Onbekend",
                "album": "",
            }
        current_tracks.append(
            {"path": track_path, "filename": os.path.basename(track_path), "tags": tags}
        )
    return current_tracks