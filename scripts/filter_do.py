"""
filter_do.py
────────────
Extract Dominican-Republic-sourced documents from any already-cleaned
JSONL shard directory by URL TLD.

This reuses the same conventions as the rest of the pipeline (JSONL in/out,
urlparse on the `url` field, per-domain counting) so it drops straight in
after clean_cc.py / clean_hf.py without re-crawling anything.

IMPORTANT CAVEAT (same as the README notes):
  A .do TLD means the document was *served from* a Dominican domain. That is
  genuinely DR-sourced, but it is overwhelmingly *formal* web Spanish
  (government, press, universities, business). It is NOT where dialectal
  features (dropped /s/, -se plurals, vocalized /r/, "vaina", etc.) surface.
  Treat this as your volume tier, and keep the spoken/oral corpora as a
  separate high-value dialectal tier.

Usage:
  python filter_do.py --input data/cleaned/commoncrawl --output data/dominican/cc_do.jsonl
  python filter_do.py --input data/cleaned/huggingface  --output data/dominican/mc4_do.jsonl
"""

import os
import json
import argparse
from glob import glob
from urllib.parse import urlparse
from collections import Counter

# Dominican Republic second/third-level domains. We accept anything whose
# netloc ends in one of these. ".do" alone covers them all, but listing the
# common SLDs lets us report a breakdown and guard against odd netlocs.
DO_SUFFIXES = (".do",)          # .com.do, .gob.do, .edu.do, .org.do, .net.do all end in .do

# Optional: tag the register so you can re-balance later. Purely informational.
REGISTER_HINTS = {
    ".gob.do": "government",
    ".edu.do": "academic",
    ".org.do": "organization",
    ".com.do": "commercial",
}


def is_dominican(url: str) -> bool:
    if not url:
        return False
    netloc = urlparse(url).netloc.lower()
    # strip a trailing port if present
    netloc = netloc.split(":")[0]
    return netloc.endswith(DO_SUFFIXES)


def register_of(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    for suffix, label in REGISTER_HINTS.items():
        if netloc.endswith(suffix):
            return label
    return "other_do"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True,
                    help="Directory of *.jsonl shards OR a single .jsonl file")
    ap.add_argument("--output", required=True,
                    help="Output .jsonl path for Dominican-sourced docs")
    args = ap.parse_args()

    if os.path.isdir(args.input):
        files = sorted(glob(os.path.join(args.input, "*.jsonl")))
    elif os.path.isfile(args.input):
        files = [args.input]
    else:
        print(f"No input found at {args.input}")
        raise SystemExit(1)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    print(f"Scanning {len(files)} file(s) for .do documents …\n", flush=True)

    total = 0
    kept = 0
    domains = Counter()
    registers = Counter()

    with open(args.output, "w", encoding="utf-8") as fout:
        for path in files:
            file_kept = 0
            with open(path, "r", encoding="utf-8") as fin:
                for line in fin:
                    line = line.strip()
                    if not line:
                        continue
                    total += 1
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    url = obj.get("url", "")
                    if not is_dominican(url):
                        continue

                    obj["country"]  = "DO"
                    obj["register"] = register_of(url)   # informational tag
                    fout.write(json.dumps(obj, ensure_ascii=False) + "\n")

                    kept += 1
                    file_kept += 1
                    domains[urlparse(url).netloc.lower().split(":")[0]] += 1
                    registers[obj["register"]] += 1

            print(f"  {os.path.basename(path):<40} kept={file_kept:,}", flush=True)

    print(f"\n{'='*55}")
    print(f"DOMINICAN (.do) FILTER COMPLETE")
    print(f"  Docs scanned          : {total:,}")
    print(f"  Dominican docs kept   : {kept:,}  ({kept/max(total,1)*100:.3f}%)")
    print(f"  Unique .do domains    : {len(domains):,}")
    print(f"  Output                : {args.output}")
    print(f"{'='*55}")

    if registers:
        print("\nBy register (informational):")
        for label, count in registers.most_common():
            print(f"  {label:<14} {count:,}")

    if domains:
        print("\nTop 15 .do domains (watch for a single site dominating):")
        for dom, count in domains.most_common(15):
            print(f"  {dom:<35} {count:,}")


if __name__ == "__main__":
    main()