# vk — semantic search over a folder of markdown notes

A single-file engine that turns a directory of markdown notes into a searchable
knowledge base. Markdown stays the source of truth in git; the vector database
is a throwaway cache you can delete and rebuild at any moment.

It exists because notes already accumulate anyway — a fix you had to dig for, a
hardware quirk that cost an afternoon — and `grep` does not answer *"how did I
solve this last time"* when you no longer remember the words you used. Every
vector-search alternative wanted a framework, a server, or a 6 GB dependency
tree for what is fundamentally an embedding call plus a cosine lookup.

## Quick start

```bash
git clone https://github.com/brdelphus/vk-notes-search.git
cd vk-notes-search
./install.sh

vk index
vk query "nginx serving the wrong site"
```

```
### infra/nginx-wrong-site.md  (dist 0.287)
Nginx serves the wrong site after a config change

## The fix
...
```

The repo ships with a few example notes so this works on the first run. Delete
them once you have your own.

## Install

`install.sh` is idempotent — re-run it to upgrade chromadb or refresh the skill.

```bash
./install.sh                  # `vk` command on PATH
./install.sh --skill          # + a Hermes Agent skill so the agent uses it
./install.sh --prefix DIR     # custom wrapper location (default ~/.local/bin)
./install.sh --uninstall      # remove wrapper, venv and skill — notes untouched
```

What it does:

1. Finds `python3` (3.9+), refuses to continue without it.
2. Creates an **isolated venv** in `~/.local/share/vk/venv` and installs
   `chromadb` there. Your system Python is never touched, and this repo's
   dependencies cannot collide with anything else you run.
3. Writes a `~/.local/bin/vk` wrapper pointing at that venv and this checkout.
4. Warns if `~/.local/bin` is not on `PATH` (and shows the line to add).
5. Runs a smoke test in a throwaway database, so a broken install fails loudly
   instead of at your first real query.

Nothing is written outside `~/.local/bin`, `~/.local/share/vk` and — with
`--skill` — the Hermes skills directory. Your notes and the vector DB cache are
never removed by `--uninstall`.

### Manual install

If you would rather not run a script:

```bash
python3 -m venv ~/.local/share/vk/venv
~/.local/share/vk/venv/bin/pip install -r requirements.txt
printf '#!/usr/bin/env bash\nexec %s/venv/bin/python %s/vk.py "$@"\n' \
  "$HOME/.local/share/vk" "$PWD" > ~/.local/bin/vk
chmod +x ~/.local/bin/vk
```

## Install in Hermes Agent

