# SkylightSync

Automatically sync photos from an iCloud shared album to an email address (such as a Skylight frame's send-to-frame address).

[![CI](https://github.com/dorkinatent/SkylightSync/actions/workflows/ci.yml/badge.svg)](https://github.com/dorkinatent/SkylightSync/actions/workflows/ci.yml)

## Features

- Fetches photos from public iCloud shared albums via the JSON web-stream API — no headless browser required
- Deduplicates by stable photo GUID (with a content-hash fallback) so photos are never re-sent
- Sends new photos via email in configurable batches
- Can run continuously or as a one-time sync (ideal for cron)
- Docker support, with a pre-built image published to GHCR

## Quick Start with Docker

1. Create `.env` file with your configuration:
```bash
cp .env.example .env
# Edit .env and set ICLOUD_ALBUM_URL, RECIPIENT_EMAIL, SENDER_EMAIL, and SENDER_PASSWORD
```

2. Run with Docker Compose:
```bash
# Build and start the container
docker-compose up -d

# View logs
docker-compose logs -f

# Stop the container
docker-compose down
```

By default Compose builds the image locally. To pull the pre-built image
instead of building, uncomment the `image:` line in `docker-compose.yml` (see
[Run from the published image](#run-from-the-published-image-ghcr)).

## Manual Setup

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

2. Configure email settings:
   - Copy `.env.example` to `.env`
   - Add your email credentials (for Gmail, use an app-specific password)

## Usage

### With Docker

```bash
# Run continuously (default: check every hour)
docker-compose up -d

# Run with custom settings
docker-compose run --rm skylight-sync python skylight_sync.py --once

# Run with custom interval
docker-compose run --rm skylight-sync python skylight_sync.py --interval 1800
```

### Run from the published image (GHCR)

Every version tag publishes an image to the GitHub Container Registry, so you
can run without cloning or building:

```bash
docker pull ghcr.io/dorkinatent/skylightsync:latest

# One-time sync; mount ./data so the dedup DB persists between runs
docker run --rm --env-file .env \
  -v "$PWD/data:/app/data" \
  ghcr.io/dorkinatent/skylightsync:latest \
  python skylight_sync.py --once
```

Available tags: `latest`, a semver tag (e.g. `1.0.0`), the minor series (`1.0`),
and a commit tag (`sha-<commit>`).

### Without Docker

Run once:
```bash
python skylight_sync.py --once
```

Run continuously (check every hour):
```bash
python skylight_sync.py
```

Custom interval (in seconds):
```bash
python skylight_sync.py --interval 1800  # Check every 30 minutes
```

Custom batch size:
```bash
python skylight_sync.py --batch-size 10  # Send 10 photos per email
```

## Scheduling (cron)

`run_skylight_sync.sh` runs a single sync and exits — the shape cron expects. It
`cd`s into the project directory and activates the project virtualenv (`venv/`
or `.venv/`) if present.

Example crontab entry — sync every day at 7am and append output to a log:

```cron
0 7 * * * /path/to/SkylightSync/run_skylight_sync.sh >> /path/to/SkylightSync/cron.log 2>&1
```

## Email Setup for Gmail

1. Enable 2-factor authentication on your Google account
2. Generate an app-specific password at https://myaccount.google.com/apppasswords
3. Use this password in the `.env` file

## Development

Runtime dependencies live in `requirements.txt`; test/lint tooling lives in
`requirements-dev.txt`.

```bash
pip install -r requirements-dev.txt
pytest          # run the test suite
ruff check .    # lint
```

## Files Created

- `downloads/` - Directory where photos are temporarily stored
- `data/skylight.db` - SQLite store tracking processed photo GUIDs (the primary
  dedup key), URLs, and content hashes, so already-synced photos are skipped
  without re-downloading. (A legacy `data/processed_photos.json` is migrated in
  automatically on first run.)
- `.env` - Your configuration (not tracked by git)