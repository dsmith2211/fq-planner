#!/usr/bin/env python3
"""
generate_browse.py — FQ App Static Browse Interface Generator
--------------------------------------------------------------
Reads all JSON files in ./data/extracted/ and generates a single-file
static HTML browse interface at ./docs/browse.html.

Features:
  - Card grid with title, page count, char count per PDF
  - Expandable per-page text viewer
  - Inline table rendering
  - Links to original PDF files
  - Client-side search (no backend)

Usage:
    python pipeline/generate_browse.py
    python pipeline/generate_browse.py --out docs/browse.html

Requirements: Python 3.9+ stdlib only (no extra dependencies)
"""

import argparse
import json
import html as html_mod
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# ── Path config ───────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRACTED_DIR = REPO_ROOT / "data" / "extracted"
MANIFEST_PATH = EXTRACTED_DIR / "_manifest.json"
DEFAULT_OUT = REPO_ROOT / "browse.html"
# PDFs are served at /data/pdfs/ relative to repo root
PDF_BASE_URL = "data/pdfs"


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_extracted_files() -> list[dict]:
    """Load all extracted JSON files, sorted alphabetically."""
    files = sorted(f for f in EXTRACTED_DIR.glob("*.json") if f.name != "_manifest.json")
    if not files:
        print(f"No extracted JSON files found in {EXTRACTED_DIR}", file=sys.stderr)
        sys.exit(1)
    docs = []
    for f in files:
        with open(f, encoding="utf-8") as fp:
            docs.append(json.load(fp))
    return docs


def render_table(tbl: dict) -> str:
    headers = tbl.get("headers", [])
    rows = tbl.get("rows", [])
    if not headers and not rows:
        return ""
    th = "".join(f"<th>{html_mod.escape(str(h))}</th>" for h in headers)
    tr_rows = []
    for row in rows:
        cells = "".join(
            f"<td>{html_mod.escape(str(row.get(h, '')))}</td>" for h in headers
        )
        tr_rows.append(f"<tr>{cells}</tr>")
    return f"""<div class="table-wrap"><table><thead><tr>{th}</tr></thead><tbody>{''.join(tr_rows)}</tbody></table></div>"""


def render_page_content(page: dict) -> str:
    parts = []
    text = page.get("text", "").strip()
    if text:
        # Preserve paragraph breaks
        paras = re.split(r"\n\n+", text)
        for p in paras:
            p = html_mod.escape(p.replace("\n", " ")).strip()
            if p:
                parts.append(f"<p>{p}</p>")
    for tbl in page.get("tables", []):
        parts.append(render_table(tbl))
    return "\n".join(parts) if parts else "<p><em>(no text on this page)</em></p>"


def render_doc_card(doc: dict, idx: int) -> str:
    title = html_mod.escape(doc.get("title", doc["source_pdf"]))
    pdf_name = doc["source_pdf"]
    page_count = doc.get("page_count", 0)
    char_count = doc.get("char_count", 0)
    extracted_at = doc.get("extracted_at", "")[:10]
    pdf_url = f"{PDF_BASE_URL}/{pdf_name}"

    # Build per-page accordion
    page_items = []
    for page in doc.get("pages", []):
        pn = page["page"]
        content = render_page_content(page)
        has_tables = "table-icon" if page.get("tables") else ""
        page_items.append(
            f"""<details class="page-detail">
  <summary>Page {pn} {f'<span class="badge">table</span>' if has_tables else ''}</summary>
  <div class="page-body">{content}</div>
</details>"""
        )

    pages_html = "\n".join(page_items)

    return f"""<div class="doc-card" id="doc-{idx}" data-title="{title.lower()}" data-pdf="{pdf_name.lower()}">
  <div class="doc-header">
    <div class="doc-meta">
      <h2 class="doc-title">{title}</h2>
      <div class="doc-stats">
        <span class="stat">{page_count} pages</span>
        <span class="stat">{char_count:,} chars</span>
        <span class="stat">extracted {extracted_at}</span>
      </div>
    </div>
    <a class="pdf-link" href="{pdf_url}" target="_blank" rel="noopener">📄 Open PDF</a>
  </div>
  <details class="pages-toggle">
    <summary>View extracted content ({page_count} pages)</summary>
    <div class="pages-container">
      {pages_html}
    </div>
  </details>
</div>"""


