# intervals.icu Workout Uploader

A script for bulk-uploading workouts to intervals.icu via API.

## Requirements

- Python 3.10+ (no third-party libraries — standard library only)
- An intervals.icu account with an API key

## Quick Start

```bash
# 1. Get your Athlete ID and API key:
#    intervals.icu → Settings → API

# 2. Create a .env file in the project folder (recommended — keeps credentials out of shell history):
cp .env.example .env
# Edit .env and fill in your values:
#   INTERVALS_ATHLETE_ID=i12345
#   INTERVALS_API_KEY=your_api_key

# 3. Dry run first — verify everything parses correctly:
python3 upload_to_intervals.py --folder ./workouts --dry-run

# 4. Upload everything:
python3 upload_to_intervals.py --folder ./workouts
```

Or via environment variables:
```bash
export INTERVALS_ATHLETE_ID=i12345
export INTERVALS_API_KEY=your_api_key
python3 upload_to_intervals.py --folder ./workouts
```

Or pass credentials directly:
```bash
python3 upload_to_intervals.py --folder ./workouts --athlete i12345 --key your_api_key
```

## File Formats

### Cycling — `.zwo` (Zwift Workout)
Standard Zwift format. The script reads the workout structure (warmup, intervals, cooldown, ramps) and creates a **calendar event** with a power graph showing all steps as percentages of FTP.

### Swim / Run / Other — `.xml`
Simple format with tags:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<workout>
  <name>Workout name</name>
  <date>2026-04-01</date>          <!-- YYYY-MM-DD -->
  <sport>Swim</sport>              <!-- Swim | Run | Bike | Other | Strength | Walk -->
  <duration>3600</duration>        <!-- seconds -->
  <description>Workout description text...</description>
</workout>
```

## File Naming

If the XML has no `<date>` tag, the script extracts the date from the **filename**.

Supported patterns:
| Filename pattern | Result |
|---|---|
| `W1_Mon_30mar_Swim.xml` | March 30 (current or next year) |
| `W1_Mon_30mar2026_Swim.xml` | March 30, 2026 |
| `2026-04-01_Swim.xml` | April 1, 2026 (ISO format) |
| `20260401_Swim.xml` | April 1, 2026 (compact ISO) |

For `.zwo` files the date is always taken from the filename using the same patterns.

## All Options

```
--folder, -f    Folder with files (default: current directory)
--athlete, -a   Athlete ID from intervals.icu (or env INTERVALS_ATHLETE_ID)
--key, -k       API key (or env INTERVALS_API_KEY)
--ext           File extensions (default: .zwo .xml)
--dry-run       Show what would be uploaded, without uploading
--overwrite     Delete and re-upload workouts with the same name on the same date
--from          Upload only from this date (YYYY-MM-DD)
--to            Upload only up to this date (YYYY-MM-DD)
--verbose, -v   Print full JSON payload for each file
```

## Examples

```bash
# Cycling workouts only:
python3 upload_to_intervals.py --folder ./workouts --ext .zwo

# April only:
python3 upload_to_intervals.py --folder ./workouts --from 2026-04-01 --to 2026-04-30

# Re-upload plan (if already uploaded and want to update):
python3 upload_to_intervals.py --folder ./workouts --overwrite

# Inspect the full JSON sent to the API:
python3 upload_to_intervals.py --folder ./workouts --dry-run --verbose
```

## Cleanup

`cleanup_intervals.py` deletes planned events from the calendar starting from a given date. Completed activities are not affected — only planned events (WORKOUT, NOTE, RACE).

```bash
# Preview what would be deleted (no changes made):
python3 cleanup_intervals.py --from 2026-04-01 --dry-run

# Delete all planned events from April 1 onwards (up to 1 year):
python3 cleanup_intervals.py --from 2026-04-01

# Delete only a specific range:
python3 cleanup_intervals.py --from 2026-04-01 --to 2026-04-30
```

Uses the same `.env` credentials as the uploader.

## Folder Structure

Recommended layout:
```
my_plan/
├── upload_to_intervals.py
├── README.md
├── .env                 <- your credentials (do not commit!)
├── .env.example         <- template
├── workouts/
│   ├── W1_Mon_30mar_Swim_Block1.xml
│   ├── W1_Wed_01apr_Bike_Z2_75min.zwo
│   ├── W2_Sat_11apr_Bike_Sweetspot.zwo
│   └── ...
```
