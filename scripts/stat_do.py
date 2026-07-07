import os
import json
import hashlib
from glob import glob
from urllib.parse import urlparse
from collections import Counter

STAGES = {
    "do_cleaned":  "dominican_data/cleaned",
    "do_deduped":  "dominican_data/final_deduped.jsonl",
    "do_balanced": "dominican_data/final_balanced.jsonl",
}

def scan_stage(path):
    if os.path.isfile(path):
        files = [path]
    elif os.path.isdir(path):
        files = sorted(glob(os.path.join(path, "*.jsonl")))
    else:
        return None

    doc_count = 0
    total_chars = 0
    total_words = 0
    total_bytes = 0
    urls = set()
    domains = Counter()
    sources = Counter()          # NEW: which source each doc came from
    source_types = Counter()     # NEW: web vs social vs curated
    seen_hashes = set()
    dupes = 0

    for file_num, fpath in enumerate(files, 1):
        fname = os.path.basename(fpath)
        file_size_mb = os.path.getsize(fpath) / (1024 ** 2)
        print(f"  [{file_num}/{len(files)}] Reading {fname} ({file_size_mb:.1f} MB)...",
              flush=True)

        total_bytes += os.path.getsize(fpath)
        line_num = 0

        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                line_num += 1

                # Progress every 100k lines
                if line_num % 100_000 == 0:
                    print(f"    → {line_num:,} lines | {doc_count:,} docs so far",
                          flush=True)

                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                text = obj.get("text", "")
                url  = obj.get("url", "")

                h = hashlib.md5(text.encode("utf-8")).hexdigest()
                if h in seen_hashes:
                    dupes += 1
                    continue
                seen_hashes.add(h)

                doc_count  += 1
                total_chars += len(text)
                total_words += len(text.split())

                sources[obj.get("source", "unknown")] += 1
                source_types[obj.get("source_type", "unknown")] += 1

                if url:
                    urls.add(url)
                    domains[urlparse(url).netloc] += 1

        print(f"  ✓ Done — {doc_count:,} docs accumulated so far", flush=True)

    return {
        "files":          len(files),
        "docs":           doc_count,
        "duplicates":     dupes,
        "unique_urls":    len(urls),
        "unique_domains": len(domains),
        "total_chars":    total_chars,
        "total_words":    total_words,
        "size_bytes":     total_bytes,
        "size_gb":        total_bytes / (1024 ** 3),
        "avg_chars":      total_chars / doc_count if doc_count else 0,
        "avg_words":      total_words / doc_count if doc_count else 0,
        "top_domains":    domains.most_common(10),
        "sources":        sources.most_common(),
        "source_types":   source_types.most_common(),
    }

def fmt(n):
    return f"{n:,.0f}"

print("=" * 65)
print(f"{'STAGE':<16} {'DOCS':>10} {'WORDS':>14} {'SIZE GB':>9} {'URLS':>10}")
print("=" * 65)

all_results = {}
for stage, path in STAGES.items():
    print(f"\nScanning stage: {stage} → {path}", flush=True)
    r = scan_stage(path)
    if r is None:
        print(f"{stage:<16} {'(not found)':>10}")
        continue
    all_results[stage] = r
    print(f"{stage:<16} {fmt(r['docs']):>10} {fmt(r['total_words']):>14} "
          f"{r['size_gb']:>9.2f} {fmt(r['unique_urls']):>10}")

print("=" * 65)
print()

# Detailed per-stage report
for stage, r in all_results.items():
    print(f"── {stage.upper()} ──────────────────────────────────────")
    print(f"  Files:            {fmt(r['files'])}")
    print(f"  Documents:        {fmt(r['docs'])}")
    print(f"  Duplicates found: {fmt(r['duplicates'])}")
    print(f"  Unique URLs:      {fmt(r['unique_urls'])}")
    print(f"  Unique domains:   {fmt(r['unique_domains'])}")
    print(f"  Total characters: {fmt(r['total_chars'])}")
    print(f"  Total words:      {fmt(r['total_words'])}")
    print(f"  Dataset size:     {r['size_gb']:.3f} GB")
    print(f"  Avg chars/doc:    {r['avg_chars']:,.1f}")
    print(f"  Avg words/doc:    {r['avg_words']:,.1f}")

    # NEW: source mix — how much is formal .do web vs dialectal comments
    print(f"  By source_type:")
    for label, count in r['source_types']:
        pct = count / r['docs'] * 100 if r['docs'] else 0
        print(f"    {label:<22} {fmt(count):>10}  ({pct:5.1f}%)")
    print(f"  By source:")
    for label, count in r['sources']:
        pct = count / r['docs'] * 100 if r['docs'] else 0
        print(f"    {label:<22} {fmt(count):>10}  ({pct:5.1f}%)")

    print(f"  Top 10 domains:")
    for domain, count in r['top_domains']:
        print(f"    {domain:<45} {fmt(count):>8}")
    print()