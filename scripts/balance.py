import json
from collections import defaultdict

INPUT_FILE = "spanish_data/final_deduped.jsonl"
OUTPUT_FILE = "spanish_data/final_balanced.jsonl"

MAX_PER_DOMAIN = 200   # 🔥 key parameter

domain_counts = defaultdict(int)

kept = 0
skipped = 0

from urllib.parse import urlparse

with open(INPUT_FILE, "r", encoding="utf-8") as fin, open(OUTPUT_FILE, "w", encoding="utf-8") as fout:
    for line in fin:
        try:
            obj = json.loads(line)
        except:
            continue

        url = obj.get("url", "")
        if not url:
            continue

        domain = urlparse(url).netloc

        if domain_counts[domain] >= MAX_PER_DOMAIN:
            skipped += 1
            continue

        domain_counts[domain] += 1
        fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        kept += 1

print(f"Kept: {kept}")
print(f"Skipped (over limit): {skipped}")
print(f"Unique domains: {len(domain_counts)}")
