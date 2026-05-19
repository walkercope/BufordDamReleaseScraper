"""Buford Dam release schedule scraper.

Fetches hourly hydropower generation schedules from the USACE Hydropower site
for Buford Dam / Lake Sidney Lanier. Outputs JSON for today and future dates.
"""
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime

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
    for table in soup.find_all("table"):
        header = table.find("th", string=lambda t: t and "Megawatts" in t)
        if not header:
            continue
        ranges = []
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) == 2:
                ranges.append({
                    "mw_range": cells[0].text.strip(),
                    "cfs_range": cells[1].text.strip(),
                })
        return ranges
    return []


def _mw_to_cfs(mw: int, flow_ranges: list[dict]) -> str:
    for r in flow_ranges:
        parts = r["mw_range"].replace(",", "").split(" - ")
        if len(parts) == 2:
            lo, hi = int(parts[0]), int(parts[1])
            if lo <= mw <= hi:
                return r["cfs_range"]
    return "unknown"


def _parse_schedule(soup: BeautifulSoup, date: str, flow_ranges: list[dict]) -> list[HourlySlot]:
    gv = soup.find("table", {"id": lambda x: x and "GridView" in x})
    if not gv:
        return []
    slots = []
    for row in gv.find_all("tr")[2:]:  # skip two header rows
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        try:
            mw = int(cells[1].text.strip().replace(",", ""))
        except ValueError:
            continue
        slots.append(HourlySlot(
            date=date,
            time_slot=cells[0].text.strip(),
            generation_mw=mw,
            flow_rate_cfs=_mw_to_cfs(mw, flow_ranges),
        ))
    return slots


def fetch_schedule() -> list[HourlySlot]:
    """Fetch Buford Dam schedule for today and all future available dates."""
    session = requests.Session()
    session.headers.update(HEADERS)

    resp = session.get(BASE_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    resp2 = session.post(BASE_URL, data={
        "__EVENTTARGET": "Plant_Selector",
        "__EVENTARGUMENT": "",
        "Plant_Selector": BUFORD_PLANT_ID,
        "Date_Selector": "",
        **_viewstate(soup),
    }, timeout=30)
    resp2.raise_for_status()
    soup2 = BeautifulSoup(resp2.text, "lxml")

    date_selector = soup2.find("select", {"id": "Date_Selector"})
    if not date_selector:
        raise RuntimeError("Date selector not found — site structure may have changed.")

    today = datetime.now().date()
    available = [
        opt["value"] for opt in date_selector.find_all("option")
        if opt["value"] != "0"
        and datetime.strptime(opt["value"], "%m/%d/%Y").date() >= today
    ]
    if not available:
        raise RuntimeError("No current or future dates available.")

    all_slots: list[HourlySlot] = []
    flow_ranges: list[dict] = []

    for date in available:
        resp3 = session.post(BASE_URL, data={
            "__EVENTTARGET": "Date_Selector",
            "__EVENTARGUMENT": "",
            "Plant_Selector": BUFORD_PLANT_ID,
            "Date_Selector": date,
            **_viewstate(soup2),
        }, timeout=30)
        resp3.raise_for_status()
        soup3 = BeautifulSoup(resp3.text, "lxml")

        if not flow_ranges:
            flow_ranges = _parse_flow_rate_table(soup3)

        all_slots.extend(_parse_schedule(soup3, date, flow_ranges))
        soup2 = soup3

    return all_slots


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Fetch Buford Dam release schedule (today + future dates).")
    parser.add_argument("--out", metavar="FILE", default="schedule.json",
                        help="Output JSON file (default: schedule.json).")
    args = parser.parse_args()

    slots = fetch_schedule()
    data = [asdict(s) for s in slots]

    Path(args.out).write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {len(slots)} slots across {len({s.date for s in slots})} date(s) to {args.out}")
