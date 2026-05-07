#!/usr/bin/env python3
"""Render a level-2 speakers JSON into a styled transcript PDF.

Usage:
    python render_transcript.py SPEAKERS_JSON METADATA_JSON OUT_PDF

SPEAKERS_JSON: {speaker_name_map_v2: {sid: {name, title}}, speakers: [{speaker, text}]}
METADATA_JSON: {ticker, company, year, quarter, conference_date}
OUT_PDF: path to write PDF
"""
import json
import sys
from pathlib import Path

from weasyprint import HTML, CSS

CSS_TEXT = """
@page {
  size: Letter; margin: 0.6in 0.65in 0.75in 0.65in;
  @bottom-left { content: "ARK INVEST EARNINGS TRANSCRIPT"; font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; font-size: 7.5pt; letter-spacing: 0.12em; color: #888; }
  @bottom-center { content: var(--footer); font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; font-size: 7.5pt; letter-spacing: 0.12em; color: #888; }
  @bottom-right { content: counter(page) " / " counter(pages); font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; font-size: 7.5pt; letter-spacing: 0.12em; color: #888; }
}
* { box-sizing: border-box; }
html, body { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; font-size: 10pt; line-height: 1.55; color: #1a1a1a; margin: 0; padding: 0; }
strong { font-weight: 600; color: #0a2540; }
.masthead { display: flex; justify-content: space-between; align-items: flex-end; padding-bottom: 12pt; margin-bottom: 8pt; border-bottom: 2.2pt solid #0a2540; }
.mast-eyebrow { font-size: 7.8pt; letter-spacing: 0.22em; color: #6b7280; font-weight: 500; margin-bottom: 5pt; text-transform: uppercase; }
.mast-title { font-size: 30pt; font-weight: 700; letter-spacing: -0.025em; color: #0a2540; margin: 0; line-height: 0.95; }
.mast-right { text-align: right; padding-bottom: 3pt; }
.mast-date { font-size: 11pt; font-weight: 500; color: #0a2540; margin-bottom: 3pt; }
.mast-coverage { font-size: 8pt; color: #6b7280; letter-spacing: 0.02em; }
.cast { display: grid; grid-template-columns: repeat(auto-fill, minmax(180pt, 1fr)); gap: 6pt 14pt; padding: 10pt 0 14pt; border-bottom: 0.4pt solid #e5e7eb; margin-bottom: 14pt; }
.cast-label { grid-column: 1 / -1; font-size: 7pt; letter-spacing: 0.12em; text-transform: uppercase; font-weight: 700; color: #0a2540; }
.cast-entry { font-size: 8.5pt; line-height: 1.3; }
.cast-entry .name { font-weight: 600; color: #0a2540; }
.cast-entry .title { display: block; color: #6b7280; font-size: 7.8pt; }
.turn { margin-bottom: 11pt; }
.turn-head { display: flex; align-items: baseline; gap: 8pt; margin-bottom: 3pt; }
.turn-name { font-size: 8.5pt; letter-spacing: 0.04em; text-transform: uppercase; font-weight: 700; color: #0a2540; }
.turn-title { font-size: 7.8pt; color: #6b7280; font-weight: 500; }
.turn-body { font-size: 9.8pt; line-height: 1.6; color: #1a1a1a; text-align: justify; hyphens: auto; }
.turn-body p { margin: 0 0 5pt; }
.turn[data-role="ceo"] .turn-body { border-left: 2pt solid #0a2540; padding-left: 9pt; }
.turn[data-role="cfo"] .turn-body { border-left: 2pt solid #1f7a4c; padding-left: 9pt; }
.turn[data-role="ir"]  .turn-body { border-left: 2pt solid #d97706; padding-left: 9pt; }
.turn[data-role="operator"] .turn-body { border-left: 2pt solid #c4cdd5; padding-left: 9pt; }
.qa-marker { margin: 18pt 0 10pt; padding: 6pt 0; border-top: 0.75pt solid #0a2540; border-bottom: 0.75pt solid #0a2540; font-size: 9pt; letter-spacing: 0.12em; text-transform: uppercase; font-weight: 700; color: #0a2540; text-align: center; }
"""


