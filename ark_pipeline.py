#!/usr/bin/env python3
"""Self-contained ARK Twitter pipeline — fetch tweets + render PDF."""
import csv, io, json, os, re, sys, threading, time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone, timedelta
from html import escape as _esc
from pathlib import Path

import requests

OUT_ROOT = Path("/tmp/ark_run")
TWITTERAPI_KEY = "new1_be2be03eb43c4dfda2d02ac3bd1bfd2d"
BASE_URL = "https://api.twitterapi.io"

# CSVs are pre-downloaded by the agent (via Google-Drive MCP) into
# /tmp/ark_run/<DATE>/csvs/<ETF>_Holdings_<DATE>.csv. We only need
# to know the ETF names to look for and to recognize them as ETF
# tickers (so we don't include them as company holdings).
ETF_TICKERS = {
    "ARKK","ARKG","ARKQ","ARKW","ARKB","ARKVX","ARKX","ARKF",
    "IZRL","PRNT","ARKD","ARKT","ARKUX","ARKSX",
}

NON_COMPANY_PATTERNS = [
    re.compile(r"\b\d+/\d+/\d+\b"),
    re.compile(r"\bHOLDCO\b", re.I),
    re.compile(r"\bMMKT\b", re.I),
    re.compile(r"\bMONEY MARKET\b", re.I),
    re.compile(r"\bTRSY OBLIG", re.I),
    re.compile(r"\bTREASURY OBLIG", re.I),
    re.compile(r"\bGOVT (CASH|MMKT)", re.I),
    re.compile(r"\bCASH MGMT", re.I),
    re.compile(r"\bSWEEP\b", re.I),
    re.compile(r"\bLIQUIDITY (FD|FUND)", re.I),
    re.compile(r"\bFEDFUND\b", re.I),
]

COMMON_WORD_NAMES = {
    "caterpillar","hp","nova","firmly","allot","lambda","bullish",
    "discord","block","toast","illumina","alphabet","3m",
    "match","snap","zoom","square",
}

SUFFIX_RE = re.compile(
    r"(?:\s+INCORPORATED|\s+CORPORATION|\s+COMPANY|\s+LIMITED|\s+HOLDINGS|"
    r"\s+GROUP|\s+TRUST|\s+ETF|\s+INC\.?|\s+CORP\.?|\s+LTD\.?|\s+CO\.?|"
    r"\s+AG|\s+PLC|\s+SA|\s+NV|\s+LLC)\.?\s*$", re.I,
)
CLASS_SUFFIX_RE = re.compile(r"[\s,]*-\s*(?:CL\.?|CLASS)\s*[A-Z]\s*$", re.I)
TRAILING_LETTER_RE = re.compile(r"\s*-\s*[A-Z]\s*$")
BLOOMBERG_RE = re.compile(r"^([A-Z]+)\s+[A-Z]{2}$")
CORP_SUFFIX_RE = re.compile(
    r"\b(inc|corp|corporation|incorporated|ltd|limited|company|holdings|"
    r"trust|co|ag|plc|sa|llc|nv)\b\.?\s*$", re.I,
)

PROMO_KEYWORDS = [
    "follow @","follow me","dm me","guaranteed","$1000/day","/day",
    "always go up","never down","make a fortune","get rich",
]
CASHTAG_RE = re.compile(r"\$[A-Z]+")
HASHTAG_RE = re.compile(r"#\w+")
TWITTER_DATE_FMT = "%a %b %d %H:%M:%S %z %Y"

def slug_priv_ticker(company):
    s = re.sub(r"[^A-Za-z0-9]+", "_", (company or "").upper()).strip("_")
    return s or "UNKNOWN_PRIVATE"

def find_weight_key(keys):
    for k in keys:
        if not k: continue
        n = k.replace("(","").replace(")","").replace("%","").replace(" ","").lower()
        if "weight" in n: return k
    return None

def parse_csv_text(text, etf):
    rows = []
    rdr = csv.DictReader(io.StringIO(text))
    keys = list(rdr.fieldnames or [])
    wk = find_weight_key(keys)
    for r in rdr:
        norm = {(k or "").strip().lower():(v or "").strip() for k,v in r.items()}
        raw = (r.get(wk) or "").strip() if wk else ""
        if not raw:
            raw = norm.get("weight (%)") or norm.get("weight") or ""
        cleaned = raw.replace("%","").strip()
        try: w = float(cleaned) if cleaned else 0.0
        except ValueError: w = 0.0
        if w == 0.0: continue
        ticker = norm.get("ticker") or norm.get("symbol") or ""
        company = norm.get("company") or norm.get("name") or norm.get("company name") or ""
        if not ticker and not company: continue
        if not ticker and company:
            ticker = slug_priv_ticker(company)
        rows.append({
            "etf": etf, "ticker": ticker, "company": company,
            "weight": w, "is_private": not (norm.get("ticker") or norm.get("symbol")),
        })
    return rows

