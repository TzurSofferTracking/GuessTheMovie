import os
import re
import random
import secrets
import hmac
from difflib import SequenceMatcher
import unicodedata
from flask import Flask, abort, render_template, request, redirect, url_for, session, flash
from flask_session import Session
from scraper import LetterboxdScraper
from security import RequestRateLimiter


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_TYPE="filesystem",
    SESSION_FILE_DIR=os.path.join(app.instance_path, "sessions"),
    SESSION_PERMANENT=False,
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_COOKIE_SECURE", "0") == "1",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=16 * 1024,
)
os.makedirs(app.config["SESSION_FILE_DIR"], exist_ok=True)
Session(app)
limiter = RequestRateLimiter()


@app.before_request
def protect_post_requests():
    if request.method == "POST":
        expected = session.get("csrf_token", "")
        provided = request.form.get("csrf_token", "")
        if not expected or not hmac.compare_digest(expected, provided):
            abort(400, description="Invalid form token.")


@app.context_processor
def inject_csrf_token():
    token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
    return {"csrf_token": token}


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' https:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'")
    return response
scraper = LetterboxdScraper()
ROUND_COUNT = 5
HINT_COSTS = {"image": 25, "cast": 20, "rating": 15, "userRating": 15, "director": 10, "genres": 10, "tagline": 10, "description": 20, "year": 10}


def has_hint_value(movie, hint_name):
    value = movie.get(hint_name)
    return bool(value)


def redact_names(value, movie):
    if not isinstance(value, str):
        return value
    names = [movie.get("title", ""), *movie.get("cast", [])]
    title_words = movie.get("title", "").split()
    if len(title_words) >= 2:
        names.append(" ".join(title_words[:2]))
    for name in sorted((name for name in names if isinstance(name, str) and name.strip()), key=len, reverse=True):
        value = re.sub(re.escape(name), "*****", value, flags=re.IGNORECASE)
    return value


app.jinja_env.filters["redact_names"] = redact_names


def title_without_year(title):
    if not isinstance(title, str):
        return ""
    title = re.sub(r"\s*\(\d{4}\)$", "", title)
    title = unicodedata.normalize("NFKD", title)
    title = "".join(character for character in title if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]", "", title.casefold())


def titles_match(submitted, answer):
    submitted_title = title_without_year(submitted)
    answer_title = title_without_year(answer)
    if not submitted_title or not answer_title:
        return False
    if submitted_title == answer_title:
        return True
    return SequenceMatcher(None, submitted_title, answer_title).ratio() >= 0.86


def make_choices(movie, movie_names):
    correct = movie["title"]
    distractors = [
        name for name in movie_names
        if isinstance(name, str) and title_without_year(name) != title_without_year(correct)
    ]
    choices = random.sample(distractors, min(3, len(distractors)))
    choices.append(correct)
    random.shuffle(choices)
    return choices


@app.route("/", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def home():
    if request.method == "POST":
        username = request.form.get("username", "").strip().strip("/")
        mode = request.form.get("mode", "easy")
        try:
            round_count = int(request.form.get("round_count", ROUND_COUNT))
        except ValueError:
            round_count = ROUND_COUNT
        if mode not in ("easy", "hard") or not 1 <= round_count <= 50:
            flash("Choose valid game settings.", "error")
            return render_template("home.html")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", username):
            flash("Enter a valid Letterboxd username.", "error")
            return render_template("home.html")
        try:
            movies = scraper.getAllUserMovieNames(username)
            if len(movies) < 4:
                raise ValueError("This profile does not have enough films for a game.")
            session.clear()
            session.update({"username": username, "movie_names": movies, "round_number": 0, "round_count": round_count, "mode": mode, "score": 0})
            return redirect(url_for("next_round"))
        except Exception as error:
            app.logger.exception("Could not load Letterboxd profile")
            flash(f"Could not load that profile: {error}", "error")
    return render_template("home.html")


@app.route("/round")
@limiter.limit("30 per minute")
def next_round():
    if not session.get("username"):
        return redirect(url_for("home"))
    if session.get("round_number", 0) >= session.get("round_count", ROUND_COUNT):
        return redirect(url_for("results"))
    try:
        movie = scraper.loadRandomMovie(session["username"])
        session["round_number"] = session.get("round_number", 0) + 1
        session["round"] = {"movie": movie, "choices": make_choices(movie, session["movie_names"]), "hints": [], "answered": False}
        return redirect(url_for("game"))
    except Exception as error:
        app.logger.exception("Could not load game round")
        flash(f"Could not load a movie right now: {error}", "error")
        return redirect(url_for("home"))


@app.route("/game")
def game():
    if not session.get("round", {}).get("movie"):
        return redirect(url_for("next_round"))
    game_round = session["round"]
    hint_total = sum(HINT_COSTS[name] for name in game_round.get("hints", []) if name in HINT_COSTS)
    return render_template(
        "game.html",
        round=game_round,
        score=session.get("score", 0),
        round_number=session.get("round_number", 0),
        round_count=session.get("round_count", ROUND_COUNT),
        mode=session.get("mode", "easy"),
        hint_costs={name: cost for name, cost in HINT_COSTS.items() if has_hint_value(game_round["movie"], name)},
        movie_names=[name for name in session.get("movie_names", []) if isinstance(name, str)],
        round_points=max(0, 100 - hint_total),
    )


@app.post("/guess")
@limiter.limit("60 per minute")
def guess():
    game_round = session.get("round", {})
    if not game_round or game_round.get("answered"):
        return redirect(url_for("game"))
    selected = request.form.get("answer", "")
    movie = game_round["movie"]
    correct = titles_match(selected, movie["title"])
    game_round.update({"answered": True, "correct": correct})
    session["round"] = game_round
    if correct:
        session["score"] = session.get("score", 0) + 100
    return redirect(url_for("game"))


@app.post("/skip")
@limiter.limit("30 per minute")
def skip():
    game_round = session.get("round", {})
    if game_round and not game_round.get("answered"):
        game_round.update({"answered": True, "correct": False, "skipped": True})
        session["round"] = game_round
    return redirect(url_for("game"))


@app.post("/hint/<hint_name>")
@limiter.limit("60 per minute")
def hint(hint_name):
    game_round = session.get("round", {})
    if hint_name not in HINT_COSTS or not game_round or game_round.get("answered") or not has_hint_value(game_round.get("movie", {}), hint_name):
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
    return render_template("results.html", score=session.get("score", 0), round_count=ROUND_COUNT)

if __name__ == "__main__":
    app.run(debug=False)
    # app.run(debug=True)