def classify(title):
    t = (title or "").lower()
    if "ceo" in t or "founder" in t or "chairman" in t: return "ceo"
    if "cfo" in t or "financial" in t: return "cfo"
    if "investor relations" in t or "ir " in t: return "ir"
    if "operator" in t: return "operator"
    return "other"


def cast_key(item):
    sid, info = item
    role = classify(info.get("title") or "")
    return ({"operator": 0, "ir": 1, "ceo": 2, "cfo": 3, "other": 4}[role], info.get("name") or sid or "")


def main():
    if len(sys.argv) != 4:
        sys.exit("usage: render_transcript.py SPEAKERS_JSON METADATA_JSON OUT_PDF")
    speakers_path, meta_path, out_pdf = sys.argv[1:4]
    data = json.loads(Path(speakers_path).read_text())
    meta = json.loads(Path(meta_path).read_text())

    name_map = data["speaker_name_map_v2"]
    speakers = data["speakers"]
    ticker = meta["ticker"]
    company = meta.get("company", "")
    year = meta["year"]
    quarter = meta["quarter"]
    conf_date = (meta.get("conference_date") or "")[:10] or "—"

    cast_html = ['<div class="cast"><div class="cast-label">Cast (in order of appearance)</div>']
    for sid, info in sorted(name_map.items(), key=cast_key):
        name = info.get("name") or sid
        title = (info.get("title") or "").replace("Conference Operator", "Operator").replace("Conference Call Host", "Host")
        cast_html.append(f'<div class="cast-entry"><span class="name">{name}</span><span class="title">{title}</span></div>')
    cast_html.append('</div>')

    turns = []
    qa_marked = False
    for seg in speakers:
        sid = seg["speaker"]
        info = name_map.get(sid, {"name": sid, "title": ""})
        role = classify(info.get("title") or "")
        text = (seg.get("text") or "").strip()
        if not qa_marked and "going to come from" in text.lower() and role == "ir":
            turns.append('<div class="qa-marker">— Q&amp;A —</div>')
            qa_marked = True
        title = (info.get("title") or "").replace("Conference Operator", "Operator").replace("Conference Call Host", "Host")
        text_safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        turns.append(
            f'<div class="turn" data-role="{role}">'
            f'<div class="turn-head"><div class="turn-name">{info.get("name") or sid}</div>'
            f'<div class="turn-title">{title}</div></div>'
            f'<div class="turn-body"><p>{text_safe}</p></div></div>'
        )

    masthead = (
        f'<header class="masthead">'
        f'<div class="mast-left"><div class="mast-eyebrow">ARK Invest · Earnings Transcript</div>'
        f'<h1 class="mast-title">{ticker} · FY{year} Q{quarter}</h1></div>'
        f'<div class="mast-right"><div class="mast-eyebrow" style="margin-bottom:0">Earnings call</div>'
        f'<div class="mast-date">{conf_date}</div>'
        f'<div class="mast-coverage">{company} · {len(speakers)} turns · {len(name_map)} participants</div>'
        f'</div></header>'
    )

    footer = f"{ticker} · FY{year} Q{quarter} · {conf_date}"
    css = CSS_TEXT.replace("var(--footer)", f'"{footer}"')

    html_doc = (
        f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f'<title>{ticker} {year}Q{quarter}</title>'
        f'<style>{css}</style></head>'
        f'<body>{masthead}{"".join(cast_html)}{"".join(turns)}</body></html>'
    )

    HTML(string=html_doc).write_pdf(out_pdf)
    print(f"PDF written: {out_pdf}")


if __name__ == "__main__":
    main()
