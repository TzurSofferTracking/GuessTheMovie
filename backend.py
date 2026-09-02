import random

from bs4 import BeautifulSoup
from curl_cffi import requests


class Template:
    def loadRandomMovie(self, username):
        raise NotImplementedError("This method should be implemented in a subclass.")

    def getAllUserMovieNames(self, username):
        raise NotImplementedError("This method should be implemented in a subclass.")

    def save(self, soup):
        raise NotImplementedError("This method should be implemented in a subclass.")

class Scraper(Template):
    def loadHtmlFromFile(self, fileName):
        with open(fileName, "r", encoding="utf-8") as file:
            return BeautifulSoup(file.read(), "html.parser")

    def getHtml(self, url):
        raise NotImplementedError("This method should be implemented in a subclass.")

    def save(self, soup):
        fileName = soup.get("class", ["No-Class"])[0] + ".html"
        with open(fileName, "w", encoding="utf-8") as file:
            file.write(soup.prettify())

class LetterboxdScraper(Scraper):
    def getHtml(self, url):
        response = requests.get(url, impersonate="chrome120", timeout=15)
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")

    @staticmethod
    def _getMovieGrid(soup):
        movieGrid = soup.find("div", class_="poster-grid")
        if movieGrid is None:
            raise ValueError(
                "Letterboxd did not return a movie list. The profile may be private or temporarily blocked."
            )
        return movieGrid

    def _loadMovieDetails(self, movieUrl):
        soup = self.getHtml(movieUrl)
        titleTag = soup.find("h1", class_="headline-1")
        title = titleTag.get_text(" ", strip=True) if titleTag else ""
        yearTag = soup.find("span", class_="releasedate")
        year = yearTag.get_text(" ", strip=True) if yearTag else ""
        if not title:
            raise ValueError("Letterboxd returned a movie page without a title.")
        imageTag = soup.find("meta", {"property": "og:image"})
        imageUrl = imageTag.get("content") if imageTag else None
        ratingTag = soup.find("meta", {"name": "twitter:data2"})
        rating = (
            ratingTag.get("content", "").removesuffix(" out of 5")
            if ratingTag
            else None
        )

        movieDescriptionSoup = soup.find("div", class_="review")
        taglineTag = (
            movieDescriptionSoup.find("h4", class_="tagline")
            if movieDescriptionSoup
            else None
        )
        truncateDiv = (
            movieDescriptionSoup.find("div", class_="truncate")
            if movieDescriptionSoup
            else None
        )
        descriptionTag = truncateDiv.find("p") if truncateDiv else None
        description = descriptionTag.get_text(strip=True) if descriptionTag else None

        tagline = taglineTag.get_text(" ", strip=True) if taglineTag else None
        description = (
            descriptionTag.get_text(" ", strip=True) if descriptionTag else None
        )

        castList = soup.find("div", class_="cast-list")
        castSoup = (
            castList.find_all("a", class_="text-slug tooltip") if castList else []
        )
        cast = [actor.text.strip() for actor in castSoup]

        contributorList = soup.find("span", class_="contributorlist")
        directorsSoup = (
            contributorList.find_all("a", class_="contributor")
            if contributorList
            else []
        )
        directors = [director.text.strip() for director in directorsSoup]

        genresPanel = soup.find("div", id="tab-panel-genres")
        genresSoup = (
            genresPanel.find_all("a", class_="text-slug") if genresPanel else []
        )
        genres = [genre.text.strip() for genre in genresSoup]

        return {
            "title": title,
            "year": year,
            "tagline": tagline,
            "description": description,
            "image": imageUrl,
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
        movieGrid = self._getMovieGrid(soup)
        movies = movieGrid.find_all("li", class_="griditem")
        if not movies:
            raise ValueError("No movies were found on this Letterboxd profile.")
        movieSoup = random.choice(movies)
        component = movieSoup.find("div", class_="react-component")
        movieUrl = component.get("data-item-link") if component else None
        if not movieUrl:
            raise ValueError("Letterboxd returned a movie without a valid link.")
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
            movieGrid = self._getMovieGrid(soup)
            movies = movieGrid.find_all("li", class_="griditem")
            for movieSoup in movies:
                image = movieSoup.find("img")
                movieName = image.get("alt", "").strip() if image else ""
                if movieName:
                    allMovies.append(movieName)
        if not allMovies:
            raise ValueError("No movies were found on this Letterboxd profile.")
        return allMovies

class LetterboxdDownloadedData(Template):
    """ Data from https://letterboxd.com/data/export/ """
    def loadRandomMovie(self, username):
        soup = self.loadHtmlFromFile("letterboxd_movie.html")
        return LetterboxdScraper()._loadMovieDetails(soup)

    def getAllUserMovieNames(self, username):
        soup = self.loadHtmlFromFile("letterboxd_profile.html")
        movieGrid = LetterboxdScraper()._getMovieGrid(soup)
        movies = movieGrid.find_all("li", class_="griditem")
        allMovies = []
        for movieSoup in movies:
            image = movieSoup.find("img")
            movieName = image.get("alt", "").strip() if image else ""
            if movieName:
                allMovies.append(movieName)
        if not allMovies:
            raise ValueError("No movies were found in the downloaded data.")
        return allMovies