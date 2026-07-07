from pathlib import Path
import json
import hashlib
from glob import glob

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).resolve().parent
INPUT_DIR  = BASE_DIR / "data" / "cleaned" / "gutenberg"
OUTPUT_DIR = BASE_DIR / "data" / "deduped" / "gutenberg"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = OUTPUT_DIR / "gutenberg_deduped.jsonl"

# ── Deduplication ─────────────────────────────────────────────────────────────

seen_hashes: set[str] = set()

total      = 0
kept       = 0
duplicates = 0

# Also track per-book duplicates for reporting
book_seen:  dict[str, int] = {}   # book_id → chunks kept

files = sorted(glob(str(INPUT_DIR / "*.jsonl")))
print(f"INPUT_DIR  = {INPUT_DIR}")
print(f"OUTPUT_FILE = {OUTPUT_FILE}")
print(f"Found {len(files)} cleaned file(s)\n", flush=True)

with open(OUTPUT_FILE, "w", encoding="utf-8") as fout:
    for path in files:
        print(f"Processing: {path}", flush=True)
        file_dupes = 0
        file_kept  = 0

        with open(path, "r", encoding="utf-8") as fin:
            for line in fin:
                total += 1
                line   = line.strip()
                if not line:
                    continue

                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                text = obj.get("text", "").strip()
                if not text:
                    continue

                text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()

                if text_hash in seen_hashes:
                    duplicates += 1
                    file_dupes += 1
                    continue

                seen_hashes.add(text_hash)
                fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
                kept      += 1
                file_kept += 1

                # Track per-book chunk counts
                bid = str(obj.get("book_id", "unknown"))
                book_seen[bid] = book_seen.get(bid, 0) + 1

        print(f"  kept={file_kept:,}  dupes={file_dupes:,}", flush=True)

# ── Summary ───────────────────────────────────────────────────────────────────

print(f"\n{'='*55}")
print(f"DEDUPLICATION COMPLETE")
print(f"  Total chunks read     : {total:,}")
print(f"  Chunks kept           : {kept:,}")
print(f"  Duplicates removed    : {duplicates:,}")
print(f"  Unique books retained : {len(book_seen):,}")
print(f"  Output                : {OUTPUT_FILE}")
print(f"{'='*55}")

# Show top 10 books by chunk count (sanity check — no single book
# should dominate the corpus)
if book_seen:
    print("\nTop 10 books by chunk count (should be well-distributed):")
    top10 = sorted(book_seen.items(), key=lambda x: x[1], reverse=True)[:10]
    for bid, count in top10:
        print(f"  book_id={bid:<8}  chunks={count:,}")
