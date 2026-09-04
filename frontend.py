import os
import re
import random
import secrets
import hmac
import sqlite3
from contextlib import contextmanager
from difflib import SequenceMatcher
import unicodedata
from flask import (
    Flask,
    abort,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
)
from flask_session import Session

from security import RequestRateLimiter
from backend import LetterboxdDownloadedData

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_TYPE="filesystem",
    SESSION_FILE_DIR=os.path.join(app.instance_path, "sessions"),
    SESSION_PERMANENT=False,
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_COOKIE_SECURE", "0") == "1",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,
)
os.makedirs(app.config["SESSION_FILE_DIR"], exist_ok=True)
Session(app)
limiter = RequestRateLimiter()
GLOBAL_STATS_DATABASE = os.path.join(app.instance_path, "daily_stats.sqlite")


@contextmanager
def connectGlobalStats():
    connection = sqlite3.connect(GLOBAL_STATS_DATABASE, timeout=30)
    connection.execute("PRAGMA busy_timeout = 30000")
    try:
        yield connection
    finally:
        connection.close()


def initializeGlobalStats():
    with connectGlobalStats() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS global_rounds "
            "(id INTEGER PRIMARY KEY CHECK (id = 1), rounds_played INTEGER NOT NULL DEFAULT 0)"
        )
        try:
            previous_count = connection.execute(
                "SELECT COALESCE(SUM(rounds_played), 0) FROM daily_rounds"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            previous_count = 0
        connection.execute(
            "INSERT OR IGNORE INTO global_rounds (id, rounds_played) VALUES (1, ?)",
            (previous_count,),
        )
        connection.commit()


def getGlobalRounds():
    with connectGlobalStats() as connection:
        row = connection.execute(
            "SELECT rounds_played FROM global_rounds WHERE id = 1"
        ).fetchone()
    return row[0] if row else 0


def incrementGlobalRounds():
    with connectGlobalStats() as connection:
        connection.execute(
            "INSERT INTO global_rounds (id, rounds_played) VALUES (1, 1) "
            "ON CONFLICT(id) DO UPDATE SET rounds_played = rounds_played + 1"
        )
        connection.commit()


initializeGlobalStats()


@app.before_request
def protectPostRequests():
    if request.method == "POST":
        expected = session.get("csrf_token", "")
        provided = request.form.get("csrf_token", "")
        if not expected or not hmac.compare_digest(expected, provided):
            abort(400, description="Invalid form token.")


@app.context_processor
def injectCsrfToken():
    token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
    return {"csrf_token": token}


@app.context_processor
def injectGlobalRounds():
    return {"rounds_played_today": getGlobalRounds()}


@app.after_request
def addSecurityHeaders(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' https:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline' https://static.cloudflareinsights.com; connect-src 'self' https://cloudflareinsights.com",
    )
    return response


scraper = LetterboxdDownloadedData(
    os.environ.get(
        "MOVIE_DATABASE_PATH",
        os.path.join(os.path.dirname(__file__), "db", "database.sqlite"),
    )
)
DEFAULT_DATA_FILE = os.path.join(os.path.dirname(__file__), "db", "defaultData.csv")
ROUND_COUNT = 5
HINT_COSTS = {
    "firstLetter": 20,
    "lastLetter": 20,
    "maskedName": 45,
    "cast": 15,
    "rating": 5,
    "userRating": 5,
    "directors": 10,
    "genres": 10,
    "tagline": 10,
    "description": 20,
    "review": 20,
    "year": 10,
}


def hasHintValue(movie, hintName):
    if hintName in ("firstLetter", "lastLetter", "maskedName"):
        return bool(movie.get("title"))
    if hintName == "review":
        reviews = movie.get("reviews") or []
        return bool(reviews and reviews[0].get("review_text"))
    value = movie.get(hintName)
    return bool(value)


def redactNames(value, movie):
    if not isinstance(value, str):
        return value
    names = [movie.get("title", ""), *movie.get("cast", [])]
    titleWords = movie.get("title", "").split()
    if len(titleWords) >= 2:
        names.append(" ".join(titleWords[:2]))
    for name in sorted(
        (name for name in names if isinstance(name, str) and name.strip()),
        key=len,
        reverse=True,
    ):
        value = re.sub(re.escape(name), "*****", value, flags=re.IGNORECASE)
    return value


def titleLetter(title, fromEnd=False):
    letters = [character for character in title if character.isalnum()]
    return (letters[-1] if fromEnd else letters[0]) if letters else ""


def maskTitle(title):
    return "".join("*" if not character.isspace() else character for character in title)


app.jinja_env.filters["redact_names"] = redactNames
app.jinja_env.filters["title_letter"] = titleLetter
app.jinja_env.filters["mask_title"] = maskTitle


def titleWithoutYear(title):
    if not isinstance(title, str):
        return ""
    title = re.sub(r"\s*\(\d{4}\)$", "", title)
    title = unicodedata.normalize("NFKD", title)
    title = "".join(
        character for character in title if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]", "", title.casefold())


def titlesMatch(submitted, answer):
    submittedTitle = titleWithoutYear(submitted)
    answerTitle = titleWithoutYear(answer)
    if not submittedTitle or not answerTitle:
        return False
    if submittedTitle == answerTitle:
        return True
    return SequenceMatcher(None, submittedTitle, answerTitle).ratio() >= 0.86


def makeChoices(movie, movieNames):
    correct = movie["title"]
    distractors = [
        name
        for name in movieNames
        if isinstance(name, str) and titleWithoutYear(name) != titleWithoutYear(correct)
    ]
    choices = random.sample(distractors, min(3, len(distractors)))
    choices.append(correct)
    random.shuffle(choices)
    return choices


@app.route("/", methods=["GET", "POST"])
@limiter.limit("30 per minute")
def home():
    if request.method == "POST":
        data_source = request.form.get("data_source", "default")
        data_file = request.files.get("custom_data")
        mode = request.form.get("mode", "hard")
        try:
            round_count = int(request.form.get("round_count", ROUND_COUNT))
        except ValueError:
            round_count = ROUND_COUNT
        if mode not in ("easy", "hard") or not 1 <= round_count <= 50:
            flash("Choose valid game settings.", "error")
            return render_template("home.html")
        if data_source not in ("default", "custom"):
            flash("Choose a valid data source.", "error")
            return render_template("home.html")
        if data_source == "custom" and (not data_file or not data_file.filename.lower().endswith(".csv")):
            flash("Upload a CSV file exported from Letterboxd.", "error")
            return render_template("home.html")
        try:
            selected_source = data_file if data_source == "custom" else DEFAULT_DATA_FILE
            loaded_movies = scraper._loadExportedMovies(selected_source)
            movies = list(loaded_movies.values())
            if len(movies) < 4:
                raise ValueError("The selected data does not contain at least four matching movies.")
            csrfToken = session.get("csrf_token")
            session.clear()
            session.update(
                {
                    "username": "Custom Letterboxd data" if data_source == "custom" else "Default movie list",
                    "movieData": movies,
                    "movieNames": [movie["title"] for movie in movies],
                    "roundNumber": 0,
                    "roundCount": round_count,
                    "mode": mode,
                    "score": 0,
                    "csrf_token": csrfToken or secrets.token_urlsafe(32),
                }
            )
            return redirect(url_for("next_round"))
        except Exception as error:
            app.logger.exception("Could not load movie data")
            flash(f"Could not load that movie data: {error}", "error")
    return render_template("home.html")


@app.route("/round", endpoint="next_round")
@limiter.limit("120 per minute")
def next_round():
    if not session.get("username"):
        return redirect(url_for("home"))
    if not session.get("movieData") or not session.get("movieNames"):
        session.clear()
        flash("Your game session expired. Start a new game.", "error")
        return redirect(url_for("home"))
    if session.get("roundNumber", 0) >= session.get("roundCount", ROUND_COUNT):
        return redirect(url_for("results"))
    try:
        movie = random.choice(session["movieData"]).copy()
        session["roundNumber"] = session.get("roundNumber", 0) + 1
        incrementGlobalRounds()
        session["round"] = {
            "movie": movie,
            "choices": makeChoices(movie, session["movieNames"]),
            "hints": [],
            "answered": False,
        }
        return redirect(url_for("game"))
    except Exception as error:
        app.logger.exception("Could not load game round")
        flash(f"Could not load a movie right now: {error}", "error")
        return redirect(url_for("home"))


@limiter.limit("120 per minute")
@app.route("/game")
def game():
    if not session.get("round", {}).get("movie"):
        return redirect(url_for("next_round"))
    game_round = session["round"]
    hint_total = sum(
        HINT_COSTS[name] for name in game_round.get("hints", []) if name in HINT_COSTS
    )
    return render_template(
        "game.html",
        round=game_round,
        score=session.get("score", 0),
        round_number=session.get("roundNumber", 0),
        round_count=session.get("roundCount", ROUND_COUNT),
        mode=session.get("mode", "easy"),
        hint_costs={
            name: cost
            for name, cost in HINT_COSTS.items()
            if hasHintValue(game_round["movie"], name)
        },
        movie_names=[
            name for name in session.get("movieNames", []) if isinstance(name, str)
        ],
        round_points=max(0, 100 - hint_total),
    )


@app.post("/guess")
@limiter.limit("120 per minute")
def guess():
    game_round = session.get("round", {})
    if not game_round or game_round.get("answered"):
        return redirect(url_for("game"))
    selected = request.form.get("answer", "")
    movie = game_round["movie"]
    correct = titlesMatch(selected, movie["title"])
    game_round.update(
        {"answered": True, "correct": correct, "submittedAnswer": selected}
    )
    session["round"] = game_round
    if correct:
        session["score"] = session.get("score", 0) + 100
    return redirect(url_for("game"))


@app.post("/skip")
@limiter.limit("60 per minute")
def skip():
    game_round = session.get("round", {})
    if game_round and not game_round.get("answered"):
        game_round.update({"answered": True, "correct": False, "skipped": True})
        session["round"] = game_round
    return redirect(url_for("game"))


@app.post("/hint/<hint_name>")
@limiter.limit("120 per minute")
def hint(hint_name):
    game_round = session.get("round", {})
    if (
        hint_name not in HINT_COSTS
        or not game_round
        or game_round.get("answered")
        or not hasHintValue(game_round.get("movie", {}), hint_name)
    ):
        return redirect(url_for("game"))
    if hint_name not in game_round["hints"]:
        game_round["hints"].append(hint_name)
        session["score"] = max(0, session.get("score", 0) - HINT_COSTS[hint_name])
        session["round"] = game_round
    return redirect(url_for("game"))


@app.route("/results")
def results():
    if not session.get("username"):
        return redirect(url_for("home"))
    return render_template(
        "results.html", score=session.get("score", 0), round_count=ROUND_COUNT
    )


if __name__ == "__main__":
    app.run(debug=False)
    # app.run(debug=True)
