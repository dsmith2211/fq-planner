#!/usr/bin/env python3
"""
embed.py — FQ App Chroma Embedding Pipeline
--------------------------------------------
Chunks extracted JSON text, generates embeddings, and upserts into a local
Chroma vector store at ./data/chroma/.

No LLM in this pipeline — pure retrieval. The search UI returns matching
chunks with source PDF references only.

Usage:
    python pipeline/embed.py                  # index all extracted JSONs
    python pipeline/embed.py --pdf rulebook_main   # re-index one source
    python pipeline/embed.py --reset          # drop and rebuild entire index
    python pipeline/embed.py --stats          # show collection stats

Chunk strategy:
    - Split on double-newlines (paragraph boundaries)
    - Min chunk size: 80 chars (skip noise)
    - Max chunk size: 800 chars (sliding window if exceeded)
    - Overlap: 80 chars between adjacent chunks

Requirements:
    pip install chromadb sentence-transformers
"""

import argparse
import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone


# ── Path config ───────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRACTED_DIR = REPO_ROOT / "data" / "extracted"
CHROMA_DIR = REPO_ROOT / "data" / "chroma"

# Embedding model — runs locally, no API key needed
# Swap to "all-mpnet-base-v2" for higher quality (slower/larger)
EMBED_MODEL = "all-MiniLM-L6-v2"
COLLECTION_NAME = "fq_rulebook"

# Chunking params
CHUNK_MAX = 800
CHUNK_MIN = 80
CHUNK_OVERLAP = 80


# ── Chunker ───────────────────────────────────────────────────────────────────

