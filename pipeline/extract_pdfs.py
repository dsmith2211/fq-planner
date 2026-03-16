#!/usr/bin/env python3
"""
extract_pdfs.py — FQ App PDF Extraction Pipeline
-------------------------------------------------
Reads every PDF in ./data/pdfs/, extracts text and tables into structured JSON,
writes output to ./data/extracted/{stem}.json, and updates a manifest.

Usage:
    python pipeline/extract_pdfs.py
    python pipeline/extract_pdfs.py --pdf data/pdfs/rulebook_main.pdf
    python pipeline/extract_pdfs.py --dry-run

Requirements:
    pip install pymupdf
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# ── Path config ──────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = REPO_ROOT / "data" / "pdfs"
OUT_DIR = REPO_ROOT / "data" / "extracted"
MANIFEST_PATH = OUT_DIR / "_manifest.json"


# ── Helpers ───────────────────────────────────────────────────────────────────

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def clean_text(raw: str) -> str:
    """Normalize whitespace; strip page-header noise."""
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    return raw.strip()


def extract_tables_from_page(page) -> list[dict]:
    """
    Use PyMuPDF's find_tables() when available (≥1.23).
    Falls back to empty list on older versions.
    """
    tables = []
    try:
        result = page.find_tables()
        for tbl in result.tables:
            rows = tbl.extract()
            if not rows:
                continue
            # First row = headers if it looks like text rather than numbers
            headers = [str(c).strip() if c else "" for c in rows[0]]
            data_rows = []
            for row in rows[1:]:
                cells = [str(c).strip() if c else "" for c in row]
                data_rows.append(dict(zip(headers, cells)))
            tables.append({
                "bbox": list(tbl.bbox),
                "headers": headers,
                "rows": data_rows,
            })
    except AttributeError:
        pass  # find_tables not available in this pymupdf version
    return tables


def extract_pdf(pdf_path: Path) -> dict:
    """
    Extract full text + tables from a PDF.
    Returns a structured dict ready for JSON serialization.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("ERROR: pymupdf not installed. Run: pip install pymupdf", file=sys.stderr)
        sys.exit(1)

    doc = fitz.open(str(pdf_path))
    pages = []
    full_text_parts = []

    for page_num, page in enumerate(doc, start=1):
        raw = page.get_text("text")
        text = clean_text(raw)
        tables = extract_tables_from_page(page)

        page_data = {
            "page": page_num,
            "text": text,
        }
        if tables:
            page_data["tables"] = tables

        pages.append(page_data)
        if text:
            full_text_parts.append(text)

    doc.close()

    full_text = "\n\n".join(full_text_parts)

    return {
        "source_pdf": pdf_path.name,
        "source_pdf_path": f"data/pdfs/{pdf_path.name}",
        "title": derive_title(full_text, pdf_path.stem),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "page_count": len(pages),
        "char_count": len(full_text),
        "sha256": sha256_file(pdf_path),
        "pages": pages,
        "full_text": full_text,
    }


def derive_title(text: str, stem: str) -> str:
    """
    Try to extract a title from the first non-empty line of the document.
    Fall back to the filename stem.
    """
    for line in text.splitlines():
        line = line.strip()
        if len(line) > 3 and not line.startswith(("©", "http")):
            return line[:120]
    return stem.replace("_", " ").title()


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        with open(MANIFEST_PATH) as f:
            return json.load(f)
    return {"files": {}, "last_updated": None}


def save_manifest(manifest: dict):
    manifest["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"  Manifest → {MANIFEST_PATH.relative_to(REPO_ROOT)}")


# ── Main ──────────────────────────────────────────────────────────────────────

def process_pdf(pdf_path: Path, out_dir: Path, dry_run: bool = False) -> dict | None:
    stem = pdf_path.stem
    out_path = out_dir / f"{stem}.json"

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Processing: {pdf_path.name}")

    # Skip if unchanged (check sha256 against manifest)
    manifest = load_manifest()
    current_sha = sha256_file(pdf_path)
    if stem in manifest["files"]:
        if manifest["files"][stem].get("sha256") == current_sha:
            print(f"  Skipped (unchanged) — {out_path.relative_to(REPO_ROOT)}")
            return None

    data = extract_pdf(pdf_path)
    print(f"  Pages: {data['page_count']} | Chars: {data['char_count']:,} | Tables: {sum(len(p.get('tables', [])) for p in data['pages'])}")

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  Written → {out_path.relative_to(REPO_ROOT)}")

    return {
        "sha256": data["sha256"],
        "title": data["title"],
        "page_count": data["page_count"],
        "char_count": data["char_count"],
        "extracted_at": data["extracted_at"],
        "json_path": f"data/extracted/{stem}.json",
        "pdf_path": f"data/pdfs/{pdf_path.name}",
    }


def main():
    parser = argparse.ArgumentParser(description="Extract FQ rulebook PDFs → structured JSON")
    parser.add_argument("--pdf", help="Process a single PDF instead of the whole directory")
    parser.add_argument("--dry-run", action="store_true", help="Parse but don't write output")
    parser.add_argument("--force", action="store_true", help="Re-extract even if unchanged")
    args = parser.parse_args()

    if args.pdf:
        pdfs = [Path(args.pdf)]
    else:
        if not PDF_DIR.exists():
            print(f"ERROR: PDF directory not found: {PDF_DIR}", file=sys.stderr)
            sys.exit(1)
        pdfs = sorted(PDF_DIR.glob("*.pdf"))
        if not pdfs:
            print(f"No PDFs found in {PDF_DIR}")
            sys.exit(0)

    print(f"FQ PDF Extractor — {len(pdfs)} file(s) found")
    print(f"Output dir: {OUT_DIR.relative_to(REPO_ROOT)}")

    if args.force:
        # Clear manifest to force re-extraction
        MANIFEST_PATH.unlink(missing_ok=True)

    manifest = load_manifest()
    updated = 0

    for pdf_path in pdfs:
        result = process_pdf(pdf_path, OUT_DIR, dry_run=args.dry_run)
        if result:
            manifest["files"][pdf_path.stem] = result
            updated += 1

    if not args.dry_run and updated:
        save_manifest(manifest)

    print(f"\nDone. {updated} file(s) extracted/updated.")


if __name__ == "__main__":
    main()
