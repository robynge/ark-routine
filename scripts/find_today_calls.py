#!/usr/bin/env python3
"""Find today's earnings calls that are in the ARK universe AND transcript_ready.

Reads EARNINGSCALL_API_KEY from env. Date defaults to America/New_York "today"
(NOT date.today(), which is UTC and silently skips ET evening calls).

Usage:
    python find_today_calls.py UNIVERSE.json [YYYY-MM-DD] > today_calls.json
"""
import json
import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import earningscall
from earningscall import get_calendar

if len(sys.argv) not in (2, 3):
    sys.exit("usage: find_today_calls.py UNIVERSE.json [YYYY-MM-DD] > today_calls.json")

api_key = os.environ.get("EARNINGSCALL_API_KEY")
if not api_key:
    sys.exit("EARNINGSCALL_API_KEY env required")
earningscall.api_key = api_key

universe = json.loads(open(sys.argv[1]).read())
ark_tickers_upper = {t.upper() for t in universe.keys()}

if len(sys.argv) == 3:
    target = date.fromisoformat(sys.argv[2])
else:
    # America/New_York "today" — UTC date silently misses ET evening calls.
    target = datetime.now(ZoneInfo("America/New_York")).date()

cal = list(get_calendar(target))

matches = []
for c in cal:
    if not getattr(c, "transcript_ready", False):
        continue
    # Calendar events expose the resolved symbol directly. The earlier
    # name-based get_company() lookup mis-resolved most companies.
    symbol = (getattr(c, "symbol", "") or "").upper()
    if not symbol or symbol not in ark_tickers_upper:
        continue
    matches.append({
        "ticker": symbol,
        "company": c.company_name,
        "year": c.year,
        "quarter": c.quarter,
        "conference_date": c.conference_date.isoformat() if c.conference_date else None,
        "ark_funds": universe[symbol]["funds"],
    })

json.dump(matches, sys.stdout, indent=2)