[Hermes Agent](https://github.com/NousResearch/hermes-agent) is an autonomous
agent framework with a skill system: a `SKILL.md` file teaches the agent when and
how to use a tool, loaded only when relevant instead of sitting in every prompt.

`./install.sh --skill` drops a ready skill into the running profile:

```
~/.hermes/profiles/<profile>/skills/knowledge/vk-notes/SKILL.md
```

(or `~/.hermes/skills/...` if no profile is found). It documents the `sync` →
`query` flow, how to read the distance values, and the pitfalls — a note absent
from the top-5 is not a note absent from the index.

Then, in the agent conversation:

```
> what did I document about the USB audio dropout?
  [agent runs: vk sync && vk query "usb audio dropout"]
```

### Making the agent run it automatically

The skill above only fires when the topic matches. To make it a standing rule,
add a line to the agent's memory or system prompt:

> Before making an infrastructure change, run `vk sync` then `vk query "<topic>"`
> to recall prior context.

`sync` matters here: it pulls notes written on other machines and reindexes, so
recall reflects the current knowledge rather than whatever this host happened to
cache last month.

### Where the skill goes

| Hermes layout | Skill path |
|---|---|
| Profiled | `~/.hermes/profiles/<profile>/skills/knowledge/vk-notes/SKILL.md` |
| Flat | `~/.hermes/skills/knowledge/vk-notes/SKILL.md` |

Skills are discovered at agent startup — restart the session after installing.

## Commands

```
vk.py sync                      git pull (ff-only) + reindex — run before searching
vk.py index                     (re)index notes/**/*.md
vk.py query "text" [--cat X]    semantic search; --cat filters by category
vk.py query "text" -n 3         top-N results (default 5)
vk.py stats                     what is indexed
```

## Layout

```
notes/<category>/<note>.md    your notes — versioned in git
~/.cache/vk/                       vector DB — regenerable, never commit
```

The category is the first path segment under `notes/`, so `--cat infra`
filters to `notes/infra/*.md`.

| Variable | Default | Purpose |
|---|---|---|
| `VK_NOTES` | `<repo>/notes` | where your notes live |
| `VK_DB` | `~/.cache/vk` | where the vector DB lives |

---

# How it works

Four steps, in order: **chunk → embed → store → rank**. Each one is a few lines
of `vk.py`.

## 1. Chunking — one note becomes several vectors

`vk.py index` walks `notes/**/*.md` and splits each note on markdown
headings (`^#{1,6}\s`). Each section becomes its own chunk, and the document
title is prefixed to every chunk so a section never loses the context of the
note it came from:

```
# Nginx serves the wrong site after a config change

## The fix            ──┐
```bash                 │  chunk 2 becomes:
...                     │
```                   ──┘  "Nginx serves the wrong site after a config change
                             \n\n## The fix\n..."
```

Sections shorter than `MIN_CHUNK` (80 chars) are dropped — a bare heading with
no body carries no meaning and only adds noise to the index.

**Why chunk at all.** A whole note embedded as one vector is a single point
representing several unrelated paragraphs. When one of those paragraphs is a long
table of opaque IDs, that table dominates the direction of the vector and the
prose nearby becomes unfindable. Measured on a 54-note / 18-query bench with
hand-checked ground truth, same model, only granularity changed:

```
whole file per vector   recall@5  94.4%   MRR 0.861
one vector per section  recall@5 100.0%   MRR 0.972
```

The note that went from unreachable to top-1 was exactly this case: the query
words were present, but a single embedding of the whole file represented none of
them well.

**The lesson worth carrying:** the miss was *index granularity, not the search
engine*. A hybrid dense + BM25 engine was benchmarked against the same corpus and
produced identical recall@5 with the identical miss, at roughly 10x the disk
footprint. Fix granularity before shopping for a better engine.

## 2. Embedding — text to vector

Each chunk is handed to chromadb's embedding function, which runs
**all-MiniLM-L6-v2** — a 384-dimensional sentence-transformer exported to ONNX
and executed locally. No API key, no network call at query time.

The model is downloaded once on the first `index` (~79 MB, cached in
`~/.cache/chroma/onnx_models/`). It looks frozen at 0% for about 40 seconds and
then completes on its own — do not kill it.

Embeddings are deterministic: the same text always produces the same vector, so
reindexing an unchanged note is idempotent.

## 3. Storage — one collection, three fields per row

Everything lands in a single chromadb collection named `notes`, configured
for **cosine** distance:

```python
metadata={"hnsw:space": "cosine"}
```

Each row holds:

| Field | Example | Role |
|---|---|---|
| `ids` | `infra/nginx-wrong-site.md#2` | note path + chunk offset — unique and stable |
| `documents` | the chunk text | what gets returned |
| `metadatas` | `{category, file, chunk}` | filtering and grouping |

Because the id is derived from the path, re-indexing the same note **upserts**
rather than duplicating. `index()` also diffs the incoming id set against what
already exists and deletes orphans — so a note that shrank or was deleted does
not leave a zombie chunk behind, permanently findable but no longer real.

The index itself is an **HNSW graph**: approximate nearest-neighbour search that
trades a small amount of recall for orders of magnitude in speed. Exact search
over a few thousand vectors would also be fast; HNSW is what makes it stay fast
at a hundred thousand.

## 4. Ranking — nearest chunks, then grouped by note

`vk.py query` embeds your text with the same model and asks for the `n * 4`
nearest chunks. It deliberately over-fetches, because several chunks frequently
belong to the same note; without over-fetching, a single verbose note could
consume the entire top-5 with fragments of itself.

Results are then **deduplicated by note**, keeping each note's best-scoring
chunk, and sorted by distance:

```python
best = {}                      # note -> (chunk, distance)
for doc, meta, dist in results:
    f = meta["file"]
    if f not in best or dist < best[f][1]:
        best[f] = (doc, dist)
```

That grouping step is what makes the output feel like search over *notes* rather
than over *paragraphs* — you get "this note is the answer", with the most
relevant section shown as the excerpt.

Distances are cosine distance: **0.0 is an identical vector, higher is less
similar**. Useful reference points on this corpus: under ~0.35 is a strong match,
0.4–0.6 is usually a real hit from the right note, above ~0.7 is the engine
telling you it found nothing good.

---

## Design notes

- **Notes are the source of truth.** Delete the cache, run `index`, lose nothing.
  This is why the DB lives outside the repo and is gitignored.
- **No server.** Chroma runs embedded, in-process. No daemon to babysit, no port
  to expose, nothing to keep alive between queries.
- **One file.** `vk.py` is meant to be read end to end in a couple of minutes and
  edited without ceremony. Nothing is abstracted behind a plugin boundary.
- **Markdown over a database.** You can read, diff, `git blame` and hand-edit the
  knowledge. A row in a proprietary store, you cannot.
- **Rebuilding is cheap.** A few hundred notes reindex in seconds.

## Install as a command

```bash
chmod +x vk.py
ln -s "$PWD/vk.py" ~/.local/bin/vk
vk query "that thing"
```

## Agent usage

The engine was built to be driven by an LLM agent as much as by a person: `query`
prints plain text, exits non-zero on nothing found, and never opens a pager or a
TUI. A typical agent rule is *run `vk.py sync`, then `vk.py query`, before
touching infrastructure* — so recall reflects notes pulled from other machines
rather than a stale local cache.

## Requirements

Python 3.9+ and `chromadb`. See `requirements.txt`.

## License

MIT
