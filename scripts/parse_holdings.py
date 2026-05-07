#!/usr/bin/env python3
"""Parse ARK ETF holdings CSVs into a unified ticker→funds dict.

Drops empty-ticker rows (cash, private holdings, warrants).
Drops the trailing legal-disclaimer row.

Usage:
    python parse_holdings.py CSV1 [CSV2 ...] > universe.json
"""
import csv
import io
import json
import sys
from pathlib import Path


def parse_one(path):
    text = Path(path).read_text()
    rows = []
    reader = csv.DictReader(io.StringIO(text))
    for r in reader:
        ticker = (r.get("ticker") or "").strip()
        # Strip Bloomberg-style market suffix: "DKNG UW" → "DKNG", "NU UN" → "NU"
        ticker = ticker.split()[0] if ticker else ticker
        fund = (r.get("fund") or "").strip()
        company = (r.get("company") or r.get("name") or "").strip()
        weight = (r.get("weight (%)") or r.get("weight") or "").strip()
        if not ticker:
            continue
        if not fund:
            continue
        rows.append({"ticker": ticker, "fund": fund, "company": company, "weight": weight})
    return rows


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: parse_holdings.py CSV1 [CSV2 ...] > universe.json")
    universe = {}
    for p in sys.argv[1:]:
        for r in parse_one(p):
            t = r["ticker"]
            entry = universe.setdefault(t, {"ticker": t, "company": r["company"], "funds": {}})
            entry["funds"][r["fund"]] = r["weight"]
    json.dump(universe, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
