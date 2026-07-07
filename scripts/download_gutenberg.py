import csv
import gzip
import io
import json
import os
import time
import re
import requests

# ── Configuration ─────────────────────────────────────────────────────────────

OUTPUT_DIR      = "data/raw/gutenberg"
PREFIX          = "gutenberg_es"
CHECKPOINT_FILE = os.path.join(OUTPUT_DIR, "checkpoint.json")

MAX_FILE_SIZE   = 200 * 1024 * 1024   # 200 MB — books are small, keep shards manageable
SLEEP_BETWEEN   = 2.0                  # seconds between HTTP requests (PG policy)

CATALOG_URL     = "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv.gz"

# Project Gutenberg robot-friendly mirror for plain text files
MIRROR_BASE     = "https://www.gutenberg.org/cache/epub"

# Gutendex — free, no-auth JSON API for PG metadata (gives us download URLs)
GUTENDEX_API    = "https://gutendex.com/books/{book_id}/"

HEADERS = {
    "User-Agent": "SpanishLLMDatasetPipeline/1.0 (academic research; "
                  "contact: your@email.edu)"
}

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ── Shard writer ──────────────────────────────────────────────────────────────

class JsonlShardWriter:
    def __init__(self, output_dir, prefix, max_file_size, start_shard=1):
        self.output_dir    = output_dir
        self.prefix        = prefix
        self.max_file_size = max_file_size
        self.shard_index   = start_shard
        self.file          = None
        self.current_size  = 0
        self._open_new_file()

    def _make_path(self):
        return os.path.join(self.output_dir,
                            f"{self.prefix}_{self.shard_index:05d}.jsonl")

    def _open_new_file(self):
        if self.file:
            self.file.close()
        path = self._make_path()
        if os.path.exists(path):
            self.current_size = os.path.getsize(path)
            self.file = open(path, "a", encoding="utf-8")
        else:
            self.current_size = 0
            self.file = open(path, "w", encoding="utf-8")
        print(f"  Shard → {os.path.basename(path)} "
              f"({self.current_size / 1024:.1f} KB)", flush=True)

    def write_obj(self, obj):
        line    = json.dumps(obj, ensure_ascii=False) + "\n"
        encoded = line.encode("utf-8")
        if self.current_size + len(encoded) > self.max_file_size:
            self.shard_index += 1
            self._open_new_file()
        self.file.write(line)
        self.current_size += len(encoded)

    def flush(self): self.file and self.file.flush()
    def close(self): self.file and self.file.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def save_checkpoint(state: dict):
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def load_catalog() -> list[dict]:
    """
    Download and parse pg_catalog.csv.gz.
    Returns a list of dicts for all Spanish-language text entries.
    """
    print("Downloading PG catalog …", flush=True)
    r = requests.get(CATALOG_URL, headers=HEADERS, timeout=60)
    r.raise_for_status()

    # The .gz contains a CSV — decompress in memory
    with gzip.open(io.BytesIO(r.content), "rt", encoding="utf-8") as f:
        raw = f.read()

    reader = csv.DictReader(io.StringIO(raw))
    books  = []
    for row in reader:
        if row.get("Type") != "Text":
            continue
        lang = row.get("Language", "")
        # Language field is a comma-separated list, e.g. "es" or "es,en"
        langs = [l.strip() for l in lang.split(",")]
        if "es" not in langs:
            continue
        books.append({
            "book_id":  row["Text#"],
            "title":    row.get("Title", "").strip(),
            "authors":  row.get("Authors", "").strip(),
            "subjects": row.get("Subjects", "").strip(),
            "language": lang.strip(),
            "issued":   row.get("Issued", "").strip(),
        })

    print(f"  Found {len(books):,} Spanish text entries in catalog.", flush=True)
    return books


