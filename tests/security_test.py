import unittest

import backend


class FakeScraper:
    def __init__(self):
        self.load_calls = 0

    def getAllUserMovieNames(self, username):
        return ["Arrival", "Moon", "The Matrix", "Dune"]

    def loadRandomMovie(self, username):
        self.load_calls += 1
        return {
            "title": "Arrival",
            "year": "2016",
            "image": "https://example.com/poster.jpg",
            "rating": "4.0",
            "userRating": 4,
            "cast": ["Amy Adams"],
            "directors": ["Denis Villeneuve"],
            "genres": ["Drama"],
            "tagline": "A safe test tagline",
            "description": "A safe test description with several words",
        }


class SecurityEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_scraper = backend.scraper
        backend.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)

    @classmethod
    def tearDownClass(cls):
        backend.scraper = cls.original_scraper

    def setUp(self):
        backend.scraper = FakeScraper()
        backend.limiter._requests.clear()
        self.client = backend.app.test_client()

    def csrf_token(self):
        self.client.get("/")
        with self.client.session_transaction() as current_session:
            return current_session["csrf_token"]

    def test_get_endpoints_and_security_headers(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/game").status_code, 302)
        self.assertEqual(self.client.get("/round").status_code, 302)
        self.assertEqual(self.client.get("/results").status_code, 302)
        response = self.client.get("/")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_post_requires_csrf_and_rejects_injection_username(self):
        self.assertEqual(self.client.post("/", data={"username": "alice"}).status_code, 400)
        token = self.csrf_token()
        response = self.client.post(
            "/",
            data={"csrf_token": token, "username": "<script>alert(1)</script>", "mode": "easy", "round_count": "5"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"<script>alert(1)</script>", response.data)

    def test_game_post_endpoints_and_malformed_values(self):
        token = self.csrf_token()
        self.client.post("/", data={"csrf_token": token, "username": "alice", "mode": "easy", "round_count": "1"})
        self.client.get("/round")
        token = self.csrf_token()
        self.assertEqual(self.client.post("/hint/%3Cscript%3E", data={"csrf_token": token}).status_code, 302)
        self.assertEqual(self.client.post("/guess", data={"csrf_token": token, "answer": "<script>alert(1)</script>"}).status_code, 302)
        self.assertEqual(self.client.post("/skip", data={"csrf_token": token}).status_code, 302)

    def test_post_methods_are_restricted(self):
        self.assertEqual(self.client.get("/guess").status_code, 405)
        self.assertEqual(self.client.get("/skip").status_code, 405)
        self.assertEqual(self.client.get("/hint/cast").status_code, 405)

    def test_round_rate_limit(self):
        for _ in range(30):
            self.assertEqual(self.client.get("/round").status_code, 302)
        self.assertEqual(self.client.get("/round").status_code, 429)


if __name__ == "__main__":
    unittest.main()