def build_html(docs: list[dict]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cards = "\n\n".join(render_doc_card(doc, i) for i, doc in enumerate(docs))
    total_pages = sum(d.get("page_count", 0) for d in docs)
    total_chars = sum(d.get("char_count", 0) for d in docs)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FQ Rulebook — Browse Extracted Content</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg: #0f0f1a;
      --surface: #1a1a2e;
      --surface2: #16213e;
      --accent: #e94560;
      --gold: #c9a84c;
      --text: #e8e8f0;
      --muted: #8888aa;
      --border: #2a2a4a;
      --radius: 8px;
    }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: 'Segoe UI', system-ui, sans-serif;
      line-height: 1.6;
      min-height: 100vh;
    }}
    /* ── Header ── */
    header {{
      background: var(--surface2);
      border-bottom: 2px solid var(--gold);
      padding: 1.5rem 2rem;
      display: flex;
      align-items: center;
      gap: 1rem;
      flex-wrap: wrap;
    }}
    header h1 {{
      font-size: 1.5rem;
      color: var(--gold);
      font-weight: 700;
      letter-spacing: 0.03em;
    }}
    header .subtitle {{ color: var(--muted); font-size: 0.85rem; }}
    /* ── Toolbar ── */
    .toolbar {{
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 1rem 2rem;
      display: flex;
      gap: 1rem;
      align-items: center;
      flex-wrap: wrap;
    }}
    .toolbar input {{
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      color: var(--text);
      font-size: 0.9rem;
      padding: 0.5rem 1rem;
      width: 320px;
      outline: none;
    }}
    .toolbar input:focus {{ border-color: var(--gold); }}
    .toolbar .stats {{
      margin-left: auto;
      color: var(--muted);
      font-size: 0.82rem;
    }}
    /* ── Main ── */
    main {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 2rem;
      display: flex;
      flex-direction: column;
      gap: 1.5rem;
    }}
    /* ── Doc Card ── */
    .doc-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      overflow: hidden;
      transition: border-color 0.2s;
    }}
    .doc-card:hover {{ border-color: var(--gold); }}
    .doc-card.hidden {{ display: none; }}
    .doc-header {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 1rem;
      padding: 1.25rem 1.5rem;
      flex-wrap: wrap;
    }}
    .doc-title {{ font-size: 1.1rem; color: var(--gold); margin-bottom: 0.25rem; }}
    .doc-stats {{ display: flex; gap: 1rem; flex-wrap: wrap; }}
    .stat {{ font-size: 0.78rem; color: var(--muted); background: var(--bg); padding: 2px 8px; border-radius: 12px; border: 1px solid var(--border); }}
    .pdf-link {{
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      background: var(--accent);
      color: #fff;
      padding: 0.4rem 1rem;
      border-radius: var(--radius);
      font-size: 0.82rem;
      font-weight: 600;
      text-decoration: none;
      white-space: nowrap;
      transition: opacity 0.2s;
    }}
    .pdf-link:hover {{ opacity: 0.85; }}
    /* ── Pages Toggle ── */
    .pages-toggle {{
      border-top: 1px solid var(--border);
    }}
    .pages-toggle > summary {{
      padding: 0.75rem 1.5rem;
      cursor: pointer;
      color: var(--muted);
      font-size: 0.85rem;
      user-select: none;
      list-style: none;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}
    .pages-toggle > summary::before {{ content: '▶'; font-size: 0.7em; transition: transform 0.2s; }}
    .pages-toggle[open] > summary::before {{ transform: rotate(90deg); }}
    .pages-toggle > summary:hover {{ color: var(--text); }}
    .pages-container {{ padding: 0 1.5rem 1.5rem; }}
    /* ── Page Detail ── */
    .page-detail {{
      border: 1px solid var(--border);
      border-radius: var(--radius);
      margin-top: 0.75rem;
      background: var(--bg);
    }}
    .page-detail > summary {{
      padding: 0.5rem 1rem;
      cursor: pointer;
      font-size: 0.8rem;
      color: var(--muted);
      user-select: none;
      list-style: none;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}
    .page-detail > summary::before {{ content: '▶'; font-size: 0.65em; transition: transform 0.2s; }}
    .page-detail[open] > summary::before {{ transform: rotate(90deg); }}
    .page-detail > summary:hover {{ color: var(--text); }}
    .page-body {{ padding: 0.75rem 1rem 1rem; font-size: 0.82rem; color: #ccc; }}
    .page-body p {{ margin-bottom: 0.5rem; }}
    .badge {{
      background: var(--accent);
      color: #fff;
      font-size: 0.65rem;
      padding: 1px 6px;
      border-radius: 10px;
      font-weight: 600;
    }}
    /* ── Tables ── */
    .table-wrap {{ overflow-x: auto; margin: 0.75rem 0; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.78rem; }}
    th {{ background: var(--surface2); color: var(--gold); padding: 6px 10px; text-align: left; border: 1px solid var(--border); white-space: nowrap; }}
    td {{ padding: 5px 10px; border: 1px solid var(--border); color: var(--text); }}
    tr:nth-child(even) td {{ background: #ffffff08; }}
    /* ── Footer ── */
    footer {{
      text-align: center;
      padding: 2rem;
      color: var(--muted);
      font-size: 0.78rem;
      border-top: 1px solid var(--border);
    }}
    /* ── No results ── */
    #no-results {{ display: none; text-align: center; padding: 3rem; color: var(--muted); }}
    #no-results.visible {{ display: block; }}
  </style>
</head>
<body>

<header>
  <div>
    <h1>⚔️ Fantasy Quest — Rulebook Browser</h1>
    <div class="subtitle">Extracted content from {len(docs)} source PDF(s) · {total_pages} pages · {total_chars:,} characters</div>
  </div>
</header>

<div class="toolbar">
  <input type="search" id="search-input" placeholder="Search document titles or filenames…" autocomplete="off">
  <div class="stats" id="result-count">{len(docs)} document(s)</div>
</div>

<main id="doc-list">
{cards}
  <div id="no-results">No documents match your search.</div>
</main>

<footer>
  Generated: {now} &nbsp;·&nbsp;
  Source: <a href="https://www.fantasyquestlarp.com/rulebook" target="_blank" style="color:var(--gold)">fantasyquestlarp.com/rulebook</a>
  &nbsp;·&nbsp; Fantasy Quest Rule Book Edition 6.1 — 2018
</footer>

<script>
  const searchInput = document.getElementById('search-input');
  const cards = Array.from(document.querySelectorAll('.doc-card'));
  const noResults = document.getElementById('no-results');
  const resultCount = document.getElementById('result-count');

  searchInput.addEventListener('input', () => {{
    const q = searchInput.value.toLowerCase().trim();
    let visible = 0;
    cards.forEach(card => {{
      const title = card.dataset.title || '';
      const pdf = card.dataset.pdf || '';
      const match = !q || title.includes(q) || pdf.includes(q);
      card.classList.toggle('hidden', !match);
      if (match) visible++;
    }});
    noResults.classList.toggle('visible', visible === 0);
    resultCount.textContent = visible + ' document' + (visible !== 1 ? 's' : '');
  }});
</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate static browse interface from extracted JSON")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output HTML file path")
    args = parser.parse_args()

    out_path = Path(args.out)

    print("FQ Browse Generator")
    print(f"Extracted dir : {EXTRACTED_DIR.relative_to(REPO_ROOT)}")
    print(f"Output        : {out_path.relative_to(REPO_ROOT)}")

    docs = load_extracted_files()
    print(f"Documents     : {len(docs)}")

    html = build_html(docs)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\nWritten → {out_path.relative_to(REPO_ROOT)}")
    print(f"Size: {out_path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
