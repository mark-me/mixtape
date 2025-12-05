import json
import os
from datetime import datetime

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import login_required
from werkzeug.utils import secure_filename

MIXTAPE_DIR = "mixtapes"

manager = Blueprint("manager", __name__)


@manager.route("/", methods=["GET", "POST"])
@login_required
def admin():
    """Displays the admin page with a list of mixtapes.

    This endpoint renders the admin interface, showing all mixtapes sorted by the selected criterion.

    Returns:
        Response: The rendered admin HTML page.
    """
    sort_by = request.args.get("sort", "alpha")
    mixtapes = load_mixtapes(sort_by)
    return render_template("admin.html", mixtapes=mixtapes, sort_by=sort_by)


def load_mixtapes(sort_by="alpha"):
    """Loads all mixtapes from disk and sorts them by the specified criterion.

    This function reads all mixtape JSON files from the mixtape directory and returns a sorted list of mixtape metadata.

    Args:
        sort_by (str, optional): The sorting criterion ('alpha', 'created', or 'modified'). Defaults to "alpha".

    Returns:
        list: A list of dictionaries containing mixtape metadata.
    """
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


@manager.route("/create_mixtape", methods=["POST"])
@login_required
def create_mixtape():
    """Creates a new mixtape with the given title.

    This endpoint handles the creation of a new mixtape, initializing its metadata and saving it to disk.

    Returns:
        Response: Redirects to the admin page after creating the mixtape, or returns an error if the title is invalid or already exists.
    """
    title = request.form["title"]
    if not title:
        return "Titel vereist", 400
    filename = secure_filename(f"{title}.json")
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


@manager.route("/clone_mixtape/<title>", methods=["POST"])
@login_required
def clone_mixtape(title):
    """Creates a copy of an existing mixtape with a new title.

    This endpoint duplicates the specified mixtape, assigning it a new title and updated timestamps.

    Args:
        title (str): The title of the mixtape to clone.

    Returns:
        Response: Redirects to the admin page after cloning, or returns an error if the original mixtape is not found.
    """
    old_filename = secure_filename(f"{title}.json")
    old_path = os.path.join(MIXTAPE_DIR, old_filename)
    if not os.path.exists(old_path):
        return "Niet gevonden", 404

    with open(old_path, "r") as f:
        data = json.load(f)

    new_title = f"{title}_clone"
    new_filename = secure_filename(f"{new_title}.json")
    data["title"] = new_title
    data["created"] = datetime.datetime.now().isoformat()
    data["modified"] = datetime.datetime.now().isoformat()

    with open(os.path.join(MIXTAPE_DIR, new_filename), "w") as f:
        json.dump(data, f)
    return redirect(url_for("admin"))


@manager.route("/delete_mixtape/<title>", methods=["POST"])
@login_required
def delete_mixtape(title):
    """Deletes the specified mixtape from the server.

    This endpoint removes the mixtape JSON file if it exists and redirects to the admin page.

    Args:
        title (str): The title of the mixtape to delete.

    Returns:
        Response: Redirects to the admin page after deletion.
    """
    filename = secure_filename(f"{title}.json")
    path = os.path.join(MIXTAPE_DIR, filename)
    if os.path.exists(path):
        os.remove(path)
    return redirect(url_for("admin"))
