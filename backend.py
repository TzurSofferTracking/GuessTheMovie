import json
import random
import csv
import sqlite3

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
    
    def getMoviesFromLetterboxdList(self, url):
        soup = self.getHtml(url)
        movieGrid = soup.find("ul", class_="poster-list")
        movies = movieGrid.find_all("li", class_="posteritem")
        if not movies:
            raise ValueError("No movies were found on this Letterboxd list.")
        movieDetailsList = []
        for movieSoup in movies:
            component = movieSoup.find("div", class_="react-component")
            movieUrl = component.get("data-item-link") if component else None
            if not movieUrl:
                continue
            fullMovieUrl = f"https://letterboxd.com{movieUrl}"
            try:
                movieDetails = self._loadMovieDetails(fullMovieUrl)
                movieDetailsList.append(movieDetails)
            except Exception as e:
                print(f"Failed to load details for {fullMovieUrl}: {e}")
        return movieDetailsList

    def _getPageCountForUrl(self, url):
        soup = self.getHtml(url)
        pages = soup.find_all("li", class_="paginate-page")
        if len(pages) == 0:
            return 1
        return int(pages[-1].text.strip())
    def _getPageCountForUser(self, username):
        url = f"https://letterboxd.com/{username}/films/"
        return self._getPageCountForUrl(url)

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
    def __init__(self, database="db/database.sqlite"):
        self.database = database

    def _findMovie(self, title, year):
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT movie FROM movies WHERE movie_key = ?",
                (title + year,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def _loadExportedMovies(self, exportedJournal):
        if hasattr(exportedJournal, "stream"):
            # Flask FileStorage
            exportedJournal.stream.seek(0)
            text = exportedJournal.stream.read().decode("utf-8-sig")
        elif hasattr(exportedJournal, "read"):
            exportedJournal.seek(0)
            data = exportedJournal.read()

            if isinstance(data, bytes):
                data = data.decode("utf-8-sig")

            text = data
        else:
            with open(exportedJournal, "r", encoding="utf-8-sig") as file:
                text = file.read()

        reader = csv.DictReader(text.splitlines())

        movies = {}

        for row in reader:
            title = (row.get("Name") or row.get("Title") or "").strip()
            year = (row.get("Year") or "").strip()

            if not title:
                continue
            if year:
                key = title + year
                movie = self._findMovie(title, year)
                if movie:
                    movie = movie.copy()
                    movie["userRating"] = row.get("Rating") or None
                    movies[key] = movie
        return movies

    def loadRandomMovie(self, exportedJournal):
        movies = self._loadExportedMovies(exportedJournal)

        if not movies:
            return None

        key = random.choice(list(movies.keys()))
        movie = movies[key].copy()
        return movie

    def getAllUserMovieNames(self, exportedJournal):
        movies = self._loadExportedMovies(exportedJournal)

        return [
            movie["title"]
            for movie in movies.values()
        ]

def buildMovieDatabaseFromLetterboxdDump(fileName="db/db.jsonl",
                                         outputFileName="db/database.json",
                                         condense=True                        #< save space by condensing the data, removing unnecessary fields and formatting
                                         ):
    movies = {}
    scraper = LetterboxdScraper()
    with open(fileName, "r", encoding="utf-8") as file:
        for lineNumber, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                movie = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Invalid JSON on line {lineNumber} of {fileName}."
                ) from error
            
            title = movie.get("title", None)
            if title:
                rating = movie.get("rating")
                if type(rating) == str:
                    rating = rating.removesuffix(" out of 5")
                cast = movie.get("cast") or []
                if condense:
                    cast = cast[:3]
                
                reviews = movie.get("reviews")
                if condense and reviews:
                    reviews = reviews[:1]
                    reviews[0]["review_text"] = reviews[0]["review_text"][:100]    #< Limit to first 200 characters of the first review

                description = movie.get("synopsis")
                if condense and description:
                    description = description[:100]  #< Limit to first 200 characters
                
                imageUrl = movie.get("poster_url")
                tagline = None
                if requests.get(imageUrl).status_code != 200: #< try to load image
                    try:
                        movie = scraper._loadMovieDetails(movie.get("url"))
                        imageUrl = movie.get("image")
                        tagline = movie.get("tagline")
                        print(f"Image not found for {title} ({movie.get('year')}), reloaded from Letterboxd: {imageUrl}")
                    except Exception as e:
                        print(f"Image not found for {title} ({movie.get('year')}), and failed to reload from Letterboxd: {e}")
                
                movie = {
                    "url": movie.get("url"),
                    "title": title,
                    "year": movie.get("year"),
                    "tagline": tagline,
                    "description": description,
                    "image": imageUrl,
                    "rating": rating,
                    "cast": cast,
                    "directors": movie.get("directors"),
                    "genres": movie.get("genres"),
                    "reviews": reviews 
                }

                movies[title+str(movie.get("year", 0))] = movie

    with open(outputFileName, "w", encoding="utf-8") as outfile:
        indent = 4
        if condense:
            indent = None
        json.dump(movies, outfile, ensure_ascii=False, indent=indent)

