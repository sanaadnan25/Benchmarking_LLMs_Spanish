import os
import json
import hashlib
from glob import glob

# Dedupes everything in the Dominican cleaned dir — this includes both the
# fresh .do crawl (clean_cc_do.py output) AND any mined_*.jsonl files you
# produced with filter_do.py, so a page captured by both is removed here.
INPUT_DIR = "dominican_data/cleaned"
OUTPUT_FILE = "dominican_data/final_deduped.jsonl"

os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

seen_hashes = set()

total = 0
kept = 0
duplicates = 0

with open(OUTPUT_FILE, "w", encoding="utf-8") as fout:
    for path in sorted(glob(os.path.join(INPUT_DIR, "*.jsonl"))):
        with open(path, "r", encoding="utf-8") as fin:
            for line in fin:
                total += 1
                try:
                    obj = json.loads(line)
                except Exception:
                    continue

                text = obj.get("text", "").strip()
                if not text:
                    continue

                h = hashlib.md5(text.encode("utf-8")).hexdigest()
                if h in seen_hashes:
                    duplicates += 1
                    continue

                seen_hashes.add(h)
                fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
                kept += 1

print("Done.")
print("Total read:", total)
print("Kept:", kept)
print("Duplicates removed:", duplicates)
print("Output:", OUTPUT_FILE)