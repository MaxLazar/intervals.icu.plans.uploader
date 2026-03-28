#!/usr/bin/env python3
"""
intervals.icu Event Cleanup
============================
Deletes planned events (WORKOUT, NOTE, RACE) from the calendar
starting from a given date.

Usage:
  python3 cleanup_intervals.py --from 2026-04-01
  python3 cleanup_intervals.py --from 2026-04-01 --to 2026-04-30
  python3 cleanup_intervals.py --from 2026-04-01 --dry-run

Credentials via .env file or environment variables:
  INTERVALS_ATHLETE_ID=i00000
  INTERVALS_API_KEY=your_key_here
"""
from __future__ import annotations

import os
import sys
import json
import ssl
import base64
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError


def _load_dotenv(path: Path = Path(".env")) -> None:
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
    ctx = ssl.create_default_context()
    if ctx.get_ca_certs():
        return ctx
    for ca_bundle in (
        "/etc/ssl/cert.pem",
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt",
    ):
        if Path(ca_bundle).is_file():
            return ssl.create_default_context(cafile=ca_bundle)
    return ctx


API_BASE = "https://intervals.icu/api/v1"

# Only delete planned events, not completed activities
DELETABLE_CATEGORIES = {"WORKOUT", "NOTE", "RACE", "HOW_AM_I_FEELING"}


class IntervalsClient:
    def __init__(self, athlete_id: str, api_key: str):
        self.athlete_id = athlete_id
        self.base = f"{API_BASE}/athlete/{athlete_id}"
        creds = base64.b64encode(f"API_KEY:{api_key}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, body=None):
        url = f"{self.base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = Request(url, data=data, headers=self.headers, method=method)
        try:
            with urlopen(req, timeout=30, context=_ssl_context()) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except HTTPError as e:
            error_body = e.read().decode(errors="replace")
            raise RuntimeError(f"HTTP {e.code} {e.reason} -> {url}\n{error_body}")
        except URLError as e:
            raise RuntimeError(f"Network error -> {url}: {e.reason}")

    def whoami(self) -> dict:
        return self._request("GET", "")

    def get_events(self, start: str, end: str) -> list:
        return self._request("GET", f"/events?oldest={start}&newest={end}")

    def delete_event(self, event_id: int) -> None:
        self._request("DELETE", f"/events/{event_id}")


def main():
    parser = argparse.ArgumentParser(
        description="Delete planned events from intervals.icu calendar",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Delete all planned events from April 1 onwards (up to 1 year):
  python3 cleanup_intervals.py --from 2026-04-01

  # Delete only April:
  python3 cleanup_intervals.py --from 2026-04-01 --to 2026-04-30

  # Preview without deleting:
  python3 cleanup_intervals.py --from 2026-04-01 --dry-run
        """
    )
    parser.add_argument("--from", dest="date_from", required=True,
                        help="Start date (YYYY-MM-DD), inclusive")
    parser.add_argument("--to", dest="date_to", default=None,
                        help="End date (YYYY-MM-DD), inclusive (default: 1 year from --from)")
    parser.add_argument("--athlete", "-a",
                        default=os.environ.get("INTERVALS_ATHLETE_ID", ""),
                        help="Athlete ID (or INTERVALS_ATHLETE_ID env)")
    parser.add_argument("--key", "-k",
                        default=os.environ.get("INTERVALS_API_KEY", ""),
                        help="API key (or INTERVALS_API_KEY env)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be deleted without deleting")

    args = parser.parse_args()

    if not args.athlete:
        print("Provide --athlete or set INTERVALS_ATHLETE_ID")
        sys.exit(1)
    if not args.key:
        print("Provide --key or set INTERVALS_API_KEY")
        sys.exit(1)

    date_from = datetime.strptime(args.date_from, "%Y-%m-%d").date()
    date_to = (
        datetime.strptime(args.date_to, "%Y-%m-%d").date()
        if args.date_to
        else date_from + timedelta(days=365)
    )

    if date_to < date_from:
        print("--to must be >= --from")
        sys.exit(1)

    client = IntervalsClient(args.athlete, args.key)
    print("Connecting to intervals.icu...")
    try:
        info = client.whoami()
        print(f"   Connected as: {info.get('name', args.athlete)}\n")
    except RuntimeError as e:
        print(f"   Connection failed: {e}")
        sys.exit(1)

    print(f"Fetching events {date_from} -> {date_to}...")
    try:
        events = client.get_events(date_from.isoformat(), date_to.isoformat())
    except RuntimeError as e:
        print(f"   Failed to fetch events: {e}")
        sys.exit(1)

    targets = [e for e in events if e.get("category") in DELETABLE_CATEGORIES]

    if not targets:
        print("No planned events found in this range.")
        return

    print(f"Found {len(targets)} planned event(s):\n")
    for ev in targets:
        date_str = (ev.get("start_date_local") or "")[:10]
        print(f"  [{date_str}]  {ev.get('category', '?'):20}  {ev.get('name', '?')}  (id={ev['id']})")

    if args.dry_run:
        print(f"\nDry-run -- nothing deleted.")
        return

    print(f"\nDeleting {len(targets)} event(s)...")
    deleted = 0
    errors = 0
    for ev in targets:
        try:
            client.delete_event(ev["id"])
            date_str = (ev.get("start_date_local") or "")[:10]
            print(f"  deleted  [{date_str}]  {ev.get('name', '?')}  (id={ev['id']})")
            deleted += 1
        except RuntimeError as e:
            print(f"  error    [{ev['id']}]  {ev.get('name', '?')}: {e}")
            errors += 1

    print(f"\n{'─'*50}")
    print(f"Deleted: {deleted}")
    if errors:
        print(f"Errors:  {errors}")
    print(f"{'─'*50}")


if __name__ == "__main__":
    main()
