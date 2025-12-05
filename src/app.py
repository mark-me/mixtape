from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for
)
from flask_login import (
    LoginManager,
    UserMixin,
    login_required,
    login_user,
    logout_user,
)
from .routes import manager, editor

app = Flask(__name__)
app.secret_key = "your_secret_key"  # Verander dit in productie!
app.register_blueprint(manager, url_prefix="/manager")
app.register_blueprint(editor, url_prefix="/editor")


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


@app.route("/login", methods=["GET", "POST"])
def login():
    """Handles user login and authentication.

    This endpoint verifies the provided username and password, logs in the user if credentials are correct, and redirects to the admin page. If authentication fails, it displays an error message.

    Returns:
        Response: Redirects to the admin page on success, or renders the login page with an error on failure.
    """
    error = None
    if request.method == "POST":
        if (
            request.form["username"] == ADMIN_USERNAME
            and request.form["password"] == ADMIN_PASSWORD
        ):
            user = User(1)
            login_user(user)
            return redirect(url_for("admin"))
        else:
            error = "Invalid username or password. Please try again."
    return render_template("login.html", error=error)


@app.route("/logout")
@login_required
def logout():
    """Logs out the current user and redirects to the login page.

    This endpoint ends the user's session and returns them to the login screen.

    Returns:
        Response: Redirects to the login page after logging out.
    """
    logout_user()
    return redirect(url_for("login"))