def addToDatabaseFromLetterboxdList(url, databaseFileName="db/database.json", condense=True):
    # https://letterboxd.com/owencharlish/list/2026/
    scraper = LetterboxdScraper()
    movies = scraper.getMoviesFromLetterboxdList(url)
    with open(databaseFileName, "r", encoding="utf-8") as file:
        existingMovies = json.load(file)
    existingMovies.update({movie["title"] + str(movie.get("year", 0)): movie for movie in movies})
    indent = 4
    if condense:
        indent = None
    with open(databaseFileName, "w", encoding="utf-8") as file:
        json.dump(existingMovies, file, ensure_ascii=False, indent=indent)

def sortDatabaseJsonByRatingCount(databaseFileName="db/database.json", condense=True):
    with open(databaseFileName, "r", encoding="utf-8") as file:
        movies = json.load(file)
    sortedMovies = dict(sorted(movies.items(), key=lambda item: item[1].get("ratingCount", 0), reverse=True))
    indent = 4
    if condense:
        indent = None
    with open(databaseFileName, "w", encoding="utf-8") as file:
        json.dump(sortedMovies, file, ensure_ascii=False, indent=indent)

def rebuildImageUrlsInDatabaseJson(databaseFileName="db/database.json"):
    scraper = LetterboxdScraper()
    with open(databaseFileName, "r", encoding="utf-8") as file:
        movies = json.load(file)
    for i, movie in enumerate(movies.values()):
        imageUrl = movie.get("image")
        if imageUrl and requests.get(imageUrl).status_code != 200: #< try to load image
            try:
                updatedMovie = scraper._loadMovieDetails(movie.get("url"))
                movie["image"] = updatedMovie.get("image")
                movie["tagline"] = updatedMovie.get("tagline")
                print(f"Updated image URL for {movie['title']} ({movie.get('year', 0)}): {movie['image']}")
            except Exception as e:
                print(f"Failed to update image URL for {movie['title']} ({movie.get('year', 0)}): {e}")
        # else:
        #     print(f"Image URL for {movie['title']} ({movie.get('year', 0)}) is valid: {imageUrl}")
        if (i + 1) % 100 == 0:
            print(f"Processed {i + 1} movies... / {len(movies)}... Saving progress to {databaseFileName}...")
            with open(databaseFileName, "w", encoding="utf-8") as file:
                json.dump(movies, file, ensure_ascii=False, indent=4)
    with open(databaseFileName, "w", encoding="utf-8") as file:
        json.dump(movies, file, ensure_ascii=False, indent=4)

def buildSqliteDatabaseFromJson(jsonFileName="db/database.json", outputFileName="db/database.sqlite"):
    import ijson

    with sqlite3.connect(outputFileName) as connection:
        connection.execute("DROP TABLE IF EXISTS movies")
        connection.execute("CREATE TABLE movies (movie_key TEXT PRIMARY KEY, movie TEXT NOT NULL)")
        with open(jsonFileName, "rb") as file:
            for key, movie in ijson.kvitems(file, ""):
                connection.execute(
                    "INSERT OR REPLACE INTO movies (movie_key, movie) VALUES (?, ?)",
                    (key, json.dumps(movie, ensure_ascii=False, separators=(",", ":"))),
                )
        connection.commit()

if __name__ == "__main__":
    # buildMovieDatabaseFromLetterboxdDump(condense=True)
    # addToDatabaseFromLetterboxdList("https://letterboxd.com/owencharlish/list/2026/", databaseFileName="db/database.json", condense=True)
    # sortDatabaseJsonByRatingCount()
    # rebuildImageUrlsInDatabaseJson()
    buildSqliteDatabaseFromJson()