def sliding_split(text: str, max_len: int, overlap: int) -> list[str]:
    """Split a long paragraph into overlapping windows."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + max_len
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = end - overlap
    return [c for c in chunks if c]


def chunk_text(text: str, source_pdf: str, page_num: int | None = None) -> list[dict]:
    """
    Split text into overlapping chunks.
    Returns list of dicts with 'text', 'source_pdf', 'page', 'chunk_idx'.
    """
    # Normalize whitespace
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    paragraphs = re.split(r"\n\n+", text)

    raw_chunks = []
    buffer = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # Flush buffer when adding this para would exceed max
        if buffer and len(buffer) + len(para) + 2 > CHUNK_MAX:
            raw_chunks.append(buffer.strip())
            # Overlap: keep last overlap chars as seed for next chunk
            buffer = buffer[-CHUNK_OVERLAP:] + "\n\n" + para if CHUNK_OVERLAP else para
        else:
            buffer = (buffer + "\n\n" + para).strip() if buffer else para

        # If a single paragraph is huge, sliding window it
        while len(buffer) > CHUNK_MAX:
            parts = sliding_split(buffer, CHUNK_MAX, CHUNK_OVERLAP)
            for part in parts[:-1]:
                raw_chunks.append(part)
            buffer = parts[-1] if parts else ""

    if buffer.strip():
        raw_chunks.append(buffer.strip())

    # Filter noise
    chunks = []
    for i, c in enumerate(raw_chunks):
        if len(c) >= CHUNK_MIN:
            chunks.append({
                "text": c,
                "source_pdf": source_pdf,
                "page": page_num,
                "chunk_idx": i,
            })
    return chunks


def chunk_document(doc: dict) -> list[dict]:
    """Chunk an entire extracted document (all pages combined + per-page)."""
    source_pdf = doc["source_pdf"]
    all_chunks = []

    for page in doc.get("pages", []):
        text = page.get("text", "").strip()
        if not text:
            continue
        page_chunks = chunk_text(text, source_pdf, page_num=page["page"])
        all_chunks.extend(page_chunks)

    return all_chunks


# ── Chroma helpers ────────────────────────────────────────────────────────────

def get_chroma_client():
    try:
        import chromadb
    except ImportError:
        print("ERROR: chromadb not installed. Run: pip install chromadb", file=sys.stderr)
        sys.exit(1)
    return chromadb.PersistentClient(path=str(CHROMA_DIR))


def get_embedding_fn():
    try:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    except ImportError:
        print("ERROR: sentence-transformers not installed. Run: pip install sentence-transformers", file=sys.stderr)
        sys.exit(1)
    return SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)


def get_or_create_collection(client, embedding_fn, reset: bool = False):
    if reset:
        try:
            client.delete_collection(COLLECTION_NAME)
            print(f"  Dropped existing collection '{COLLECTION_NAME}'")
        except Exception:
            pass
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )
    return collection


def upsert_chunks(collection, chunks: list[dict], batch_size: int = 128):
    """Upsert chunks into Chroma with deterministic IDs."""
    if not chunks:
        return 0

    ids, docs, metas = [], [], []
    for ch in chunks:
        # Deterministic ID: source + page + chunk_idx
        chunk_id = f"{ch['source_pdf']}::p{ch['page']}::c{ch['chunk_idx']}"
        ids.append(chunk_id)
        docs.append(ch["text"])
        metas.append({
            "source_pdf": ch["source_pdf"],
            "page": ch["page"] if ch["page"] is not None else -1,
            "chunk_idx": ch["chunk_idx"],
            "char_count": len(ch["text"]),
        })

    # Batch upsert
    total = 0
    for i in range(0, len(ids), batch_size):
        collection.upsert(
            ids=ids[i:i+batch_size],
            documents=docs[i:i+batch_size],
            metadatas=metas[i:i+batch_size],
        )
        total += min(batch_size, len(ids) - i)
        print(f"  Upserted {total}/{len(ids)} chunks...", end="\r")

    print()
    return len(ids)


# ── Main ──────────────────────────────────────────────────────────────────────

def load_extracted_docs(stem_filter: str | None = None) -> list[dict]:
    files = sorted(f for f in EXTRACTED_DIR.glob("*.json") if f.name != "_manifest.json")
    if stem_filter:
        files = [f for f in files if f.stem == stem_filter]
    if not files:
        print(f"No extracted JSON found (filter={stem_filter})", file=sys.stderr)
        sys.exit(1)
    docs = []
    for f in files:
        with open(f, encoding="utf-8") as fp:
            docs.append(json.load(fp))
    return docs


def main():
    parser = argparse.ArgumentParser(description="Chunk + embed FQ rulebook into Chroma")
    parser.add_argument("--pdf", help="Only re-index this document stem (e.g. rulebook_main)")
    parser.add_argument("--reset", action="store_true", help="Drop and rebuild entire index")
    parser.add_argument("--stats", action="store_true", help="Print collection stats and exit")
    args = parser.parse_args()

    print("FQ Embedding Pipeline")
    print(f"Model      : {EMBED_MODEL}")
    print(f"Chroma dir : {CHROMA_DIR.relative_to(REPO_ROOT)}")
    print(f"Chunk max  : {CHUNK_MAX} chars | overlap: {CHUNK_OVERLAP} chars")
    print("")

    client = get_chroma_client()
    embedding_fn = get_embedding_fn()

    if args.stats:
        try:
            col = client.get_collection(COLLECTION_NAME, embedding_function=embedding_fn)
            count = col.count()
            print(f"Collection '{COLLECTION_NAME}': {count:,} chunks indexed")
        except Exception:
            print(f"Collection '{COLLECTION_NAME}' not found (not yet indexed)")
        return

    collection = get_or_create_collection(client, embedding_fn, reset=args.reset)
    docs = load_extracted_docs(stem_filter=args.pdf)

    total_chunks = 0
    for doc in docs:
        source = doc["source_pdf"]
        print(f"Chunking: {source}")
        chunks = chunk_document(doc)
        print(f"  → {len(chunks)} chunks from {doc.get('page_count', 0)} pages")
        n = upsert_chunks(collection, chunks)
        total_chunks += n
        print(f"  ✓ {n} chunks upserted")

    print(f"\nDone. {total_chunks:,} total chunks. Collection size: {collection.count():,}")
    print(f"Chroma DB: {CHROMA_DIR.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
