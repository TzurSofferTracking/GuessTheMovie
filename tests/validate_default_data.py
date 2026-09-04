import argparse
import csv
import json
import requests
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = PROJECT_ROOT / "db" / "defaultData.csv"
DATABASE = PROJECT_ROOT / "db" / "database.sqlite"
DEFAULT_OUTPUT = PROJECT_ROOT / "tests" / "default_data_report.json"

def load_movies(csv_path, database_path):
    with sqlite3.connect(database_path) as connection:
        movies = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            for line_number, row in enumerate(csv.DictReader(csv_file), start=2):
                title = (row.get("Name") or row.get("Title") or "").strip()
                year = (row.get("Year") or "").strip()
                record = {
                    "line": line_number,
                    "title": title,
                    "year": year,
                    "key": title + year,
                    "movie": None,
                    "exists_in_db": False,
                }
                if title and year:
                    result = connection.execute(
                        "SELECT movie FROM movies WHERE movie_key = ?", (record["key"],)
                    ).fetchone()
                    if result:
                        record["movie"] = json.loads(result[0])
                        record["exists_in_db"] = True
                movies.append(record)
    return movies

def check_image(record):
    image_url = (record.get("movie") or {}).get("image")
    if not image_url:
        return False
    try:
        request = requests.get(image_url)
        print(f"{request.status_code} for {record['title']} ({record['year']}) - {image_url}")
        return request.status_code == 200
    except:
        return False


def validate(csv_path, database_path, timeout, workers):
    records = load_movies(csv_path, database_path)
    for i in range(len(records)):
        records[i]["imageOk"] = False
        if records[i]["exists_in_db"]:
            records[i]["imageOk"] = check_image(records[i])
    return records


def main():
    parser = argparse.ArgumentParser(description="Validate every movie in defaultData.csv.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Write the detailed report as JSON.",
    )
    parser.add_argument("--timeout", type=float, default=8)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    records = validate(args.csv, args.database, args.timeout, args.workers)

    # Extract only the names for each section
    missing_entry_names = [record["title"] for record in records if not record["exists_in_db"]]
    invalid_image_names = [record["title"] for record in records if record["imageOk"] == False and record["exists_in_db"]]

    report = {
        "missing_entries": missing_entry_names,
        "invalid_images": invalid_image_names,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"Report written to: {args.output}\n")

    print("--- MISSING ENTRIES (NOT IN DATABASE) ---")
    if missing_entry_names:
        for name in missing_entry_names:
            print(name)
    else:
        print("None")

    print("\n--- INVALID IMAGE URLS ---")
    if invalid_image_names:
        for name in invalid_image_names:
            print(name)
    else:
        print("None")

    return 1 if missing_entry_names or invalid_image_names else 0


if __name__ == "__main__":
    sys.exit(main())