def fetch_holdings(target_date):
    """Read pre-downloaded CSVs from /tmp/ark_run/<DATE>/csvs/.
    The agent downloads these via Google-Drive MCP before running fetch.
    Filename pattern: {ETF}_Holdings_{DATE}.csv (e.g. ARKK_Holdings_2026-04-29.csv).
    Tolerates missing files (some ETFs might not be published on a given day)."""
    csv_dir = OUT_ROOT / target_date / "csvs"
    if not csv_dir.exists():
        sys.exit(f"[err] CSV directory missing: {csv_dir}\n"
                 f"      Agent must download CSVs via Google-Drive MCP first.")
    rows = []
    found_etfs = []
    missing_etfs = []
    for etf in ETF_TICKERS:
        # accept either {ETF}_Holdings_{date}.csv (preferred) or any file
        # starting with {ETF}_Holdings_ (fallback)
        candidates = sorted(csv_dir.glob(f"{etf}_Holdings_*.csv"))
        if not candidates:
            missing_etfs.append(etf)
            continue
        # prefer one matching target_date exactly; else use the latest by name
        exact = csv_dir / f"{etf}_Holdings_{target_date}.csv"
        path = exact if exact.exists() else candidates[-1]
        try:
            text = path.read_text(encoding="utf-8")
            rows.extend(parse_csv_text(text, etf))
            found_etfs.append(etf)
        except Exception as e:
            print(f"[warn] failed to read {path.name}: {e}", file=sys.stderr)
            missing_etfs.append(etf)
    print(f"[ark] holdings: read {len(found_etfs)}/{len(ETF_TICKERS)} ETFs from {csv_dir}")
    if missing_etfs:
        print(f"[ark] missing: {','.join(sorted(missing_etfs))}")
    return rows

def is_non_company(company):
    return any(p.search(company or "") for p in NON_COMPANY_PATTERNS)

def unique_companies(rows):
    by_t = {}
    for r in rows:
        t = r["ticker"]
        if t in ETF_TICKERS: continue
        if is_non_company(r["company"]): continue
        if t not in by_t:
            by_t[t] = {"ticker":t,"company":r["company"],"is_private":r["is_private"],
                       "weight_sum":r["weight"],"etfs":[r["etf"]]}
        else:
            by_t[t]["weight_sum"] += r["weight"]
            by_t[t]["etfs"].append(r["etf"])
    return list(by_t.values())

def clean_company_name(name):
    s = (name or "").strip().rstrip(".,")
    if not s: return ""
    prev = None
    while s != prev:
        prev = s
        s = SUFFIX_RE.sub("", s).rstrip(" ,.-")
        m = re.match(r"^(.+?)[\s,]*-\s*(?:CL\.?|CLASS)?\s*[A-Z]\s*$", s, re.I)
        if m: s = m.group(1).strip(" ,.-")
    if not s: return ""
    if s.isupper():
        s = " ".join(w[:1].upper()+w[1:].lower() for w in s.split())
    return s

def disambiguating_name(company):
    s = (company or "").strip().rstrip(".")
    if not s: return ""
    prev = None
    while s != prev:
        prev = s
        s = CLASS_SUFFIX_RE.sub("", s).strip(" ,.-")
        s = TRAILING_LETTER_RE.sub("", s).strip(" ,.-")
    if s.isupper():
        s = " ".join(w[:1].upper()+w[1:].lower() for w in s.split())
    return s

def has_corp_suffix(name):
    return bool(CORP_SUFFIX_RE.search(name or ""))

def cashtag_ticker(t):
    if not t: return None
    m = BLOOMBERG_RE.match(t)
    if m: return m.group(1)
    if "_" in t or not t.isalpha(): return None
    return t

def build_query(ticker, company, is_private=False, since=None, until=None, min_faves=10):
    cleaned = clean_company_name(company)
    ambig = bool(cleaned) and cleaned.lower() in COMMON_WORD_NAMES
    keyword = None
    if cleaned and not ambig:
        keyword = cleaned
    elif ambig:
        full = disambiguating_name(company)
        if has_corp_suffix(full):
            keyword = full
    cashtag = None if is_private else cashtag_ticker(ticker)
    sub = []
    if cashtag: sub.append(f"${cashtag}")
    if keyword and (not cashtag or keyword.upper() != cashtag):
        sub.append(f'"{keyword}"')
    if not sub: return None
    base = sub[0] if len(sub)==1 else f"({' OR '.join(sub)})"
    parts = [base, "-is:retweet", f"min_faves:{min_faves}"]
    if since: parts.append(f"since:{since}")
    if until: parts.append(f"until:{until}")
    return " ".join(parts)

