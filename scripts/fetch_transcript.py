#!/usr/bin/env python3
"""Fetch a single level-2 transcript via earningscall and save speakers JSON.

Usage:
    python fetch_transcript.py TICKER YEAR QUARTER OUT_JSON

Throttle: 3-second sleep before fetch (20/min API limit).
"""
import json
import os
import sys
import time

import earningscall
from earningscall import get_company

if len(sys.argv) != 5:
    sys.exit("usage: fetch_transcript.py TICKER YEAR QUARTER OUT_JSON")
ticker, year, quarter, out_path = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]

api_key = os.environ.get("EARNINGSCALL_API_KEY")
if not api_key:
    sys.exit("EARNINGSCALL_API_KEY env required")
earningscall.api_key = api_key

co = get_company(ticker.lower())
if co is None:
    sys.exit(f"earningscall does not have ticker {ticker}")

time.sleep(3)
tx = co.get_transcript(year=year, quarter=quarter, level=2)
if not tx or not tx.speakers:
    sys.exit(f"empty transcript for {ticker} {year}Q{quarter}")

name_map = {}
speakers = []
for sp in tx.speakers:
    sid = sp.speaker
    if sid not in name_map and sp.speaker_info:
        name_map[sid] = {"name": sp.speaker_info.name, "title": sp.speaker_info.title}
    speakers.append({"speaker": sid, "text": sp.text})

obj = {
    "event": {"ticker": ticker, "year": year, "quarter": quarter},
    "speaker_name_map_v2": name_map,
    "speakers": speakers,
}
with open(out_path, "w") as f:
    json.dump(obj, f, indent=2, ensure_ascii=False)
print(f"saved {out_path}: {len(speakers)} segments, {len(name_map)} speakers")
