from pathlib import Path
import json
import hashlib
from glob import glob

BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_DIR = BASE_DIR / "data" / "cleaned" / "huggingface"
OUTPUT_DIR = BASE_DIR / "data" / "deduped" / "huggingface"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = OUTPUT_DIR / "hf_deduped.jsonl"

seen_hashes = set()
total = 0
kept = 0
duplicates = 0

files = sorted(glob(str(INPUT_DIR / "*.jsonl")))
print("INPUT_DIR =", INPUT_DIR)
print("Found", len(files), "cleaned HF files")

with open(OUTPUT_FILE, "w", encoding="utf-8") as fout:
    for path in files:
        print("Processing:", path)

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

                text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()

                if text_hash in seen_hashes:
                    duplicates += 1
                    continue

                seen_hashes.add(text_hash)
                fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
                kept += 1

print("Done.")
print("Total read:", total)
print("Kept:", kept)
print("Duplicates removed:", duplicates)
print("Output:", OUTPUT_FILE)