def advanced_search(query, query_type="Top", cursor="", retries=4):
    headers = {"x-api-key": TWITTERAPI_KEY}
    params = {"query": query, "queryType": query_type}
    if cursor: params["cursor"] = cursor
    delay = 2.0
    for a in range(retries):
        r = requests.get(f"{BASE_URL}/twitter/tweet/advanced_search",
                         headers=headers, params=params, timeout=30)
        if r.status_code == 429:
            if a == retries-1: r.raise_for_status()
            time.sleep(delay); delay *= 2; continue
        r.raise_for_status()
        return r.json()
    return {}

def paginated_search(query, max_pages=3, min_likes_continue=50, pace=1.0):
    all_t = []; cursor = ""
    for _ in range(max_pages):
        r = advanced_search(query, "Top", cursor)
        page = r.get("tweets", [])
        all_t.extend(page)
        if not page or not r.get("has_next_page"): break
        last_likes = page[-1].get("likeCount") or 0
        if last_likes < min_likes_continue: break
        cursor = r.get("next_cursor","")
        if not cursor: break
        time.sleep(pace)
    return all_t

def account_age_days(created_at):
    if not created_at: return None
    try: c = datetime.strptime(created_at, TWITTER_DATE_FMT)
    except ValueError: return None
    return (datetime.now(timezone.utc) - c).total_seconds()/86400

def is_spam(tweet):
    text = tweet.get("text") or ""
    tl = text.lower()
    a = tweet.get("author") or {}
    nc = len(CASHTAG_RE.findall(text))
    if nc > 3: return True, f"cashtags={nc}"
    nh = len(HASHTAG_RE.findall(text))
    if nh > 5: return True, f"hashtags={nh}"
    for kw in PROMO_KEYWORDS:
        if kw in tl: return True, f"promo='{kw}'"
    if a.get("isAutomated"): return True, "self_declared_bot"
    fol = a.get("followers")
    if fol is None or fol < 50: return True, f"followers={fol}"
    age = account_age_days(a.get("createdAt"))
    if age is not None and age < 30: return True, f"account_age={age:.0f}d"
    fol2 = fol or 0; flw = a.get("following") or 0
    if fol2 > 0 and flw/fol2 > 10: return True, f"follow_ratio={flw}/{fol2}"
    return False, ""

