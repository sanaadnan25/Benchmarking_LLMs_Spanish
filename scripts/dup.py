import os
import json
import hashlib
from glob import glob

INPUT_DIR = "spanish_data/cleaned"
OUTPUT_FILE = "spanish_data/final_deduped.jsonl"

seen_hashes = set()

with open(OUTPUT_FILE, "w", encoding="utf-8") as fout:
    for path in sorted(glob(os.path.join(INPUT_DIR, "*.jsonl"))):
        with open(path, "r", encoding="utf-8") as fin:
            for line in fin:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue

                text = obj.get("text", "").strip()
                if not text:
                    continue

                h = hashlib.md5(text.encode("utf-8")).hexdigest()
                if h in seen_hashes:
                    continue

                seen_hashes.add(h)
                fout.write(json.dumps(obj, ensure_ascii=False) + "\n")