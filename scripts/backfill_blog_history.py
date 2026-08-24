#!/usr/bin/env python3
"""One-time backfill: blog.arkinvesttrades.com CSV archive -> official holdings layout.

The daily GitHub Action only started on 2026-04-29, so data/holdings has no
history before that. The blog archive covers 8 equity ETFs back to 2021-05-06.
Numbers are identical to ARK's official files (verified on the overlap window,
see --verify); only the serialization differs:

    blog     #,Date,Company,Ticker,CUSIP,Weight,Shares Held,Market Value
    official date,fund,company,ticker,cusip,shares,market value ($),weight (%)

Transformations: drop the "#" column, add "fund", reorder, strip the
"(delta) (pct)" suffix the blog appends to Shares Held, and fold en/em dashes in
company names back to ASCII hyphens.

Usage:
    backfill_blog_history.py --src <blog-data-dir> [--verify] [--apply]

--verify re-derives the dates that ALSO exist officially and diffs them; that is
the correctness test for this converter. --apply writes the missing dates only
(never overwrites an official file).
"""
import argparse
import csv
import os
import re
import sys
from collections import defaultdict

OFFICIAL_HEADER = ["date", "fund", "company", "ticker", "cusip",
                   "shares", "market value ($)", "weight (%)"]

# Blog appends per-day share deltas: "1,670,380 (0) (0%)" -> "1,670,380"
SHARES_SUFFIX = re.compile(r"\s*\([^)]*\)")


def clean_company(s):
    return s.replace("–", "-").replace("—", "-").strip()


def clean_shares(s):
    return SHARES_SUFFIX.sub("", s).strip()


def read_blog(path):
    """-> (fund_from_caller, [official-shaped rows]). Tolerates the 16 files
    that ship without the leading '#' column."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    off = 1 if header and header[0] == "#" else 0
    out = []
    for r in rows[1:]:
        if len(r) < off + 7:
            continue  # short/blank/footer line
        date, company, ticker, cusip, weight, shares, mv = r[off:off + 7]
        if not date.strip():
            continue
        out.append([date.strip(), clean_company(company), ticker.strip(),
                    cusip.strip(), weight.strip(), clean_shares(shares), mv.strip()])
    return out


def to_official(fund, rows):
    """Blog column order -> official column order."""
    return [[date, fund, company, ticker, cusip, shares, mv, weight]
            for (date, company, ticker, cusip, weight, shares, mv) in rows]


def write_csv(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        w.writerow(OFFICIAL_HEADER)
        w.writerows(rows)


def load_official(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    # trailing disclaimer row has <3 populated cells
    return [r for r in rows[1:] if len([c for c in r if c.strip()]) >= 3]


def internal_date(rows):
    """ISO date carried in the file's own first data row, or None."""
    if not rows:
        return None
    try:
        m, d, y = rows[0][0].strip().split("/")
    except ValueError:
        return None
    return f"{y}-{m}-{d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="blog archive data/ dir")
    ap.add_argument("--repo", default=os.path.join(os.path.dirname(__file__), ".."))
    ap.add_argument("--verify", action="store_true",
                    help="diff re-derived rows against official files on the overlap window")
    ap.add_argument("--apply", action="store_true", help="write missing dates")
    ap.add_argument("--repair-stale", action="store_true",
                    help="overwrite official files frozen at a wrong internal date")
    args = ap.parse_args()

    holdings = os.path.join(args.repo, "data", "holdings")

    def day_dir(date):
        return os.path.join(holdings, date[:4], date)

    existing = {d for y in os.listdir(holdings) if re.fullmatch(r"\d{4}", y)
                for d in os.listdir(os.path.join(holdings, y))
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)}

    # group blog files by date
    by_date = defaultdict(dict)
    for name in sorted(os.listdir(args.src)):
        m = re.fullmatch(r"([a-z]+)-(\d{4}-\d{2}-\d{2})\.csv", name)
        if not m:
            continue
        fund, date = m.group(1).upper(), m.group(2)
        by_date[date][fund] = os.path.join(args.src, name)

    overlap = sorted(set(by_date) & existing)
    missing = sorted(set(by_date) - existing)
    print(f"blog dates: {len(by_date)}  |  already official: {len(overlap)}  |  to backfill: {len(missing)}")

    # An official file is STALE when it carries an internal date other than its
    # own folder date while the blog's file for that folder date is self-consistent.
    # That is the ARKF/ARKX rename-freeze class (ARK served a frozen 2026-01-02
    # file under the old filename for months). Weekend folders are stale by
    # design but the blog has no weekend files, so they never enter this set;
    # venture funds report monthly and are absent from the blog entirely.
    stale = []
    for date in overlap:
        for fund, path in sorted(by_date[date].items()):
            off_path = os.path.join(day_dir(date), f"{fund}_Holdings_{date}.csv")
            if not os.path.exists(off_path):
                continue
            if internal_date(load_official(off_path)) != date and \
               internal_date(to_official(fund, read_blog(path))) == date:
                stale.append((date, fund, off_path, path))
    if stale:
        funds = sorted({f for _, f, _, _ in stale})
        print(f"stale official files repairable from blog: {len(stale)} ({', '.join(funds)}) "
              f"{stale[0][0]}..{stale[-1][0]}")

    if args.verify:
        stale_paths = {p for _, _, p, _ in stale}
        checked = mismatched = 0
        diffs = []
        for date in overlap:
            for fund, path in sorted(by_date[date].items()):
                off_path = os.path.join(day_dir(date), f"{fund}_Holdings_{date}.csv")
                if not os.path.exists(off_path) or off_path in stale_paths:
                    continue  # ARK's own bug, not a converter defect
                mine = to_official(fund, read_blog(path))
                theirs = load_official(off_path)
                checked += 1
                if mine != theirs:
                    mismatched += 1
                    if len(diffs) < 5:
                        d = next((f"  row {i}:\n    blog={a}\n    ark ={b}"
                                  for i, (a, b) in enumerate(zip(mine, theirs)) if a != b),
                                 f"  length {len(mine)} vs {len(theirs)}")
                        diffs.append(f"{date} {fund}\n{d}")
        print(f"\nVERIFY: {checked} healthy official files compared, {mismatched} mismatched")
        for d in diffs:
            print(d)
        if mismatched:
            return 1
        print("converter reproduces ARK's official files byte-for-byte on the overlap window")

    if args.apply:
        written = 0
        for date in missing:
            for fund, path in sorted(by_date[date].items()):
                rows = to_official(fund, read_blog(path))
                if not rows:
                    continue
                write_csv(os.path.join(day_dir(date), f"{fund}_Holdings_{date}.csv"), rows)
                written += 1
        print(f"\nAPPLY: wrote {written} files across {len(missing)} dates")

    if args.repair_stale:
        for date, fund, off_path, blog_path in stale:
            write_csv(off_path, to_official(fund, read_blog(blog_path)))
        print(f"REPAIR: rewrote {len(stale)} stale official files from blog data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
