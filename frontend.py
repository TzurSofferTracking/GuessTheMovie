import os
import random
import re
import hmac
import secrets
import unicodedata

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from flask_session import Session
from scraper import LetterboxdScraper
from security import RequestRateLimiter


class GuessTheMovieFrontend:
    ROUND_DEFAULT = 5
    ROUND_OPTIONS = (3, 5, 10, 15)
    TOP_250_URL = "https://letterboxd.com/dave/list/official-top-250-films/"
    HINT_COSTS = {
        "image": 25,
        "cast": 20,
        "rating": 15,
        "userRating": 15,
        "director": 10,
        "genres": 10,
        "tagline": 10,
        "description": 20,
        "year": 10,
    }

    def __init__(self, scraper=None):
        self.scraper = scraper or LetterboxdScraper()
        self.app = Flask(__name__)
        self.app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
        self.app.config.update(
            SESSION_TYPE="filesystem",
            SESSION_FILE_DIR=os.path.join(self.app.instance_path, "sessions"),
            SESSION_PERMANENT=False,
            SESSION_COOKIE_SECURE=os.environ.get("FLASK_COOKIE_SECURE", "0") == "1",
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE="Lax",
            MAX_CONTENT_LENGTH=16 * 1024,
        )
        os.makedirs(self.app.config["SESSION_FILE_DIR"], exist_ok=True)
        Session(self.app)
        self.limiter = RequestRateLimiter()
        self.app.jinja_env.filters["redact_names"] = self._redact_names
        self._register_security_hooks()
        self._register_routes()

    def _register_security_hooks(self):
        @self.app.before_request
        def protect_post_requests():
            if request.method == "POST":
                expected = session.get("csrf_token", "")
                provided = request.form.get("csrf_token", "")
                if not expected or not hmac.compare_digest(expected, provided):
                    abort(400, description="Invalid form token.")

        @self.app.context_processor
        def inject_csrf_token():
            token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
            return {"csrf_token": token}

        @self.app.after_request
        def add_security_headers(response):
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
            response.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' https:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'")
            return response

    @staticmethod
    def _normalize_title(title):
        if not isinstance(title, str):
            return ""
        title = re.sub(r"\s*\(\d{4}\)$", "", title)
        title = unicodedata.normalize("NFKD", title)
        title = "".join(character for character in title if not unicodedata.combining(character))
        return re.sub(r"[^a-z0-9]", "", title.casefold())

    @classmethod
    def _titles_match(cls, submitted, answer):
        submitted_title = cls._normalize_title(submitted)
        answer_title = cls._normalize_title(answer)
        if not submitted_title or not answer_title:
            return False
        if submitted_title == answer_title:
            return True
        from difflib import SequenceMatcher
        return SequenceMatcher(None, submitted_title, answer_title).ratio() >= 0.86

    @staticmethod
    def _redact_names(value, movie):
        if not isinstance(value, str):
            return value
        names = [movie.get("title", ""), *movie.get("cast", [])]
        title_words = movie.get("title", "").split()
        if len(title_words) >= 2:
            names.append(" ".join(title_words[:2]))
        for name in sorted((name for name in names if isinstance(name, str) and name.strip()), key=len, reverse=True):
            value = re.sub(re.escape(name), "*****", value, flags=re.IGNORECASE)
        return value

    def _make_choices(self, movie, movie_names):
        correct = movie["title"]
        distractors = [name for name in movie_names if isinstance(name, str) and self._normalize_title(name) != self._normalize_title(correct)]
        choices = random.sample(distractors, min(3, len(distractors)))
        choices.append(correct)
        random.shuffle(choices)
        return choices

    def _available_hint_costs(self, movie):
        return {
            name: cost for name, cost in self.HINT_COSTS.items()
            if movie.get(name)
        }

    def _top_250_movies(self):
        soup = self.scraper.getHtml(self.TOP_250_URL)
        grid = soup.find("div", class_="poster-grid")
        if not grid:
            raise ValueError("Could not load the Letterboxd Top 250 list.")
        movies = []
        for item in grid.find_all("li", class_="griditem"):
            component = item.find("div", class_="react-component")
            if not component:
                continue
            name = component.get("data-item-name") or item.find("img").get("alt", "")
            path = component.get("data-item-link")
            if name and path:
                movies.append({"name": name, "url": f"https://letterboxd.com{path}"})
        if len(movies) < 4:
            raise ValueError("Could not find enough films in the Top 250 list.")
        return movies

    def _load_top_250_movie(self):
        movies = self._top_250_movies()
        selected = random.choice(movies)
        movie = self.scraper._loadMovieDetails(selected["url"])
        movie["title"] = selected["name"]
        return movie, [item["name"] for item in movies]

    def _load_user_movie(self, username, movie_names):
        return self.scraper.loadRandomMovie(username), movie_names

    def _register_routes(self):
        @self.app.route("/", methods=["GET", "POST"])
        @self.limiter.limit("10 per minute")
        def home():
            if request.method == "POST":
                source = request.form.get("source", "user")
                username = request.form.get("username", "").strip().strip("/")
                mode = request.form.get("mode", "easy")
                try:
                    round_count = int(request.form.get("round_count", self.ROUND_DEFAULT))
                except ValueError:
                    round_count = self.ROUND_DEFAULT
                if source == "user" and not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", username):
                    flash("Enter a valid Letterboxd username.", "error")
                    return render_template("home.html")
                if not 1 <= round_count <= 50 or mode not in ("easy", "hard") or source != "user":
                    flash("Choose valid game settings.", "error")
                    return render_template("home.html")
                try:
                    movie_names = self.scraper.getAllUserMovieNames(username)
                    if len(movie_names) < 4:
                        raise ValueError("This profile does not have enough films for a game.")
                    session.clear()
                    session.update({
                        "username": username,
                        "source": "user",
                        "mode": mode,
                        "round_count": round_count,
                        "movie_names": movie_names,
                        "round_number": 0,
                        "score": 0,
                    })
                    return redirect(url_for("next_round"))
                except Exception as error:
                    self.app.logger.exception("Could not load game source")
                    flash(f"Could not start that game: {error}", "error")
            return render_template("home.html", round_options=self.ROUND_OPTIONS)

        @self.app.route("/round")
        @self.limiter.limit("30 per minute")
        def next_round():
            if not session.get("source"):
                return redirect(url_for("home"))
            if session.get("round_number", 0) >= session.get("round_count", self.ROUND_DEFAULT):
                return redirect(url_for("results"))
            try:
                movie, movie_names = self._load_user_movie(session["username"], session["movie_names"])
                session["round_number"] = session.get("round_number", 0) + 1
                session["round"] = {
                    "movie": movie,
                    "choices": self._make_choices(movie, movie_names),
                    "hints": [],
                    "answered": False,
                }
                return redirect(url_for("game"))
            except Exception as error:
                self.app.logger.exception("Could not load game round")
                flash(f"Could not load a movie right now: {error}", "error")
                return redirect(url_for("home"))

        @self.app.route("/game")
        def game():
            if not session.get("round", {}).get("movie"):
                return redirect(url_for("next_round"))
            game_round = session["round"]
            hint_total = sum(
                self.HINT_COSTS[name]
                for name in game_round.get("hints", [])
                if name in self.HINT_COSTS
            )
            return render_template(
                "game.html",
                round=game_round,
                score=session.get("score", 0),
                round_number=session.get("round_number", 0),
                round_count=session.get("round_count", self.ROUND_DEFAULT),
                mode=session.get("mode", "easy"),
                hint_costs=self._available_hint_costs(session["round"]["movie"]),
                round_points=max(0, 100 - hint_total),
            )

        @self.app.post("/guess")
        @self.limiter.limit("60 per minute")
        def guess():
            game_round = session.get("round", {})
            if not game_round or game_round.get("answered"):
                return redirect(url_for("game"))
            selected = request.form.get("answer", "").strip()
            movie = game_round["movie"]
            correct = self._titles_match(selected, movie["title"])
            game_round.update({"answered": True, "correct": correct})
            session["round"] = game_round
            if correct:
                session["score"] = session.get("score", 0) + 100
            return redirect(url_for("game"))

        @self.app.post("/skip")
        @self.limiter.limit("30 per minute")
        def skip():
            game_round = session.get("round", {})
            if game_round and not game_round.get("answered"):
                game_round.update({"answered": True, "correct": False, "skipped": True})
                session["round"] = game_round
            return redirect(url_for("game"))

        @self.app.post("/hint/<hint_name>")
        @self.limiter.limit("60 per minute")
        def hint(hint_name):
            game_round = session.get("round", {})
            if hint_name not in self.HINT_COSTS or not game_round or game_round.get("answered") or not game_round.get("movie", {}).get(hint_name):
                return redirect(url_for("game"))
            if hint_name not in game_round["hints"]:
                game_round["hints"].append(hint_name)
                session["score"] = max(0, session.get("score", 0) - self.HINT_COSTS[hint_name])
                session["round"] = game_round
            return redirect(url_for("game"))

        @self.app.route("/results")
        def results():
            if not session.get("source"):
                return redirect(url_for("home"))
            return render_template("results.html", score=session.get("score", 0), round_count=session.get("round_count", self.ROUND_DEFAULT))


frontend = GuessTheMovieFrontend()
app = frontend.app

if __name__ == "__main__":
    app.run(debug=True)
