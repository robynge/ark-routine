#!/usr/bin/env python3
"""Fetch + render ARK earnings transcripts for one or more ET dates.

Used by .github/workflows/fetch-earnings.yml.

For each ET date passed:
  1. Read pre-fetched ARK universe (data/LATEST/universe.json)
  2. Query earningscall calendar for that date, intersect with ARK universe,
     keep transcript_ready calls
  3. For each call: fetch current + prior-quarter transcripts (level=2 speakers
     JSON), then render styled transcript PDFs via the routine's renderer
  4. Write a date index at earnings/_index/<DATE>.json

Layout produced:
  earnings/<TICKER>/<YYYY>Q<q>.json   speakers JSON (level 2)
  earnings/<TICKER>/<YYYY>Q<q>.pdf    styled transcript PDF
  earnings/_index/<DATE>.json         { date, calls: [{ticker, year, quarter, ...}] }

Idempotent: skips a (ticker, year, quarter) whose JSON+PDF are both already
present. Re-runs only refresh the date index.

Reads EARNINGSCALL_API_KEY from env. Reads renderer scripts from a clone of
robynge/ark-earnings-routine at SCRIPTS_REPO_DIR (env, default ./.ear).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import earningscall
from earningscall import get_calendar

ROOT = Path(os.environ.get("REPO_ROOT", ".")).resolve()
SCRIPTS_REPO = Path(os.environ.get("SCRIPTS_REPO_DIR", ".ear")).resolve()
RENDER_SCRIPT = SCRIPTS_REPO / "scripts" / "render_transcript.py"
FETCH_SCRIPT = SCRIPTS_REPO / "scripts" / "fetch_transcript.py"
EARNINGS_DIR = ROOT / "earnings"
INDEX_DIR = EARNINGS_DIR / "_index"


def prior_quarter(year: int, quarter: int) -> tuple[int, int]:
    return (year, quarter - 1) if quarter > 1 else (year - 1, 4)


def find_calls(universe: dict, target: date) -> list[dict]:
    """ARK calls on `target` ET date with transcript_ready=True."""
    ark = {t.upper() for t in universe.keys()}
    matches = []
    for c in get_calendar(target):
        if not getattr(c, "transcript_ready", False):
            continue
        sym = (getattr(c, "symbol", "") or "").upper()
        if not sym or sym not in ark:
            continue
        matches.append({
            "ticker": sym,
            "company": c.company_name,
            "year": c.year,
            "quarter": c.quarter,
            "conference_date": c.conference_date.isoformat() if c.conference_date else None,
            "ark_funds": universe[sym].get("funds", {}),
        })
    return matches


def run(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    return r.returncode, out[:300]


def ensure_transcript(ticker: str, year: int, quarter: int, company: str,
                      conf_date: str | None) -> tuple[bool, bool]:
    """Fetch JSON + render PDF for one (ticker, year, quarter). Returns
    (json_new, pdf_new)."""
    co_dir = EARNINGS_DIR / ticker
    co_dir.mkdir(parents=True, exist_ok=True)
    json_path = co_dir / f"{year}Q{quarter}.json"
    pdf_path = co_dir / f"{year}Q{quarter}.pdf"

    json_new = False
    if not json_path.exists():
        rc, msg = run(["python3", str(FETCH_SCRIPT), ticker, str(year), str(quarter), str(json_path)])
        if rc != 0:
            print(f"  [err fetch] {ticker} {year}Q{quarter}: {msg}", flush=True)
            return False, False
        json_new = True
        print(f"  [fetched] {ticker} {year}Q{quarter}", flush=True)

    pdf_new = False
    if json_path.exists() and not pdf_path.exists():
        meta = {
            "ticker": ticker, "company": company,
            "year": year, "quarter": quarter,
            "conference_date": conf_date or "",
        }
        meta_path = co_dir / f"{year}Q{quarter}.meta.json"
        meta_path.write_text(json.dumps(meta))
        rc, msg = run(["python3", str(RENDER_SCRIPT), str(json_path), str(meta_path), str(pdf_path)])
        # Don't keep meta files in the repo; they're re-derivable
        meta_path.unlink(missing_ok=True)
        if rc != 0:
            print(f"  [err render] {ticker} {year}Q{quarter}: {msg}", flush=True)
        else:
            pdf_new = True
            print(f"  [rendered] {ticker} {year}Q{quarter}", flush=True)
    return json_new, pdf_new


def process_date(target: date, universe: dict) -> dict:
    print(f"\n=== {target.isoformat()} ===", flush=True)
    calls = find_calls(universe, target)
    print(f"  matches: {len(calls)}", flush=True)

    EARNINGS_DIR.mkdir(exist_ok=True)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    seen = set()
    counts = {"calls": len(calls), "json_new": 0, "pdf_new": 0}
    for c in calls:
        tic = c["ticker"]
        # Current quarter
        jn, pn = ensure_transcript(tic, c["year"], c["quarter"], c["company"], c["conference_date"])
        counts["json_new"] += int(jn); counts["pdf_new"] += int(pn)
        # Prior quarter (for cross-quarter analysis); conference_date unknown here
        py, pq = prior_quarter(c["year"], c["quarter"])
        key = (tic, py, pq)
        if key not in seen:
            seen.add(key)
            jn, pn = ensure_transcript(tic, py, pq, c["company"], None)
            counts["json_new"] += int(jn); counts["pdf_new"] += int(pn)

    index = {
        "date": target.isoformat(),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_calls": len(calls),
        "calls": calls,
    }
    (INDEX_DIR / f"{target.isoformat()}.json").write_text(json.dumps(index, indent=2))
    print(f"  -> {counts}", flush=True)
    return counts


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("dates", nargs="*", help="YYYY-MM-DD ET dates. Default: today + yesterday (ET).")
    args = p.parse_args()

    api_key = os.environ.get("EARNINGSCALL_API_KEY")
    if not api_key:
        sys.exit("EARNINGSCALL_API_KEY env required")
    earningscall.api_key = api_key

    if not RENDER_SCRIPT.exists() or not FETCH_SCRIPT.exists():
        sys.exit(f"renderer scripts missing under {SCRIPTS_REPO}/scripts/. "
                 f"Set SCRIPTS_REPO_DIR to a clone of robynge/ark-earnings-routine.")

    # Universe: prefer prebuilt JSON if present; else derive from local CSVs.
    uni_json = ROOT / "data" / "LATEST" / "universe.json"
    if uni_json.exists():
        universe = json.loads(uni_json.read_text())
    else:
        # Build universe.json on the fly from data/LATEST/*.csv using the
        # parser shipped alongside the renderer scripts.
        parser = SCRIPTS_REPO / "scripts" / "parse_holdings.py"
        if not parser.exists():
            sys.exit("no universe.json and no parse_holdings.py available")
        csvs = sorted((ROOT / "data" / "LATEST").glob("*.csv"))
        if not csvs:
            sys.exit(f"no CSVs found at {ROOT}/data/LATEST/*.csv")
        r = subprocess.run(["python3", str(parser), *[str(p) for p in csvs]],
                           capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"parse_holdings failed: {r.stderr}")
        universe = json.loads(r.stdout)
        print(f"built universe in-memory from {len(csvs)} CSVs: {len(universe)} tickers")

    if args.dates:
        targets = [date.fromisoformat(d) for d in args.dates]
    else:
        today_et = datetime.now(ZoneInfo("America/New_York")).date()
        targets = [today_et - timedelta(days=1), today_et]  # yesterday, today

    total = {"json_new": 0, "pdf_new": 0, "dates": []}
    for t in targets:
        c = process_date(t, universe)
        total["dates"].append({"date": t.isoformat(), **c})
        total["json_new"] += c["json_new"]; total["pdf_new"] += c["pdf_new"]
    print(f"\n=== TOTAL: {json.dumps(total)}", flush=True)


if __name__ == "__main__":
    main()
