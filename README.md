# SkylightSync

Automatically sync photos from an iCloud shared album to an email address.

## Features

- Scrapes photos from public iCloud shared albums
- Tracks already processed photos to avoid duplicates
- Sends new photos via email in configurable batches
- Can run continuously or as a one-time sync
- Docker support for easy deployment

## Quick Start with Docker

1. Create `.env` file with your email credentials:
```bash
cp .env.example .env
# Edit .env and add your SENDER_EMAIL and SENDER_PASSWORD
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

## Email Setup for Gmail

1. Enable 2-factor authentication on your Google account
2. Generate an app-specific password at https://myaccount.google.com/apppasswords
3. Use this password in the `.env` file

## Files Created

- `downloads/` - Directory where photos are temporarily stored
- `data/skylight.db` - SQLite store tracking processed photo URLs and content
  hashes, so already-synced photos are skipped without re-downloading. (A legacy
  `data/processed_photos.json` is migrated in automatically on first run.)
- `.env` - Your configuration (not tracked by git)