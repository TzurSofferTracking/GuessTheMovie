#!/usr/bin/env bash
set -euo pipefail

DATABASE_URL="https://github.com/TzurSofferTracking/GuessTheMovie/releases/download/DATABASE/database.json"
DATABASE_PATH="db/database.json"

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir -p "$(dirname "$DATABASE_PATH")"
curl --fail --location --silent --show-error --retry 3 --connect-timeout 15 \
    "$DATABASE_URL" \
    --output "$DATABASE_PATH"

python -c "import json; json.load(open('$DATABASE_PATH', encoding='utf-8'))"

echo "Installed dependencies and downloaded $DATABASE_PATH"
