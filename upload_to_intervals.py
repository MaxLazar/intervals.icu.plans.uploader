#!/usr/bin/env python3
"""
intervals.icu Workout Uploader
================================
Uploads workouts from a local folder to intervals.icu via API.

Supported formats:
  .zwo  — cycling workouts (Zwift), uploaded to workout library as structured workouts with a power graph
  .xml  — swim / run / other, uploaded to calendar as planned workouts with a description

Date is taken from the filename (pattern W1_Mon_30mar_...) or from a <date> tag inside XML.

Usage:
  python3 upload_to_intervals.py --folder ./workouts --athlete YOUR_ATHLETE_ID --key YOUR_API_KEY

  or via environment variables / .env file:
  INTERVALS_ATHLETE_ID=i00000
  INTERVALS_API_KEY=your_key_here
  python3 upload_to_intervals.py --folder ./workouts

Get athlete_id: intervals.icu → Settings → Profile → your ID in the URL (i00000...)
Get API key:    intervals.icu → Settings → API
"""
from __future__ import annotations

import os
import re
import ssl
import sys
import json
import base64
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime, date
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (no overwrite)."""
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()


def _ssl_context() -> ssl.SSLContext:
    """Return an SSL context with verified certificates.

    On macOS, Python from python.org ships without system CA certificates.
    This tries the default context first, then falls back to known CA bundle
    paths so the connection is always verified.
    """
    ctx = ssl.create_default_context()
    if ctx.get_ca_certs():
        return ctx
    for ca_bundle in (
        "/etc/ssl/cert.pem",                        # macOS / Alpine
        "/etc/ssl/certs/ca-certificates.crt",       # Debian / Ubuntu
        "/etc/pki/tls/certs/ca-bundle.crt",         # RHEL / CentOS
    ):
        if Path(ca_bundle).is_file():
            return ssl.create_default_context(cafile=ca_bundle)
    return ctx  # best-effort; will still verify if the OS provides certs


# ─── Constants ────────────────────────────────────────────────────────────────

API_BASE = "https://intervals.icu/api/v1"

SPORT_MAP = {
    "Swim":     "Swim",
    "Run":      "Run",
    "Bike":     "Ride",
    "bike":     "Ride",
    "Ride":     "Ride",
    "Other":    "Other",
    "Strength": "WeightTraining",
    "Walk":     "Walk",
}

# Month abbreviations for parsing dates from filenames
MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


# ─── API client ───────────────────────────────────────────────────────────────

class IntervalsClient:
    def __init__(self, athlete_id: str, api_key: str):
        self.athlete_id = athlete_id
        self.base = f"{API_BASE}/athlete/{athlete_id}"
        # intervals.icu uses HTTP Basic Auth: login "API_KEY", password = key
        creds = base64.b64encode(f"API_KEY:{api_key}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, body=None) -> dict | list:
        url = f"{self.base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = Request(url, data=data, headers=self.headers, method=method)
        try:
            with urlopen(req, timeout=30, context=_ssl_context()) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except HTTPError as e:
            error_body = e.read().decode(errors="replace")
            raise RuntimeError(f"HTTP {e.code} {e.reason} → {url}\n{error_body}")
        except URLError as e:
            raise RuntimeError(f"Network error → {url}: {e.reason}")

    def get_events(self, start: str, end: str) -> list:
        """Fetch events in a date range (YYYY-MM-DD)."""
        path = f"/events?oldest={start}&newest={end}"
        return self._request("GET", path)

    def create_event(self, payload: dict) -> dict:
        """Create a planned workout / event."""
        return self._request("POST", "/events", payload)

    def create_event_bulk(self, events: list) -> dict:
        """Create multiple events in bulk."""
        return self._request("POST", "/events/bulk", events)

    def update_event(self, event_id: int, payload: dict) -> dict:
        """Update an existing event."""
        return self._request("PUT", f"/events/{event_id}", payload)

    def delete_event(self, event_id: int) -> dict:
        return self._request("DELETE", f"/events/{event_id}")

    def whoami(self) -> dict:
        """Verify connection — return athlete data."""
        return self._request("GET", "")




# ─── File parsers ─────────────────────────────────────────────────────────────

def parse_date_from_filename(filename: str) -> date | None:
    """
    Extract a date from a filename.
    Supported patterns:
      W1_Mon_30mar_...     →  day+month, year = current (or next if already passed)
      W1_Mon_30mar2026_... →  day+month+year
      2026-03-30_...       →  ISO format
      20260330_...         →  compact ISO
    """
    name = Path(filename).stem.lower()

    # ISO: 2026-03-30 or 20260330
    m = re.search(r'(\d{4})[_-]?(\d{2})[_-]?(\d{2})', name)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # Pattern: 30mar or 30mar2026
    m = re.search(r'(\d{1,2})(' + '|'.join(MONTH_MAP.keys()) + r')(\d{4})?', name)
    if m:
        day = int(m.group(1))
        month = MONTH_MAP[m.group(2)]
        year = int(m.group(3)) if m.group(3) else None
        if year is None:
            today = date.today()
            year = today.year
            # if the date has already passed this year — use next year
            try:
                candidate = date(year, month, day)
                if candidate < today:
                    year += 1
            except ValueError:
                pass
        try:
            return date(year, month, day)
        except ValueError:
            pass

    return None


def parse_zwo(filepath: Path) -> dict | None:
    """Parse a .zwo file and return an intervals.icu /events payload.

    Warmup and Cooldown blocks are converted to type="Ramp" steps in workout_doc
    using intervals.icu's native format: {"type":"Ramp","power":{"start":X,"end":Y,"unit":"ftp"}}
    This is exactly what intervals.icu's own workout builder produces and renders correctly.
    """
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"    XML parse error in {filepath.name}: {e}")
        return None

    name = (root.findtext("n") or root.findtext("name") or filepath.stem).strip()
    description = (root.findtext("description") or "").strip()

    workout_el = root.find("workout")
    total_seconds = 0
    if workout_el is not None:
        for el in workout_el:
            dur = 0
            if el.tag in ("SteadyState", "Warmup", "Cooldown", "FreeRide", "MaxEffort"):
                dur = int(float(el.get("Duration", 0)))
            elif el.tag == "IntervalsT":
                reps = int(float(el.get("Repeat", 1)))
                on   = int(float(el.get("OnDuration", 0)))
                off  = int(float(el.get("OffDuration", 0)))
                dur  = reps * (on + off)
            elif el.tag == "Ramp":
                dur = int(float(el.get("Duration", 0)))
            total_seconds += dur

    workout_date = parse_date_from_filename(filepath.name)

    icu_description = _zwo_to_icu_description(workout_el) if workout_el is not None else ""
    if description:
        icu_description = (icu_description + "\n\n" + description) if icu_description else description

    steps = _zwo_to_icu_steps(workout_el) if workout_el is not None else []

    return {
        "category":         "WORKOUT",
        "name":             name,
        "type":             "Ride",
        "description":      icu_description,
        "moving_time":      total_seconds if total_seconds > 0 else None,
        "start_date_local": (workout_date.isoformat() + "T00:00:00") if workout_date else None,
        "workout_doc":      {"steps": steps} if steps else None,
    }


def _zwo_to_icu_steps(workout_el) -> list:
    """Convert ZWO elements to intervals.icu workout_doc steps.

    Uses intervals.icu's native step types:
      Warmup/Cooldown → {"type": "Ramp", "power": {"start": X, "end": Y, "unit": "ftp"}}
      SteadyState     → {"type": "SteadyState", "power": {"value": X, "unit": "ftp"}}
      IntervalsT      → {"type": "IntervalsT", ...}

    The "Ramp" type with power.start/power.end is exactly what intervals.icu's
    own workout builder produces — this renders as a gradient in the power graph
    and syncs correctly to Zwift.
    """
    steps = []
    for el in workout_el:
        tag = el.tag
        if tag == "Warmup":
            lo = float(el.get("PowerLow", 0.45))   # start power (low)
            hi = float(el.get("PowerHigh", 0.65))  # end power (high)
            steps.append({
                "type":     "Ramp",
                "duration": int(float(el.get("Duration", 0))),
                "power":    {"start": lo, "end": hi, "unit": "ftp"},
            })
        elif tag == "Cooldown":
            lo = float(el.get("PowerLow", 0.45))   # end power (low)
            hi = float(el.get("PowerHigh", 0.65))  # start power (high)
            steps.append({
                "type":     "Ramp",
                "duration": int(float(el.get("Duration", 0))),
                "power":    {"start": hi, "end": lo, "unit": "ftp"},
            })
        elif tag == "SteadyState":
            pct = float(el.get("Power", 0.65))
            texts = [te.get("message", "") for te in el.findall("textevent")
                     if te.get("timeoffset", "0") == "0"]
            steps.append({
                "type":     "SteadyState",
                "duration": int(float(el.get("Duration", 0))),
                "power":    {"value": pct, "unit": "ftp"},
                "text":     texts[0] if texts else "",
            })
        elif tag == "IntervalsT":
            reps    = int(float(el.get("Repeat", 1)))
            on_dur  = int(float(el.get("OnDuration", 0)))
            off_dur = int(float(el.get("OffDuration", 0)))
            on_pwr  = float(el.get("OnPower", 1.0))
            off_pwr = float(el.get("OffPower", 0.5))
            on_texts = [te.get("message", "") for te in el.findall("textevent")
                        if te.get("timeoffset", "0") == "0"]
            steps.append({
                "type":         "IntervalsT",
                "repeat":       reps,
                "on_duration":  on_dur,
                "off_duration": off_dur,
                "on_power":     {"value": on_pwr,  "unit": "ftp"},
                "off_power":    {"value": off_pwr, "unit": "ftp"},
                "text":         on_texts[0] if on_texts else "",
            })
        elif tag in ("Ramp", "FreeRide"):
            lo = float(el.get("PowerLow", 0.5))
            hi = float(el.get("PowerHigh", 0.8))
            steps.append({
                "type":     "Ramp",
                "duration": int(float(el.get("Duration", 0))),
                "power":    {"start": lo, "end": hi, "unit": "ftp"},
            })
    return steps


def _fmt_dur(seconds: int) -> str:
    if seconds > 0 and seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds > 0 and seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _zwo_to_icu_description(workout_el) -> str:
    """Convert ZWO workout elements to intervals.icu native text format for calendar events."""
    lines = []
    for el in workout_el:
        tag = el.tag
        if tag == "Warmup":
            dur = int(float(el.get("Duration", 0)))
            low = int(float(el.get("PowerLow", 0.45)) * 100)
            high = int(float(el.get("PowerHigh", 0.62)) * 100)
            lines.append(f"- {_fmt_dur(dur)} {low}-{high}%")
        elif tag == "Cooldown":
            dur = int(float(el.get("Duration", 0)))
            low = int(float(el.get("PowerLow", 0.62)) * 100)
            high = int(float(el.get("PowerHigh", 0.45)) * 100)
            lines.append(f"- {_fmt_dur(dur)} {low}-{high}%")
        elif tag == "SteadyState":
            dur = int(float(el.get("Duration", 0)))
            pct = int(float(el.get("Power", 0.65)) * 100)
            lines.append(f"- {_fmt_dur(dur)} {pct}%")
        elif tag == "IntervalsT":
            reps = int(float(el.get("Repeat", 1)))
            on_dur = int(float(el.get("OnDuration", 0)))
            off_dur = int(float(el.get("OffDuration", 0)))
            on_pct = int(float(el.get("OnPower", 1.0)) * 100)
            off_pct = int(float(el.get("OffPower", 0.5)) * 100)
            lines.append(f"{reps}x")
            lines.append(f"- {_fmt_dur(on_dur)} {on_pct}%")
            lines.append(f"- {_fmt_dur(off_dur)} {off_pct}%")
        elif tag in ("Ramp", "FreeRide"):
            dur = int(float(el.get("Duration", 0)))
            low = int(float(el.get("PowerLow", 0.5)) * 100)
            high = int(float(el.get("PowerHigh", 0.8)) * 100)
            lines.append(f"- {_fmt_dur(dur)} {low}-{high}%")
    return "\n".join(lines)



def parse_xml_workout(filepath: Path) -> dict | None:
    """
    Parse a custom .xml file for swim / run / rest workouts.
    Format: <workout> with <n>, <date>, <sport>, <duration>, <description> tags.
    """
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"    XML parse error in {filepath.name}: {e}")
        return None

    name = (root.findtext("n") or root.findtext("name") or filepath.stem).strip()
    sport_raw = (root.findtext("sport") or "Other").strip()
    sport = SPORT_MAP.get(sport_raw, sport_raw)
    description = (root.findtext("description") or "").strip()

    # Duration
    dur_text = root.findtext("duration") or "0"
    try:
        duration_sec = int(dur_text)  # in seconds
    except ValueError:
        duration_sec = 0

    # Date: first from XML tag, then from filename
    date_text = root.findtext("date") or ""
    workout_date = None
    if date_text:
        try:
            workout_date = datetime.strptime(date_text.strip(), "%Y-%m-%d").date()
        except ValueError:
            pass
    if workout_date is None:
        workout_date = parse_date_from_filename(filepath.name)

    # Rest day (duration=0, sport=Other) — create as a Day Off note
    if sport == "Other" and duration_sec == 0 and "rest" in name.lower():
        payload = {
            "category":         "NOTE",
            "type":             "Note",
            "name":             name,
            "description":      description,
        }
        if workout_date:
            payload["start_date_local"] = workout_date.isoformat() + "T00:00:00"
        return payload

    payload = {
        "category":         "WORKOUT",
        "type":             sport,
        "name":             name,
        "description":      description,
        "moving_time":      duration_sec if duration_sec > 0 else None,
    }
    if workout_date:
        payload["start_date_local"] = workout_date.isoformat() + "T00:00:00"

    return payload


# ─── Main logic ───────────────────────────────────────────────────────────────

def load_files(folder: Path, extensions: list[str]) -> list[Path]:
    """Collect all files with the given extensions, sorted by date in filename."""
    files = []
    for ext in extensions:
        files.extend(folder.glob(f"*{ext}"))
    # Sort by date from filename, then by name
    def sort_key(p):
        d = parse_date_from_filename(p.name)
        return (d or date(2099, 1, 1), p.name)
    return sorted(files, key=sort_key)


def upload_file(client: IntervalsClient | None, filepath: Path,
                dry_run: bool = False, overwrite: bool = False,
                tags: list[str] | None = None) -> bool:
    """Upload a single file to intervals.icu /events. Returns True on success.

    .zwo  — creates a calendar event with workout_doc containing Ramp steps for
            warmup/cooldown (intervals.icu native format, renders as gradient).
    .xml  — creates a calendar event with text description.
    tags  — list of tag strings added to every event (default: ["autoload"]).
    """
    if tags is None:
        tags = ["autoload"]

    ext = filepath.suffix.lower()

    if ext == ".zwo":
        payload = parse_zwo(filepath)
    elif ext == ".xml":
        payload = parse_xml_workout(filepath)
    else:
        print(f"    unknown format: {ext}, skipping")
        return False

    if payload is None:
        print(f"    failed to parse file")
        return False

    sport        = payload.get("type", "?")
    name         = payload.get("name", filepath.stem)
    workout_date = payload.get("start_date_local", "no date")
    tag_str      = ", ".join(tags) if tags else "none"
    print(f"    {workout_date}  [{sport}]  {name}  [tags: {tag_str}]")

    if dry_run:
        print(f"       (dry-run, skipping upload)")
        return True

    # ── Overwrite: delete existing event with same name on same date ──────────
    if overwrite and payload.get("start_date_local"):
        d = payload["start_date_local"].split("T")[0]
        try:
            for ev in client.get_events(d, d):
                if ev.get("name") == name:
                    client.delete_event(ev["id"])
                    print(f"       deleted existing event id={ev['id']}")
        except RuntimeError:
            pass

    try:
        if tags:
            payload["tags"] = tags
        result = client.create_event(payload)
        print(f"       created id={result.get('id', '?')}")
        return True
    except RuntimeError as e:
        print(f"       error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Upload workouts from a folder to intervals.icu",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Upload all files from the workouts folder:
  python3 upload_to_intervals.py --folder ./workouts --athlete i12345 --key abc123

  # Dry run — show what would be uploaded without actually uploading:
  python3 upload_to_intervals.py --folder ./workouts --dry-run

  # Overwrite existing workouts with the same name:
  python3 upload_to_intervals.py --folder ./workouts --overwrite

  # Via .env file or environment variables (recommended):
  python3 upload_to_intervals.py --folder ./workouts

  # Only specific extensions:
  python3 upload_to_intervals.py --folder ./workouts --ext .zwo

  # Only files within a date range:
  python3 upload_to_intervals.py --folder ./workouts --from 2026-04-01 --to 2026-04-30

  # Custom tags:
  python3 upload_to_intervals.py --folder ./workouts --tags plan2026 triathlon

  # No tags:
  python3 upload_to_intervals.py --folder ./workouts --tags
        """
    )

    parser.add_argument("--folder",   "-f",  default=".",
                        help="Folder with workout files (default: current directory)")
    parser.add_argument("--athlete",  "-a",
                        default=os.environ.get("INTERVALS_ATHLETE_ID", ""),
                        help="Athlete ID from intervals.icu (or INTERVALS_ATHLETE_ID env)")
    parser.add_argument("--key",      "-k",
                        default=os.environ.get("INTERVALS_API_KEY", ""),
                        help="intervals.icu API key (or INTERVALS_API_KEY env)")
    parser.add_argument("--ext",       nargs="+", default=[".zwo", ".xml"],
                        help="File extensions to process (default: .zwo .xml)")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Show what would be uploaded without uploading")
    parser.add_argument("--overwrite", action="store_true",
                        help="Delete and re-upload existing workouts with the same name")
    parser.add_argument("--from",     dest="date_from", default=None,
                        help="Upload only from this date (YYYY-MM-DD)")
    parser.add_argument("--to",       dest="date_to",   default=None,
                        help="Upload only up to and including this date (YYYY-MM-DD)")
    parser.add_argument("--verbose",  "-v", action="store_true",
                        help="Print full JSON payload for each file")
    parser.add_argument("--tags",     "-t", nargs="*", default=["autoload"],
                        metavar="TAG",
                        help="Tags added to every uploaded event. "
                             "Default: autoload. Use --tags with no args to upload without tags.")

    args = parser.parse_args()

    # ── Validation ──
    if not args.dry_run:
        if not args.athlete:
            print("Provide --athlete or set INTERVALS_ATHLETE_ID")
            sys.exit(1)
        if not args.key:
            print("Provide --key or set INTERVALS_API_KEY")
            sys.exit(1)

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"Folder not found: {folder}")
        sys.exit(1)

    # Date range for filtering
    date_from = datetime.strptime(args.date_from, "%Y-%m-%d").date() if args.date_from else None
    date_to   = datetime.strptime(args.date_to,   "%Y-%m-%d").date() if args.date_to   else None

    # ── Init ──
    client = None
    if not args.dry_run:
        client = IntervalsClient(args.athlete, args.key)
        print("Connecting to intervals.icu...")
        try:
            info = client.whoami()
            print(f"   Connected as: {info.get('name', args.athlete)}\n")
        except RuntimeError as e:
            print(f"   Connection failed: {e}")
            sys.exit(1)
    else:
        print("Dry-run mode — no files will be uploaded\n")

    # ── Collect files ──
    files = load_files(folder, args.ext)
    if not files:
        print(f"No {args.ext} files found in {folder}")
        sys.exit(0)

    print(f"Found {len(files)} file(s) in {folder}\n")

    # ── Upload ──
    ok = 0
    skipped = 0
    errors = 0

    for filepath in files:
        # Filter by date range
        file_date = parse_date_from_filename(filepath.name)
        if date_from and file_date and file_date < date_from:
            continue
        if date_to and file_date and file_date > date_to:
            continue

        print(f"  {filepath.name}")

        if args.verbose:
            # Print full payload
            ext = filepath.suffix.lower()
            if ext == ".zwo":
                payload = parse_zwo(filepath)
            else:
                payload = parse_xml_workout(filepath)
            if payload:
                print(f"     payload: {json.dumps(payload, ensure_ascii=False, indent=6)}")

        success = upload_file(client, filepath, dry_run=args.dry_run, overwrite=args.overwrite, tags=args.tags or [])
        if success:
            ok += 1
        else:
            errors += 1

    # ── Summary ──
    print(f"\n{'─'*50}")
    print(f"Uploaded: {ok}")
    if skipped:
        print(f"Skipped:  {skipped}")
    if errors:
        print(f"Errors:   {errors}")
    print(f"{'─'*50}")


if __name__ == "__main__":
    main()