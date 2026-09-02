import random
from urllib.parse import urljoin

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
        response = requests.get(url, impersonate="chrome120", timeout=15)
        return BeautifulSoup(response.text, "html.parser")

    def _loadMovieDetails(self, movieUrl):
        soup = self.getHtml(movieUrl)
        title = soup.find("h1", class_="headline-1").text.strip()
        year = soup.find("span", class_="releasedate").text.strip()
        imageTag = soup.find("meta", {"property": "og:image"})
        imageUrl = imageTag.get("content") if imageTag else None
        ratingTag = soup.find("meta", {"name": "twitter:data2"})
        rating = ratingTag.get("content", "").removesuffix(" out of 5") if ratingTag else None
        
        moveDescriptionSoup = soup.find("div", class_="review")
        taglineTag = moveDescriptionSoup.find("h4", class_="tagline") if moveDescriptionSoup else None
        truncateDiv = moveDescriptionSoup.find("div", class_="truncate")
        descriptionTag = truncateDiv.find("p") if truncateDiv else None
        description = descriptionTag.get_text(strip=True) if descriptionTag else None

        tagline = taglineTag.get_text(" ", strip=True) if taglineTag else None
        description = descriptionTag.get_text(" ", strip=True) if descriptionTag else None

        castList = soup.find("div", class_="cast-list")
        castSoup = castList.find_all("a", class_="text-slug tooltip") if castList else []
        cast = [actor.text.strip() for actor in castSoup]

        contributorList = soup.find("span", class_="contributorlist")
        directorsSoup = contributorList.find_all("a", class_="contributor") if contributorList else []
        directors = [director.text.strip() for director in directorsSoup]

        genresPanel = soup.find("div", id="tab-panel-genres")
        genresSoup = genresPanel.find_all("a", class_="text-slug") if genresPanel else []
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
                movieName = movieSoup.find("img").get("alt")
                allMovies.append(movieName)
                print(allMovies)
        return allMovies
