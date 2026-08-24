#!/usr/bin/env python3
"""Rebuild data/consolidated/<FUND>.csv -- one file per fund -- from every
per-day holdings CSV under data/holdings/<YYYY>/<YYYY-MM-DD>/.

One row per (trading date, holding), oldest day first, each new day appended
below the last. Columns (identical in every file; `fund` is constant within a
file but kept so files concatenate cleanly):

    date, fund, company, ticker, cusip, weight, shares_held, market_value

Values are cleaned for analysis, not for display: ISO dates that sort correctly,
and bare numbers with no $ , or % glyphs so a spreadsheet or pandas reads them
as numbers. The three venture funds (ARKSX/ARKUX/ARKVX) publish only a weight,
so shares_held and market_value are empty for their rows. ARKY's CSV format
varies by day: standard-schema days (e.g. 2026-08-20/21, backfilled by hand)
parse like any fund; dateless autocallable-notes days contribute no rows.

Two facts about the source archive drive the dedupe:
  * The daily Action stamps folders with the calendar day it ran, so weekend and
    holiday folders are verbatim copies of the previous trading day's files.
  * Venture funds report monthly, so their file is re-copied every day for weeks.
Both mean the same (date, fund) shows up under many folders. We therefore key on
the date INSIDE each file and keep one file per (date, fund) -- preferring the
one whose folder name matches its own date, which is the day it was really
published. Dedupe is per FILE, never per row, so a fund legitimately holding two
rows with the same identity (buffer-ETF option legs) keeps both.

Full rebuild every run: deterministic, so re-running is a no-op the committer
sees as an empty diff, and there is no incremental-append state to drift.

Usage: build_history.py [--repo .] [--outdir data/consolidated]
"""
import argparse
import csv
import glob
import os
import re
import sys

HEADER = ["date", "fund", "company", "ticker", "cusip",
          "weight", "shares_held", "market_value"]

FOLDER_RE = re.compile(r"\d{4}-\d{2}-\d{2}$")


def iso(mdy):
    """'08/05/2026' -> '2026-08-05'; passes through anything already ISO."""
    mdy = mdy.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", mdy):
        return mdy
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", mdy)
    if not m:
        return None
    mm, dd, yy = m.groups()
    return f"{yy}-{int(mm):02d}-{int(dd):02d}"


def num(s):
    """'$577,363,235.15' / '1,763,749' / '9.51%' -> bare number string.

    Strips glyphs and validates, but returns the ORIGINAL digits rather than a
    reformatted float: market values run to 11+ significant digits and any
    round-trip through a format spec silently drops cents.
    """
    s = (s or "").strip().replace(",", "").replace("$", "").replace("%", "")
    if s in ("", "-", "--", "N/A", "NA"):
        return ""
    neg = s.startswith("(") and s.endswith(")")   # accounting negatives
    if neg:
        s = s[1:-1]
    try:
        float(s)
    except ValueError:
        return ""
    return f"-{s}" if neg else s


