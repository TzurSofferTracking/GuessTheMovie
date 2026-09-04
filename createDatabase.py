import json
import ijson
import sqlite3

from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests
from backend import LetterboxdScraper

def saveMoviesToJson(movies, outputFileName="db/database.json", condense=True):
    indent = 4
    if condense:
        indent = None
    with open(outputFileName, "w", encoding="utf-8") as outfile:
        json.dump(movies, outfile, ensure_ascii=False, indent=indent)

def buildMovieDatabaseFromLetterboxdDump(fileName="db/db.jsonl",
                                         outputFileName="db/database.json",
                                         condense=True,                        #< save space by condensing the data, removing unnecessary fields and formatting
                                         save = False                          #< save dump to file, otherwise just return the movies dictionary
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
                if reviews:
                    reviews = [review for review in reviews if review.get("review_text") != "This review may contain spoilers.I can handle the truth."]
                if condense and reviews:
                    reviews = reviews[:1]
                    reviews[0]["review_text"] = reviews[0]["review_text"][:100]    #< Limit to first 200 characters of the first review

                description = movie.get("synopsis")
                if condense and description:
                    description = description[:100]  #< Limit to first 200 characters
                
                imageUrl = movie.get("poster_url")
                tagline = None
                # if requests.get(imageUrl).status_code != 200: #< try to load image
                #     try:
                #         movie = scraper._loadMovieDetails(movie.get("url"))
                #         imageUrl = movie.get("image")
                #         tagline = movie.get("tagline")
                #         print(f"Image not found for {title} ({movie.get('year')}), reloaded from Letterboxd: {imageUrl}")
                #     except Exception as e:
                #         print(f"Image not found for {title} ({movie.get('year')}), and failed to reload from Letterboxd: {e}")

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
    
    if save:
        saveMoviesToJson(movies, outputFileName=outputFileName, condense=condense)
    return movies

def _fixReviews(fileName="db/db.jsonl",
                outputFileName="db/database.json",
                condense=True                        #< save space by condensing the data, removing unnecessary fields and formatting
                ):
    """ Data dump from Letterboxd has a bug where some reviews are just "This review may contain spoilers.I can handle the truth." which is not useful. This function removes those reviews. Function is no longer useful as the bug has been fixed in the data dump, but is kept here for reference. """
    reviews = buildMovieDatabaseFromLetterboxdDump(fileName=fileName, outputFileName=outputFileName, condense=condense, save=False)
    with open(outputFileName, "r", encoding="utf-8") as database:
        movies = json.load(database)
    
    emptyReviewsCount = 0
    nonEmptyReviewsCount = 0
    for key in movies.keys():
        if key in reviews:
            nonEmptyReviewsCount += 1
            if not reviews[key]["reviews"]:
                emptyReviewsCount += 1
                nonEmptyReviewsCount -= 1
            movies[key]["reviews"] = reviews[key]["reviews"]
    print(f"Deleted {emptyReviewsCount} empty reviews and modified {nonEmptyReviewsCount} reviews.")
    saveMoviesToJson(movies, outputFileName=outputFileName, condense=condense)

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

def sortDatabaseJsonByRating(databaseFileName="db/database.json", condense=True):
    with open(databaseFileName, "r", encoding="utf-8") as file:
        movies = json.load(file)

    moviesZip = movies.items()
    moviesZip = sorted(moviesZip, key=lambda movieZip: (float(movieZip[1].get("rating") or 0), movieZip[1].get("title")), reverse=True)
    movies = dict(moviesZip)
    saveMoviesToJson(movies, outputFileName=databaseFileName, condense=condense)

def updateMovieImage(movie, scraper):
    imageUrl = movie.get("image")
    
    # Check if image URL is invalid or missing
    if not imageUrl or requests.get(imageUrl, timeout=5).status_code != 200:
        try:
            updatedMovie = scraper._loadMovieDetails(movie.get("url"))
            movie["image"] = updatedMovie.get("image")
            movie["tagline"] = updatedMovie.get("tagline")
            print(f"Updated image URL for {movie.get('title')} ({movie.get('year', 0)}): {movie['image']}")
            return True
        except Exception as e:
            print(f"Failed to update image URL for {movie.get('title')} ({movie.get('year', 0)}): {e}")
            return False
    return False

def rebuildImageUrlsInDatabaseJson(databaseFileName="db/database.json", offset=0, maxWorkers=10, condense=True):
    scraper = LetterboxdScraper()
    
    with open(databaseFileName, "r", encoding="utf-8") as file:
        movies = json.load(file)

    movieItems = list(movies.values())[offset:]
    totalMovies = len(movieItems)
    
    indent = None if condense else 4
    batchSize = 1000
    
    print(f"Starting execution with {maxWorkers} threads across {totalMovies} movies...")

    # Process movies in safe batches
    for batchStart in range(0, totalMovies, batchSize):
        batch = movieItems[batchStart : batchStart + batchSize]
        
        # 1. Run threads ONLY for the current batch
        with ThreadPoolExecutor(max_workers=maxWorkers) as executor:
            futures = [
                executor.submit(updateMovieImage, movie, scraper) 
                for movie in batch
            ]

            for future in as_completed(futures):
                pass

        processedCount = min(batchStart + batchSize, totalMovies)
        print(f"Processed {processedCount}/{totalMovies} movies... Saving progress to {databaseFileName}...")
        
        with open(databaseFileName, "w", encoding="utf-8") as file:
            json.dump(movies, file, ensure_ascii=False, indent=indent)

    print("Finished updating database.")

def buildSqliteDatabaseFromJson(jsonFileName="db/database.json", outputFileName="db/database.sqlite"):
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
    # _fixReviews()
    # buildMovieDatabaseFromLetterboxdDump(condense=True)
    # addToDatabaseFromLetterboxdList("https://letterboxd.com/owencharlish/list/2026/", databaseFileName="db/database.json", condense=True)
    # sortDatabaseJsonByRating()
    # rebuildImageUrlsInDatabaseJson(offset=185000)
    buildSqliteDatabaseFromJson()