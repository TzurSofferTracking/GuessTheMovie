#!/usr/bin/env bash
set -euo pipefail

DATABASE_URL="https://github.com/TzurSofferTracking/GuessTheMovie/releases/download/DATABASE/database.json"
DATABASE_PATH="db/database.json"
SQLITE_PATH="db/database.sqlite"

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir -p "$(dirname "$DATABASE_PATH")"
curl --fail --location --silent --show-error --retry 3 --connect-timeout 15 \
    "$DATABASE_URL" \
    --output "$DATABASE_PATH"

python -c "from backend import buildSqliteDatabaseFromJson; buildSqliteDatabaseFromJson('$DATABASE_PATH', '$SQLITE_PATH')"
rm -f "$DATABASE_PATH"

echo "Installed dependencies and built $SQLITE_PATH"