def cmd_fetch(target_date):
    out_dir = OUT_ROOT / target_date
    out_dir.mkdir(parents=True, exist_ok=True)
    if not TWITTERAPI_KEY:
        sys.exit("TWITTERAPI_IO_KEY env var missing")
    target = date.fromisoformat(target_date)
    since = (target - timedelta(days=1)).isoformat()
    until = target_date

    print(f"[ark] target={target_date} since={since} until={until}")
    rows = fetch_holdings(target_date)
    uniq = unique_companies(rows)
    uniq.sort(key=lambda r: -r["weight_sum"])
    print(f"[ark] {len(uniq)} unique companies")

    counts = {"done":0,"skip":0,"err":0,"kept":0,"drop":0}
    lock = threading.Lock()

    def proc(row):
        ticker = row["ticker"]
        safe = ticker.replace("/","_").replace("\\","_")
        out = out_dir / f"{safe}.json"
        if out.exists():
            with lock: counts["skip"] += 1
            return
        q = build_query(ticker, row["company"], is_private=row["is_private"],
                        since=since, until=until)
        if q is None:
            stub = {"ticker":ticker,"company":row["company"],"weight_sum":row["weight_sum"],
                    "etfs":row["etfs"],"is_private":row["is_private"],"query":None,
                    "skipped_reason":"unsearchable","n_fetched":0,"n_kept":0,"n_dropped":0,
                    "kept":[],"dropped":[]}
            out.write_text(json.dumps(stub, default=str))
            with lock: counts["done"] += 1
            return
        try:
            tweets = paginated_search(q)
        except Exception as e:
            with lock: counts["err"] += 1
            print(f"[err] {ticker}: {str(e)[:80]}")
            return
        tweets.sort(key=lambda t: -(t.get("likeCount") or 0))
        kept, dropped = [], []
        for t in tweets:
            sp, why = is_spam(t)
            if sp: dropped.append({"reason":why,"tweet":t})
            else: kept.append(t)
        rec = {"ticker":ticker,"company":row["company"],"weight_sum":row["weight_sum"],
               "etfs":row["etfs"],"is_private":row["is_private"],"query":q,
               "n_fetched":len(tweets),"n_kept":len(kept),"n_dropped":len(dropped),
               "kept":kept,"dropped":dropped}
        out.write_text(json.dumps(rec, default=str))
        with lock:
            counts["done"] += 1
            counts["kept"] += len(kept)
            counts["drop"] += len(dropped)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = [ex.submit(proc, r) for r in uniq]
        for i, _ in enumerate(as_completed(futs), 1):
            if i % 10 == 0 or i == len(uniq):
                el = time.time() - t0
                print(f"[{i}/{len(uniq)}] kept={counts['kept']} drop={counts['drop']} {el:.0f}s", flush=True)

    files = [p for p in out_dir.glob("*.json") if p.name != "_summary.json" and p.name != "digest.json"]
    silent = sum(1 for p in files if json.loads(p.read_text()).get("n_kept",0) == 0)
    saturated = sum(1 for p in files if json.loads(p.read_text()).get("n_fetched",0) >= 60)
    summary = {"date":target_date,"total_holdings":len(uniq),
               "total_tweets_kept":counts["kept"],"total_tweets_dropped":counts["drop"],
               "silent_holdings":silent,"saturated_holdings":saturated,
               "errors":counts["err"]}
    (out_dir / "_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"[ark] DONE {json.dumps(summary)}")

CSS = r"""
@page { size: Letter; margin: 0.55in 0.6in 0.7in 0.6in;
  @bottom-left { content:"ARK INVEST — DAILY BRIEFING"; font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;font-size:7.5pt;letter-spacing:0.12em;color:#888;}
  @bottom-center { content:"__DATE__"; font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;font-size:7.5pt;letter-spacing:0.12em;color:#888;}
  @bottom-right { content: counter(page) " / " counter(pages); font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;font-size:7.5pt;letter-spacing:0.12em;color:#888;}
}
* { box-sizing: border-box; }
html, body { font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;font-size:9.5pt;line-height:1.42;color:#111;margin:0;padding:0;}
.masthead { display:flex;justify-content:space-between;align-items:flex-end;padding-bottom:10pt;margin-bottom:6pt;border-bottom:2.2pt solid #0a2540;page-break-after:avoid;}
.mast-eyebrow { font-size:7.8pt;letter-spacing:0.22em;color:#6b7280;font-weight:500;margin-bottom:4pt;text-transform:uppercase;}
.mast-title { font-size:34pt;font-weight:700;letter-spacing:-0.025em;color:#0a2540;margin:0;line-height:0.95;}
.mast-right { text-align:right;padding-bottom:3pt;}
.mast-date-small { font-size:7.5pt;letter-spacing:0.22em;color:#6b7280;font-weight:500;margin-bottom:3pt;}
.mast-date { font-size:11pt;font-weight:500;color:#0a2540;margin-bottom:3pt;}
.mast-coverage { font-size:8pt;color:#6b7280;}
.highlights-section { padding:8pt 10pt 9pt;margin:6pt 0 10pt;background:#f3f5f8;border-left:2.5pt solid #0a2540;page-break-inside:avoid;}
.highlights-label { font-size:7.8pt;letter-spacing:0.22em;text-transform:uppercase;color:#0a2540;font-weight:700;margin-bottom:5pt;}
.highlights-list { list-style:decimal;padding-left:14pt;margin:0;font-size:9.5pt;line-height:1.4;color:#111;}
.highlights-list > li { margin:2pt 0;padding-left:2pt;}
.highlights-list > li::marker { color:#6b7280;font-weight:600;font-size:8.5pt;}
.theme-section { margin:9pt 0 5pt;page-break-inside:avoid;}
.section-header { display:flex;align-items:flex-end;margin-top:8pt;margin-bottom:5pt;border-top:0.75pt solid #0a2540;padding-top:5pt;page-break-after:avoid;}
.section-title { font-size:16pt;font-weight:700;letter-spacing:-0.02em;color:#0a2540;margin:0;line-height:1.05;}
.theme-summary-bullets { list-style:none;padding-left:0;margin:2pt 0 5pt;font-size:8.5pt;color:#222;line-height:1.35;}
.theme-summary-bullets > li { position:relative;padding-left:11pt;margin:1.5pt 0;font-style:italic;}
.theme-summary-bullets > li:before { content:"";position:absolute;left:2pt;top:0.55em;width:5pt;height:0.6pt;background:#0a2540;}
.ticker-block { margin:5pt 0 6pt;page-break-inside:avoid;}
.ticker-block-header { display:flex;align-items:baseline;gap:6pt;padding:2pt 0 3pt;margin-bottom:3pt;border-bottom:0.4pt solid #0a2540;}
.ticker-block-id { font-size:10pt;font-weight:700;color:#0a2540;letter-spacing:0.04em;}
.ticker-block-name { font-size:8.5pt;color:#374151;flex:1;}
.ticker-block-chips { font-size:7pt;color:#6b7280;}
.ticker-block-chips .hc { display:inline-block;padding:0.5pt 4pt;margin-left:2pt;background:#eef2f6;color:#0a2540;border-radius:2pt;font-weight:500;}
.bullets { list-style:none;padding-left:0;margin:1pt 0 0;font-size:8.5pt;line-height:1.38;}
.bullets > li { position:relative;padding-left:11pt;margin:1.2pt 0;color:#111;page-break-inside:avoid;}
.bullets > li:before { content:"";position:absolute;left:2pt;top:0.55em;width:5pt;height:0.65pt;background:#0a2540;}
"""

def render_html(target_date, digest):
    cov = digest.get("coverage") or {}
    css = CSS.replace("__DATE__", target_date)
    parts = [f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>ARK Daily · {_esc(target_date)}</title><style>{css}</style></head><body>']
    parts.append(
        '<header class="masthead"><div class="mast-left">'
        '<div class="mast-eyebrow">ARK Invest · Social Pulse</div>'
        '<h1 class="mast-title">Today on X</h1></div>'
        '<div class="mast-right">'
        '<div class="mast-date-small">DAILY DIGEST</div>'
        f'<div class="mast-date">{_esc(target_date)}</div>'
        f'<div class="mast-coverage">{_esc(cov.get("total_holdings","?"))} holdings · '
        f'{_esc(cov.get("total_tweets_kept","?"))} tweets · '
        f'{_esc(cov.get("silent_holdings","?"))} silent</div>'
        '</div></header>'
    )
    hl = digest.get("highlights") or []
    if hl:
        parts.append('<section class="highlights-section">')
        parts.append('<div class="highlights-label">Today\'s Highlights</div>')
        parts.append('<ol class="highlights-list">')
        for b in hl: parts.append(f'<li>{_esc(b)}</li>')
        parts.append('</ol></section>')
    for t in digest.get("themes") or []:
        parts.append('<section class="theme-section">')
        parts.append(f'<div class="section-header"><h2 class="section-title">{_esc(t.get("name","?"))}</h2></div>')
        sm = t.get("summary")
        if isinstance(sm, list) and sm:
            parts.append('<ul class="theme-summary-bullets">')
            for b in sm: parts.append(f'<li>{_esc(b)}</li>')
            parts.append('</ul>')
        for d in t.get("discussion") or []:
            priv = ' (priv)' if d.get("is_private") else ''
            parts.append('<div class="ticker-block">')
            parts.append(
                '<header class="ticker-block-header">'
                f'<div class="ticker-block-id">{_esc(d.get("ticker","?"))}</div>'
                f'<div class="ticker-block-name">{_esc(d.get("company",""))}{priv}</div>'
                '<div class="ticker-block-chips">'
                f'<span class="hc">{_esc(d.get("n_tweets","?"))} tweets</span>'
                f'<span class="hc">{d.get("weight",0):.2f}% wt</span>'
                '</div></header>'
            )
            bs = d.get("bullets") or []
            if bs:
                parts.append('<ul class="bullets">')
                for b in bs: parts.append(f'<li>{_esc(b)}</li>')
                parts.append('</ul>')
            parts.append('</div>')
        parts.append('</section>')
    parts.append('</body></html>')
    return "".join(parts)

def cmd_render(target_date):
    from weasyprint import HTML
    out_dir = OUT_ROOT / target_date
    digest_path = out_dir / "digest.json"
    if not digest_path.exists():
        sys.exit(f"missing {digest_path}")
    digest = json.loads(digest_path.read_text())
    html_str = render_html(target_date, digest)
    html_out = out_dir / "digest.html"
    html_out.write_text(html_str, encoding="utf-8")
    pdf_out = out_dir / f"Daily Tweets {target_date}.pdf"
    doc = HTML(string=html_str).render()
    n_pages = len(doc.pages)
    doc.write_pdf(target=str(pdf_out))
    print(f"[render] pages={n_pages} pdf={pdf_out}")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    td = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()
    if cmd == "fetch": cmd_fetch(td)
    elif cmd == "render": cmd_render(td)
    else: sys.exit(f"unknown command: {cmd}")
