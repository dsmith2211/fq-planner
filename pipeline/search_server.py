#!/usr/bin/env python3
"""
search_server.py — FQ Rulebook Retrieval Search API
----------------------------------------------------
Serves a local HTTP API for vector similarity search against the Chroma index.
NO LLM in the response path — pure retrieval only.

Endpoints:
    GET  /search?q=<query>&n=<top_k>  → JSON results
    GET  /health                       → {"status":"ok","count":<n>}
    GET  /                             → serves docs/search.html (static UI)

Usage:
    python pipeline/search_server.py
    python pipeline/search_server.py --port 8765
    python pipeline/search_server.py --host 0.0.0.0 --port 8765

The search UI at docs/search.html calls this server directly from the browser
when running locally. For GitHub Pages deployment, it falls back to a
client-side message explaining Chroma is local-only.

Requirements:
    pip install chromadb sentence-transformers
"""

import json
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CHROMA_DIR = REPO_ROOT / "data" / "chroma"
STATIC_DIR = REPO_ROOT / "docs"
COLLECTION_NAME = "fq_rulebook"
EMBED_MODEL = "all-MiniLM-L6-v2"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_TOP_K = 8
MAX_TOP_K = 20

# ── Lazy-loaded Chroma client (singleton) ────────────────────────────────────
_collection = None

def get_collection():
    global _collection
    if _collection is not None:
        return _collection
    try:
        import chromadb
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    except ImportError:
        print("ERROR: pip install chromadb sentence-transformers", file=sys.stderr)
        sys.exit(1)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    ef = SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
    _collection = client.get_collection(COLLECTION_NAME, embedding_function=ef)
    return _collection


def search(query: str, n: int = DEFAULT_TOP_K) -> list[dict]:
    """Run similarity search. Returns list of result dicts."""
    n = min(max(1, n), MAX_TOP_K)
    col = get_collection()
    results = col.query(
        query_texts=[query],
        n_results=n,
        include=["documents", "metadatas", "distances"],
    )

    hits = []
    docs = results["documents"][0]
    metas = results["metadatas"][0]
    dists = results["distances"][0]

    for doc, meta, dist in zip(docs, metas, dists):
        # Cosine distance → similarity score (0–1, higher = more similar)
        score = round(1.0 - dist, 4)
        hits.append({
            "score": score,
            "text": doc,
            "source_pdf": meta.get("source_pdf", ""),
            "page": meta.get("page", -1),
            "chunk_idx": meta.get("chunk_idx", 0),
            "char_count": meta.get("char_count", 0),
            # PDF URL relative to repo root — caller resolves full path
            "pdf_url": f"data/pdfs/{meta.get('source_pdf', '')}",
        })

    return hits


# ── HTTP Handler ──────────────────────────────────────────────────────────────

class SearchHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"[{self.address_string()}] {fmt % args}")

    def send_json(self, data: dict | list, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_html_file(self, path: Path):
        if not path.exists():
            self.send_error(404, f"Not found: {path.name}")
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = dict(urllib.parse.parse_qsl(parsed.query))

        if path == "/health":
            try:
                col = get_collection()
                count = col.count()
                self.send_json({"status": "ok", "count": count, "model": EMBED_MODEL})
            except Exception as e:
                self.send_json({"status": "error", "message": str(e)}, status=503)
            return

        if path == "/search":
            q = qs.get("q", "").strip()
            if not q:
                self.send_json({"error": "Missing query parameter ?q="}, status=400)
                return
            try:
                n = int(qs.get("n", DEFAULT_TOP_K))
            except ValueError:
                n = DEFAULT_TOP_K
            try:
                hits = search(q, n)
                self.send_json({"query": q, "count": len(hits), "results": hits})
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)
            return

        if path == "/" or path == "/search.html":
            self.send_html_file(STATIC_DIR / "search.html")
            return

        self.send_error(404)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="FQ Rulebook search server (retrieval-only)")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    # Warm up collection on start
    print(f"Loading Chroma collection '{COLLECTION_NAME}'...")
    try:
        col = get_collection()
        print(f"  {col.count():,} chunks indexed")
    except Exception as e:
        print(f"  WARNING: {e}")
        print("  Run 'python pipeline/embed.py' first to build the index.")

    addr = (args.host, args.port)
    print(f"\nFQ Search Server running at http://{args.host}:{args.port}")
    print(f"Search UI : http://{args.host}:{args.port}/")
    print(f"Health    : http://{args.host}:{args.port}/health")
    print(f"Search API: http://{args.host}:{args.port}/search?q=how+do+I+cast+a+spell")
    print("\nPress Ctrl-C to stop.\n")

    server = HTTPServer(addr, SearchHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()