def fetch_plain_text(book_id: str) -> str | None:
    """
    Try to download the plain-text file for a given PG book ID.
    Tries UTF-8 variant first, then ASCII fallback, then Gutendex API for URL.
    Returns the raw text string or None on failure.
    """
    bid = str(book_id)

    candidates = [
        # Standard UTF-8 plain text location
        f"https://www.gutenberg.org/files/{bid}/{bid}-0.txt",
        # Older books use just the ID
        f"https://www.gutenberg.org/files/{bid}/{bid}.txt",
        # Cache/epub mirror (robot-friendly)
        f"{MIRROR_BASE}/{bid}/pg{bid}.txt",
    ]

    for url in candidates:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200 and len(r.content) > 500:
                # Try UTF-8 first, fall back to latin-1
                try:
                    return r.content.decode("utf-8")
                except UnicodeDecodeError:
                    return r.content.decode("latin-1", errors="replace")
        except requests.RequestException:
            pass
        time.sleep(0.5)

    # Last resort: query Gutendex API for the actual download URL
    try:
        api_r = requests.get(GUTENDEX_API.format(book_id=bid),
                             headers=HEADERS, timeout=20)
        if api_r.status_code == 200:
            data   = api_r.json()
            formats = data.get("formats", {})
            # Look for plain text mime types
            for mime in ("text/plain; charset=utf-8",
                         "text/plain; charset=us-ascii",
                         "text/plain"):
                if mime in formats:
                    txt_url = formats[mime]
                    tr = requests.get(txt_url, headers=HEADERS, timeout=30)
                    if tr.status_code == 200 and len(tr.content) > 500:
                        try:
                            return tr.content.decode("utf-8")
                        except UnicodeDecodeError:
                            return tr.content.decode("latin-1", errors="replace")
    except (requests.RequestException, ValueError, KeyError):
        pass

    return None


# ── Main ──────────────────────────────────────────────────────────────────────

# Load or resume checkpoint
state = {"book_index": 0, "saved": 0, "failed": 0, "shard_index": 1}
if os.path.exists(CHECKPOINT_FILE):
    with open(CHECKPOINT_FILE) as f:
        state.update(json.load(f))
    print(f"Resuming from book_index={state['book_index']:,}  "
          f"saved={state['saved']:,}  failed={state['failed']:,}", flush=True)
else:
    print("Starting fresh.", flush=True)

# Always re-download catalog (it's small and updated weekly)
books = load_catalog()

writer = JsonlShardWriter(OUTPUT_DIR, PREFIX, MAX_FILE_SIZE, state["shard_index"])

saved  = state["saved"]
failed = state["failed"]

for i, book in enumerate(books):
    if i < state["book_index"]:
        continue  # skip already-processed on resume

    bid    = book["book_id"]
    title  = book["title"]
    print(f"[{i+1}/{len(books)}] Book {bid}: {title[:60]}", flush=True)

    text = fetch_plain_text(bid)
    time.sleep(SLEEP_BETWEEN)

    if not text:
        print(f"  ✗ Could not fetch text.", flush=True)
        failed += 1
    else:
        entry = {
            "text":     text,
            "url":      f"https://www.gutenberg.org/ebooks/{bid}",
            "title":    title,
            "authors":  book["authors"],
            "subjects": book["subjects"],
            "language": book["language"],
            "book_id":  int(bid) if bid.isdigit() else bid,
            "source":   "gutenberg_es",
        }
        writer.write_obj(entry)
        writer.flush()
        saved += 1
        print(f"  ✓ {len(text):,} chars saved.", flush=True)

    # Checkpoint after every book (they're slow to fetch — don't lose progress)
    save_checkpoint({
        "book_index":  i + 1,
        "saved":       saved,
        "failed":      failed,
        "shard_index": writer.shard_index,
    })

writer.flush()
writer.close()

print(f"\nDone!  {saved:,} books saved  |  {failed:,} failed  "
      f"|  {writer.shard_index} shard(s).", flush=True)
