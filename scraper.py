"""Buford Dam release schedule scraper.

Fetches hourly hydropower generation schedules from the USACE Hydropower site
for Buford Dam / Lake Sidney Lanier and saves them to CSV and JSON.
"""
import csv
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://spatialdata.usace.army.mil/Hydropower/"
BUFORD_PLANT_ID = "2"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


@dataclass
class HourlySlot:
    date: str
    time_slot: str
    generation_mw: int
    flow_rate_cfs: str


def _viewstate(soup: BeautifulSoup) -> dict:
    return {
        "__VIEWSTATE": soup.find("input", {"id": "__VIEWSTATE"})["value"],
        "__VIEWSTATEGENERATOR": soup.find("input", {"id": "__VIEWSTATEGENERATOR"})["value"],
        "__EVENTVALIDATION": soup.find("input", {"id": "__EVENTVALIDATION"})["value"],
    }


def _parse_flow_rate_table(soup: BeautifulSoup) -> list[dict]:
    """Parse the MW → CFS conversion ranges from the page."""
    ranges = []
    for table in soup.find_all("table"):
        caption = table.find_previous("table", {"bgcolor": "#054E81"})
        header = table.find("th", string=lambda t: t and "Megawatts" in t)
        if not header:
            continue
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) == 2:
                ranges.append({
                    "mw_range": cells[0].text.strip(),
                    "cfs_range": cells[1].text.strip(),
                })
    return ranges


def _mw_to_cfs(mw: int, flow_ranges: list[dict]) -> str:
    """Map a MW value to its CFS range string."""
    for r in flow_ranges:
        parts = r["mw_range"].replace(",", "").split(" - ")
        if len(parts) == 2:
            lo, hi = int(parts[0]), int(parts[1])
            if lo <= mw <= hi:
                return r["cfs_range"]
    return "unknown"


def _parse_schedule_table(soup: BeautifulSoup, date: str, flow_ranges: list[dict]) -> list[HourlySlot]:
    """Parse the hourly schedule GridView table."""
    gv = soup.find("table", {"id": lambda x: x and "GridView" in x})
    if not gv:
        return []

    slots = []
    rows = gv.find_all("tr")
    for row in rows[2:]:  # skip header rows
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        time_slot = cells[0].text.strip()
        mw_text = cells[1].text.strip().replace(",", "")
        try:
            mw = int(mw_text)
        except ValueError:
            continue
        slots.append(HourlySlot(
            date=date,
            time_slot=time_slot,
            generation_mw=mw,
            flow_rate_cfs=_mw_to_cfs(mw, flow_ranges),
        ))
    return slots


def fetch_schedule(dates: list[str] | None = None) -> list[HourlySlot]:
    """Fetch Buford Dam schedule for given dates (or all available if None)."""
    session = requests.Session()
    session.headers.update(HEADERS)

    # Initial GET to get session + VIEWSTATE
    resp = session.get(BASE_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    vs = _viewstate(soup)

    # POST plant selection to populate date dropdown
    resp2 = session.post(BASE_URL, data={
        "__EVENTTARGET": "Plant_Selector",
        "__EVENTARGUMENT": "",
        "Plant_Selector": BUFORD_PLANT_ID,
        "Date_Selector": "",
        **vs,
    }, timeout=30)
    resp2.raise_for_status()
    soup2 = BeautifulSoup(resp2.text, "lxml")

    # Get available dates (skip the "---" placeholder with value "0")
    date_selector = soup2.find("select", {"id": "Date_Selector"})
    if not date_selector:
        raise RuntimeError("Date selector not found — site may have changed.")
    available = [
        opt["value"] for opt in date_selector.find_all("option")
        if opt["value"] != "0"
    ]
    if not available:
        raise RuntimeError("No dates available in dropdown.")

    target_dates = dates if dates else available
    print(f"Available dates: {available}")
    print(f"Fetching: {target_dates}")

    # Fetch schedule for each requested date
    all_slots: list[HourlySlot] = []
    flow_ranges: list[dict] = []

    for date in target_dates:
        if date not in available:
            print(f"  Skipping {date} (not in available dates)", file=sys.stderr)
            continue

        print(f"  Fetching {date}...", end=" ")
        vs2 = _viewstate(soup2)
        resp3 = session.post(BASE_URL, data={
            "__EVENTTARGET": "Date_Selector",
            "__EVENTARGUMENT": "",
            "Plant_Selector": BUFORD_PLANT_ID,
            "Date_Selector": date,
            **vs2,
        }, timeout=30)
        resp3.raise_for_status()
        soup3 = BeautifulSoup(resp3.text, "lxml")

        if not flow_ranges:
            flow_ranges = _parse_flow_rate_table(soup3)

        slots = _parse_schedule_table(soup3, date, flow_ranges)
        all_slots.extend(slots)
        print(f"{len(slots)} slots")

        # Update soup2 for next iteration (fresh VIEWSTATE)
        soup2 = soup3

    return all_slots


def save_csv(slots: list[HourlySlot], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "time_slot", "generation_mw", "flow_rate_cfs"])
        writer.writeheader()
        writer.writerows(asdict(s) for s in slots)
    print(f"Saved CSV: {path}")


def save_json(slots: list[HourlySlot], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump([asdict(s) for s in slots], f, indent=2)
    print(f"Saved JSON: {path}")


def print_table(slots: list[HourlySlot]) -> None:
    if not slots:
        print("No data.")
        return
    current_date = None
    for s in slots:
        if s.date != current_date:
            current_date = s.date
            print(f"\n{'='*55}")
            print(f"  Buford Dam Release Schedule — {s.date}")
            print(f"{'='*55}")
            print(f"  {'Time (Eastern)':<25} {'MW':>6}  CFS Range")
            print(f"  {'-'*25} {'-'*6}  {'-'*15}")
        print(f"  {s.time_slot:<25} {s.generation_mw:>6}  {s.flow_rate_cfs}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape Buford Dam release schedule.")
    parser.add_argument("--dates", nargs="*", metavar="M/D/YYYY",
                        help="Specific dates to fetch (default: all available).")
    parser.add_argument("--csv", metavar="FILE", help="Save results to CSV file.")
    parser.add_argument("--json", metavar="FILE", help="Save results to JSON file.")
    parser.add_argument("--no-print", action="store_true", help="Suppress table output.")
    args = parser.parse_args()

    slots = fetch_schedule(dates=args.dates)

    if not args.no_print:
        print_table(slots)

    if args.csv:
        save_csv(slots, Path(args.csv))
    if args.json:
        save_json(slots, Path(args.json))

    if not args.csv and not args.json:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = Path(f"buford_schedule_{stamp}.csv")
        save_csv(slots, out)
