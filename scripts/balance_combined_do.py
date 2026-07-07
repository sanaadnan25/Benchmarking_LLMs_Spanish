"""
balance_combined_do.py
──────────────────────
Tier-aware balancer for the combined Dominican corpus.

THE PROBLEM THIS FIXES
──────────────────────
balance_do.py caps per DOMAIN. Every YouTube comment shares the domain
"www.youtube.com", so a domain cap collapses the ENTIRE comment tier to
MAX_PER_DOMAIN (200) — which is what happened: 132,265 comments → 200.

THE FIX
───────
Use a different balance key per tier:
  * comments (social)  → key on video_id   (cap per video, not per site)
  * web (.do crawl)    → key on domain      (cap per .do site, as before)

So each .do newspaper is capped, AND each YouTube video is capped, but the
comment tier as a whole is preserved.

TUNING
──────
Comments are your dialectal signal, so you may WANT them to dominate. Options:
  * MAX_PER_VIDEO = 200   → balanced: trims heavy videos, keeps breadth
  * MAX_PER_VIDEO = None  → keep ALL comments (corpus stays ~80% dialectal)
Set to taste. MAX_PER_DOMAIN controls the web tier independently.

Reads : dominican_data/final_deduped.jsonl
Writes: dominican_data/final_balanced.jsonl   (REPLACES the broken one)
"""

import json
from collections import defaultdict
from urllib.parse import urlparse

INPUT_FILE  = "dominican_data/final_deduped.jsonl"
OUTPUT_FILE = "dominican_data/final_balanced.jsonl"

MAX_PER_DOMAIN = 200    # cap per .do web domain
MAX_PER_VIDEO  = 200    # cap per YouTube video  (set to None to keep ALL comments)


def is_comment(obj) -> bool:
    if obj.get("source_type") == "social":
        return True
    if obj.get("source") == "youtube_comments_do":
        return True
    # fallback: crawl cleaner doesn't tag source, but comments always carry video_id
    if obj.get("video_id"):
        return True
    return "youtube.com" in obj.get("url", "")


def balance_key(obj):
    """Return ('video', id) for comments or ('domain', netloc) for web."""
    if is_comment(obj):
        vid = obj.get("video_id") or obj.get("url", "")
        return ("video", vid)
    return ("domain", urlparse(obj.get("url", "")).netloc)


counts = defaultdict(int)

kept = 0
skipped = 0
kept_comments = 0
kept_web = 0

with open(INPUT_FILE, "r", encoding="utf-8") as fin, \
     open(OUTPUT_FILE, "w", encoding="utf-8") as fout:

    for line in fin:
        try:
            obj = json.loads(line)
        except Exception:
            continue

        kind, key = balance_key(obj)
        if not key:
            skipped += 1
            continue

        cap = MAX_PER_VIDEO if kind == "video" else MAX_PER_DOMAIN

        if cap is not None and counts[(kind, key)] >= cap:
            skipped += 1
            continue

        counts[(kind, key)] += 1
        fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        kept += 1
        if kind == "video":
            kept_comments += 1
        else:
            kept_web += 1

n_videos  = sum(1 for (k, _) in counts if k == "video")
n_domains = sum(1 for (k, _) in counts if k == "domain")

print(f"Kept total           : {kept:,}")
print(f"  comments (social)  : {kept_comments:,}  across {n_videos:,} videos")
print(f"  web (.do)          : {kept_web:,}  across {n_domains:,} domains")
print(f"Skipped (over cap)   : {skipped:,}")
print(f"Caps: per-video={MAX_PER_VIDEO}  per-domain={MAX_PER_DOMAIN}")
print(f"Output: {OUTPUT_FILE}")
