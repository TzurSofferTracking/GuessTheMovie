import os
import re
import random
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_session import Session
from scraper import LetterboxdScraper


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "guess-the-movie-dev-key")
app.config.update(
    SESSION_TYPE="filesystem",
    SESSION_FILE_DIR=os.path.join(app.instance_path, "sessions"),
    SESSION_PERMANENT=False,
)
os.makedirs(app.config["SESSION_FILE_DIR"], exist_ok=True)
Session(app)
scraper = LetterboxdScraper()
ROUND_COUNT = 5
HINT_COSTS = {"cast": 20, "rating": 15, "userRating": 15, "director": 10, "year": 10}


def title_without_year(title):
    if not isinstance(title, str):
        return ""
    return re.sub(r"\s*\(\d{4}\)$", "", title).strip().casefold()


def make_choices(movie, movie_names):
    correct = f"{movie['title']} ({movie['year']})"
    distractors = [
        name for name in movie_names
        if isinstance(name, str) and title_without_year(name) != title_without_year(correct)
    ]
    choices = random.sample(distractors, min(3, len(distractors)))
    choices.append(correct)
    random.shuffle(choices)
    return choices


@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        username = request.form.get("username", "").strip().strip("/")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", username):
            flash("Enter a valid Letterboxd username.", "error")
            return render_template("home.html")
        try:
            movies = scraper.getAllUserMovieNames(username)
            if len(movies) < 4:
                raise ValueError("This profile does not have enough films for a game.")
            session.clear()
            session.update({"username": username, "movie_names": movies, "round_number": 0, "score": 0})
            return redirect(url_for("next_round"))
        except Exception as error:
            app.logger.exception("Could not load Letterboxd profile")
            flash(f"Could not load that profile: {error}", "error")
    return render_template("home.html")


@app.route("/round")
def next_round():
    if not session.get("username"):
        return redirect(url_for("home"))
    if session.get("round_number", 0) >= ROUND_COUNT:
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
    return render_template("game.html", round=session["round"], score=session.get("score", 0), round_number=session.get("round_number", 0), round_count=ROUND_COUNT, hint_costs=HINT_COSTS)


@app.post("/guess")
def guess():
    game_round = session.get("round", {})
    if not game_round or game_round.get("answered"):
        return redirect(url_for("game"))
    selected = request.form.get("answer", "")
    movie = game_round["movie"]
    correct = title_without_year(selected) == title_without_year(f"{movie['title']} ({movie['year']})")
    game_round.update({"answered": True, "correct": correct})
    session["round"] = game_round
    if correct:
        session["score"] = session.get("score", 0) + 100
    return redirect(url_for("game"))


@app.post("/skip")
def skip():
    game_round = session.get("round", {})
    if game_round and not game_round.get("answered"):
        game_round.update({"answered": True, "correct": False, "skipped": True})
        session["round"] = game_round
    return redirect(url_for("game"))


@app.post("/hint/<hint_name>")
def hint(hint_name):
    game_round = session.get("round", {})
    if hint_name not in HINT_COSTS or not game_round or game_round.get("answered"):
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
    app.run(debug=True)