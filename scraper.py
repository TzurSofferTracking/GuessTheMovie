import random

from bs4 import BeautifulSoup
from curl_cffi import requests


class Scraper:
    def loadHtmlFromFile(self, filename):
        with open(filename, "r", encoding="utf-8") as file:
            return BeautifulSoup(file.read(), "html.parser")

    def getHtml(self, url):
        raise NotImplementedError("This method should be implemented in a subclass.")

    def loadRandomMovie(self, username):
        raise NotImplementedError("This method should be implemented in a subclass.")

    def getAllUserMovieNames(self, username):
        raise NotImplementedError("This method should be implemented in a subclass.")

    def save(self, soup):
        filename = soup.get("class", ["No-Class"])[0] + ".html"
        with open(filename, "w", encoding="utf-8") as file:
            file.write(soup.prettify())


class LetterboxdScraper(Scraper):
    def getHtml(self, url):
        response = requests.get(url, impersonate="chrome120")
        return BeautifulSoup(response.text, "html.parser")

    def _loadMovieDetails(self, movieUrl):
        soup = self.getHtml(movieUrl)
        self.save(soup)
        title = soup.find("h1", class_="headline-1").text.strip()
        year = soup.find("span", class_="releasedate").text.strip()
        image = soup.find("div", class_="film-poster").find("img").get("src")
        rating = soup.find("meta", {"name": "twitter:data2"}).get("content").removesuffix(" out of 5")

        castSoup = soup.find("div", class_="cast-list").find_all("a", class_="text-slug tooltip")
        cast = [actor.text.strip() for actor in castSoup]

        directorsSoup = soup.find("span", class_="contributorlist").find_all("a", class_="contributor")
        directors = [director.text.strip() for director in directorsSoup]

        genresSoup = soup.find("div", id="tab-panel-genres").find_all("a", class_="text-slug")
        genres = [genre.text.strip() for genre in genresSoup]

        return {
            "title": title,
            "year": year,
            "image": image,
            "rating": rating,
            "cast": cast,
            "directors": directors,
            "genres": genres,
            "userRating": None,
        }

    def _getPageCountForUser(self, username):
        url = f"https://letterboxd.com/{username}/films/"
        soup = self.getHtml(url)
        pages = soup.find_all("li", class_="paginate-page")
        if len(pages) == 0:
            return 1
        return int(pages[-1].text.strip())

    def loadRandomMovie(self, username):
        pageCount = self._getPageCountForUser(username)
        userPage = random.randint(1, pageCount)
        url = f"https://letterboxd.com/{username}/films/page/{userPage}/"

        soup = self.getHtml(url)
        movieGrid = soup.find("div", class_="poster-grid")
        movies = movieGrid.find_all("li", class_="griditem")
        movieSoup = random.choice(movies)
        movieUrl = movieSoup.find("div", class_="react-component").get("data-item-link")
        fullMovieUrl = f"https://letterboxd.com{movieUrl}"

        movie = self._loadMovieDetails(fullMovieUrl)
        rating = movieSoup.find("span", class_="rating")
        if rating:
            ratingText = rating.text.strip()
            movie["userRating"] = ratingText.count("★") + ratingText.count("½") / 2
        return movie

    def getAllUserMovieNames(self, username):
        pageCount = self._getPageCountForUser(username)
        allMovies = []
        for userPage in range(1, pageCount + 1):
            url = f"https://letterboxd.com/{username}/films/page/{userPage}/"
            soup = self.getHtml(url)
            movieGrid = soup.find("div", class_="poster-grid")
            movies = movieGrid.find_all("li", class_="griditem")
            for movieSoup in movies:
                movieName = movieSoup.find("img").get("img-alt")
                allMovies.append(movieName)
        return allMovies