def read_holdings(path, fund_hint=""):
    """-> [row] for one per-fund CSV. Rows carry their own date; a handful of
    blog-sourced files cover two trading days at once, so callers must group by
    each row's date rather than assume one date per file."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if len(rows) < 2:
        return []
    header = [h.strip().lower() for h in rows[0]]
    try:
        ix = {name: header.index(name) for name in ("date", "fund", "company", "ticker", "cusip")}
    except ValueError:
        return []
    # 11 ETFs carry shares + market value; the 3 venture funds carry neither
    i_shares = header.index("shares") if "shares" in header else None
    i_mv = next((i for i, h in enumerate(header) if h.startswith("market value")), None)
    i_wt = next((i for i, h in enumerate(header) if h.startswith("weight")), None)

    out = []
    for r in rows[1:]:
        # ARK appends a one-cell legal disclaimer to every file
        if len([c for c in r if c.strip()]) < 3 or len(r) <= max(ix.values()):
            continue
        d = iso(r[ix["date"]])
        if not d:
            continue
        out.append([
            d,
            r[ix["fund"]].strip() or fund_hint,
            r[ix["company"]].strip(),
            r[ix["ticker"]].strip(),
            r[ix["cusip"]].strip(),
            num(r[i_wt]) if i_wt is not None and i_wt < len(r) else "",
            num(r[i_shares]) if i_shares is not None and i_shares < len(r) else "",
            num(r[i_mv]) if i_mv is not None and i_mv < len(r) else "",
        ])
    return out


def selftest():
    import tempfile
    assert iso("08/05/2026") == "2026-08-05"
    assert iso("1/2/2026") == "2026-01-02"
    assert iso("2026-08-05") == "2026-08-05"
    assert iso("garbage") is None
    assert num("$577,363,235.15") == "577363235.15"
    assert num("1,763,749") == "1763749"
    assert num("9.51%") == "9.51"
    assert num("(1,234.5)") == "-1234.5"
    assert num("") == "" and num("N/A") == "" and num("-") == ""

    d = tempfile.mkdtemp()
    etf = ("date,fund,company,ticker,cusip,shares,market value ($),weight (%)\n"
           '{d},ARKK,TESLA INC,TSLA,88160R101,"1,000","$2,000.00",10.00%\n'
           '"Holdings are subject to change."\n')
    # a real trading day
    os.makedirs(os.path.join(d, "data", "holdings", "2026", "2026-01-05"))
    open(os.path.join(d, "data", "holdings", "2026", "2026-01-05", "ARKK_Holdings_2026-01-05.csv"),
         "w").write(etf.format(d="01/05/2026"))
    # weekend folder: verbatim copy of Friday, must collapse
    os.makedirs(os.path.join(d, "data", "holdings", "2026", "2026-01-06"))
    open(os.path.join(d, "data", "holdings", "2026", "2026-01-06", "ARKK_Holdings_2026-01-06.csv"),
         "w").write(etf.format(d="01/05/2026"))
    # venture fund: weight only, no shares/market value
    open(os.path.join(d, "data", "holdings", "2026", "2026-01-05", "ARKVX_Holdings_2026-01-05.csv"),
         "w").write("date,fund,company,ticker,cusip,weight (%)\n"
                    "01/05/2026,ARKVX,OpenAI,,,6.18%\n")

    rows = read_holdings(os.path.join(d, "data", "holdings", "2026", "2026-01-05",
                                      "ARKK_Holdings_2026-01-05.csv"), "ARKK")
    assert rows == [["2026-01-05", "ARKK", "TESLA INC", "TSLA", "88160R101",
                     "10.00", "1000", "2000.00"]], rows     # disclaimer dropped

    outdir = os.path.join("data", "consolidated")
    sys.argv = ["build_history.py", "--repo", d, "--outdir", outdir]
    main()
    arkk = list(csv.reader(open(os.path.join(d, outdir, "ARKK.csv"), newline="")))
    arkvx = list(csv.reader(open(os.path.join(d, outdir, "ARKVX.csv"), newline="")))
    assert arkk[0] == HEADER and arkvx[0] == HEADER
    assert len(arkk) == 2, arkk                             # header + one row, weekend copy gone
    assert len(arkvx) == 2, arkvx
    assert arkk[1][1] == "ARKK" and arkvx[1][1] == "ARKVX"
    assert arkvx[1][6] == "" and arkvx[1][7] == "", arkvx[1]  # venture: blank shares + mv
    print("selftest OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.path.join(os.path.dirname(__file__), ".."))
    ap.add_argument("--outdir", default=os.path.join("data", "consolidated"))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    holdings = os.path.join(args.repo, "data", "holdings")
    chosen = {}   # (date, fund) -> (folder_matches_that_date, rows)
    scanned = groups = 0
    for path in sorted(glob.glob(os.path.join(holdings, "*", "*", "*.csv"))):
        folder = os.path.basename(os.path.dirname(path))
        if not FOLDER_RE.fullmatch(folder):
            continue          # skips the LATEST symlink
        scanned += 1
        rows = read_holdings(path, fund_hint=os.path.basename(path).split("_")[0])
        if not rows:
            continue
        # a file may cover more than one trading day, so split before choosing
        per_key = {}
        for r in rows:
            per_key.setdefault((r[0], r[1]), []).append(r)
        for key, rws in per_key.items():
            groups += 1
            canonical = (folder == key[0])
            prev = chosen.get(key)
            # keep the copy filed on its own date; otherwise first one wins
            if prev is None or (canonical and not prev[0]):
                chosen[key] = (canonical, rws)

    by_fund = {}
    for _, rws in chosen.values():
        for r in rws:
            by_fund.setdefault(r[1], []).append(r)

    out_dir = os.path.join(args.repo, args.outdir)
    os.makedirs(out_dir, exist_ok=True)
    total = 0
    for fund in sorted(by_fund):
        rows = by_fund[fund]
        # oldest day first; within a day, largest position first
        rows.sort(key=lambda r: (r[0], -(float(r[5]) if r[5] else 0.0)))
        with open(os.path.join(out_dir, f"{fund}.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(HEADER)
            w.writerows(rows)
        total += len(rows)

    dates = sorted({r[0] for rws in by_fund.values() for r in rws})
    print(f"scanned {scanned} files -> {groups} (date,fund) groups -> kept {len(chosen)} "
          f"({groups - len(chosen)} duplicate copies dropped)")
    print(f"wrote {len(by_fund)} per-fund files to {out_dir}")
    print(f"  {total:,} rows | {len(dates)} dates {dates[0]}..{dates[-1]} | {len(by_fund)} funds")
    print(f"  {', '.join(sorted(by_fund))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
