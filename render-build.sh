#!/usr/bin/env bash
set -euo pipefail

DATABASE_URL="https://github.com/TzurSofferTracking/GuessTheMovie/releases/download/DATABASE/database.sqlite"
DATABASE_PATH="db/database.sqlite"

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir -p "$(dirname "$DATABASE_PATH")"
curl --fail --location --silent --show-error --retry 3 --connect-timeout 15 \
    "$DATABASE_URL" \
    --output "$DATABASE_PATH"

python -c "import sqlite3; connection = sqlite3.connect('$DATABASE_PATH'); connection.execute('SELECT COUNT(*) FROM movies').fetchone(); connection.close()"

echo "Installed dependencies and downloaded $DATABASE_PATH"
