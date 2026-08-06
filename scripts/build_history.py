#!/usr/bin/env python3
"""Rebuild data/history/ark_holdings_history.csv from every per-day holdings CSV.

One row per (trading date, fund, holding), oldest day first, each new day
appended below the last. Columns:

    date, fund, company, ticker, cusip, weight, shares_held, market_value

Values are cleaned for analysis, not for display: ISO dates that sort correctly,
and bare numbers with no $ , or % glyphs so a spreadsheet or pandas reads them
as numbers. The three venture funds (ARKSX/ARKUX/ARKVX) publish only a weight,
so shares_held and market_value are empty for their rows.

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

Usage: build_history.py [--repo .] [--out data/history/ark_holdings_history.csv]
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
    os.makedirs(os.path.join(d, "data", "holdings", "2026-01-05"))
    open(os.path.join(d, "data", "holdings", "2026-01-05", "ARKK_Holdings_2026-01-05.csv"),
         "w").write(etf.format(d="01/05/2026"))
    # weekend folder: verbatim copy of Friday, must collapse
    os.makedirs(os.path.join(d, "data", "holdings", "2026-01-06"))
    open(os.path.join(d, "data", "holdings", "2026-01-06", "ARKK_Holdings_2026-01-06.csv"),
         "w").write(etf.format(d="01/05/2026"))
    # venture fund: weight only, no shares/market value
    open(os.path.join(d, "data", "holdings", "2026-01-05", "ARKVX_Holdings_2026-01-05.csv"),
         "w").write("date,fund,company,ticker,cusip,weight (%)\n"
                    "01/05/2026,ARKVX,OpenAI,,,6.18%\n")

    rows = read_holdings(os.path.join(d, "data", "holdings", "2026-01-05",
                                      "ARKK_Holdings_2026-01-05.csv"), "ARKK")
    assert rows == [["2026-01-05", "ARKK", "TESLA INC", "TSLA", "88160R101",
                     "10.00", "1000", "2000.00"]], rows     # disclaimer dropped

    out = os.path.join("data", "history", "h.csv")
    sys.argv = ["build_history.py", "--repo", d, "--out", out]
    main()
    got = list(csv.reader(open(os.path.join(d, out), newline="")))
    assert got[0] == HEADER
    assert len(got) == 3, got                               # header + ARKK + ARKVX, weekend gone
    assert [r[1] for r in got[1:]] == ["ARKK", "ARKVX"]
    assert got[2][6] == "" and got[2][7] == "", got[2]      # venture: blank shares + mv
    print("selftest OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.path.join(os.path.dirname(__file__), ".."))
    ap.add_argument("--out", default=os.path.join("data", "history", "ark_holdings_history.csv"))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    holdings = os.path.join(args.repo, "data", "holdings")
    chosen = {}   # (date, fund) -> (folder_matches_that_date, rows)
    scanned = groups = 0
    for path in sorted(glob.glob(os.path.join(holdings, "*", "*.csv"))):
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

    all_rows = []
    for _, rws in chosen.values():
        all_rows.extend(rws)
    # oldest day first; within a day, fund then largest position first
    all_rows.sort(key=lambda r: (r[0], r[1], -(float(r[5]) if r[5] else 0.0)))

    out_path = os.path.join(args.repo, args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(all_rows)

    dates = sorted({r[0] for r in all_rows})
    funds = sorted({r[1] for r in all_rows})
    print(f"scanned {scanned} files -> {groups} (date,fund) groups -> kept {len(chosen)} "
          f"({groups - len(chosen)} duplicate copies dropped)")
    print(f"wrote {out_path}")
    print(f"  {len(all_rows):,} rows | {len(dates)} dates {dates[0]}..{dates[-1]} | {len(funds)} funds")
    print(f"  {', '.join(funds)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
