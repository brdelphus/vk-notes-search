#!/usr/bin/env python3
"""vk — a thin semantic-search layer over a folder of markdown notes.

Drop your .md files under `notes/`, run `index`, then `query` in natural
language. Markdown stays the source of truth in git; the vector DB is a
throwaway cache that can be rebuilt at any time.

Usage:
  vk.py sync                     # git pull (ff-only) + reindex — run before searching
  vk.py index                    # (re)index notes/**/*.md
  vk.py query "text" [--cat X]   # semantic search; --cat filters by category
  vk.py stats                    # what is indexed

Layout:
  notes/<category>/<note>.md    <- your notes (versioned)
  <cache dir>                   <- vector DB, regenerable, never commit

CHUNKING (why each note becomes several vectors):
Every note is split on markdown headings and each section is embedded on its
own, prefixed with the document title. Measured on a 54-note / 18-query bench
with hand-checked ground truth, same embedding model, only granularity changed:

    whole file  ->  recall@5  94.4%   MRR 0.861
    per section ->  recall@5 100.0%   MRR 0.972

The failure that disappeared was a note dominated by a long table of opaque
IDs: the words were there, but the first section is not what the whole-file
embedding was representing. This is a property of the index granularity, not of
the search engine -- a hybrid dense+BM25 engine costing 10x the footprint
produced the same recall@5 and missed the same note.

Search groups results by file, so the answer is still "the note", not "a piece
of it".
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
NOTES = Path(os.environ.get("VK_NOTES", REPO / "notes"))
DB = Path(os.environ.get("VK_DB", Path.home() / ".cache" / "vk"))

MIN_CHUNK = 80  # characters; shorter fragments are heading noise, not content
COLLECTION = "notes"

import chromadb  # noqa: E402


def sync():
    """Pull from the remote (if any) and reindex. Offline is not fatal."""
    if (REPO / ".git").exists():
        r = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=REPO, capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"[sync] pull: {r.returncode} — using local copy "
                  f"({r.stderr.strip()[:120] or 'no remote / offline'})")
        else:
            print(f"[sync] pull: {r.stdout.strip().splitlines()[-1][:100]}")
    index()


def get_collection(client):
    return client.get_or_create_collection(
        COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


def chunks_of(text):
    """Split on markdown headings; every chunk carries the document title."""
    title = ""
    m = re.search(r"^#\s+(.+)$", text, re.M)
    if m:
        title = m.group(1).strip()
    parts = re.split(r"(?m)^(?=#{1,6}\s)", text)
    out = []
    for p in parts:
        p = p.strip()
        if len(p) < MIN_CHUNK:
            continue
        # prefix the title so a chunk never loses the global context
        if title and not p.startswith("# "):
            p = f"{title}\n\n{p}"
        out.append(p)
    return out or [text]


def index():
    client = chromadb.PersistentClient(path=str(DB))
    col = get_collection(client)
    ids, docs, metas = [], [], []
    for md in sorted(NOTES.rglob("*.md")):
        rel = md.relative_to(NOTES).as_posix()
        category = rel.split("/")[0] if "/" in rel else "root"
        for i, chunk in enumerate(chunks_of(md.read_text(encoding="utf-8"))):
            ids.append(f"{rel}#{i}")
            docs.append(chunk)
            metas.append({"category": category, "file": rel, "chunk": i})
    if docs:
        # drop chunks orphaned by a note that shrank or was deleted
        existing = set(col.get()["ids"])
        col.upsert(ids=ids, documents=docs, metadatas=metas)
        stale = existing - set(ids)
        if stale:
            col.delete(ids=sorted(stale))
    nfiles = len({m["file"] for m in metas})
    print(f"indexed: {nfiles} notes in {len(docs)} chunks "
          f"({len({m['category'] for m in metas})} categories)")


def query(text, cat=None, n=5):
    client = chromadb.PersistentClient(path=str(DB))
    col = get_collection(client)
    where = {"category": cat} if cat else None
    # ask for more than n: several chunks usually land in the same file
    res = col.query(query_texts=[text], n_results=min(n * 4, max(col.count(), 1)), where=where)
    best = {}  # file -> (chunk_text, distance)
    for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        name = meta["file"]
        if name not in best or dist < best[name][1]:
            best[name] = (doc, dist)
    for name, (doc, dist) in sorted(best.items(), key=lambda kv: kv[1][1])[:n]:
        print(f"\n### {name}  (dist {dist:.3f})")
        print(doc.strip()[:600])


def stats():
    client = chromadb.PersistentClient(path=str(DB))
    col = get_collection(client)
    got = col.get()
    files = sorted({m["file"] for m in got["metadatas"]})
    print(f"total: {len(got['ids'])} chunks in {len(files)} notes")
    for m in got["metadatas"]:
        print(f"  {m['category']:10s} {m['file']}")


def main():
    ap = argparse.ArgumentParser(prog="vk.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync")
    sub.add_parser("index")
    q = sub.add_parser("query")
    q.add_argument("text")
    q.add_argument("--cat", default=None)
    q.add_argument("-n", type=int, default=5)
    sub.add_parser("stats")
    a = ap.parse_args()
    if a.cmd == "sync":
        sync()
    elif a.cmd == "index":
        index()
    elif a.cmd == "query":
        query(a.text, a.cat, a.n)
    elif a.cmd == "stats":
        stats()


if __name__ == "__main__":
    sys.exit(main())
