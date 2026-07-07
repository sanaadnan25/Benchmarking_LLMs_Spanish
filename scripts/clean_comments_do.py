"""
clean_comments_do.py
────────────────────
Cleaner for the YouTube-comment tier (output of harvest_yt_comments_do.py).

WHY A SEPARATE CLEANER
──────────────────────
clean_cc_do.py is built for news/web prose and rejects anything under 400 chars
and anything "too short or repetitive" — which describes essentially every
comment. Running comments through it deletes the entire tier. This cleaner keeps
the same shape but is tuned for short informal text:

  * minimum length 12 chars, not 400
  * no 100-word floor (comments are SUPPOSED to be short)
  * unique-ratio / repetition checks apply ONLY to long comments, so "jaja jaja"
    survives but "gana dinero gana dinero ..." spam doesn't
  * lenient language gate (default-keep) so dialectal comments with no formal
    function words ("klk manito") aren't thrown away

The harvester already strips PII and does a first filtering pass, so this is
mostly a second, idempotent pass that also catches anything that bypassed it.
Metadata fields (source, source_type, register, country, video_id, …) are
preserved untouched.

Reads : dominican_data/raw/youtube_comments/*.jsonl
Writes: dominican_data/cleaned/*.jsonl   (same dir as your other DR cleaners,
        so dup_do.py picks it up automatically)
"""

import os
import re
import json
from glob import glob

INPUT_DIR  = "dominican_data/raw/youtube_comments"
OUTPUT_DIR = "dominican_data/cleaned"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Lenient language gate — same philosophy as the harvester. Default to KEEP;
# only reject text that looks clearly English.
SPANISH_HINTS = [" que ", " el ", " la ", " de ", " y ", " no ", " es ", " un ",
                 " una ", " lo ", " me ", " mi ", " tu ", " pa ", " con ", " por ",
                 " jaja", " dios ", " bien ", " ese ", " esa ", " pero "]
ENGLISH_MARKERS = [" the ", " you ", " and ", " is ", " this ", " for ", " what ",
                   " with ", " love ", " so ", " that "]

MIN_CHARS = 12
MAX_CHARS = 4_000     # above this it's copy-paste spam, not a comment


def normalize_text(text):
    text = text.replace("\x00", " ").replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"\s+", " ", text).strip()    # collapse newlines/runs
    return text


def looks_spanish(text):
    low = " " + text.lower() + " "
    if any(h in low for h in SPANISH_HINTS):
        return True
    if sum(1 for m in ENGLISH_MARKERS if m in low) >= 2:
        return False
    return True   # default keep — the channel is Dominican


def is_spam(text):
    """
    Repetition spam guard, applied ONLY to longer comments so short repetitive
    ones ("jaja jaja", "ok ok ok") are NOT killed — those are valid informal text.
    """
    words = text.lower().split()
    if len(words) >= 15:
        unique_ratio = len(set(words)) / len(words)
        if unique_ratio < 0.30:
            return True
    return False


def looks_noisy(text):
    """
    Looser than the news cleaner — comments legitimately carry more punctuation
    and emoji. Only drop when symbols/emoji dominate the text.
    """
    letters = sum(1 for c in text if c.isalpha())
    if letters / max(len(text), 1) < 0.45:
        return True
    return False


total = 0
kept = 0
skipped_short = 0
skipped_lang = 0
skipped_noise = 0
skipped_spam = 0

input_files = sorted(glob(os.path.join(INPUT_DIR, "*.jsonl")))
if not input_files:
    print(f"No input files in {INPUT_DIR}. Run harvest_yt_comments_do.py first.")
    raise SystemExit(1)

print(f"Found {len(input_files)} raw comment shard(s)\n", flush=True)

for path in input_files:
    out_path = os.path.join(OUTPUT_DIR, os.path.basename(path))
    file_kept = 0

    with open(path, "r", encoding="utf-8") as fin, \
         open(out_path, "w", encoding="utf-8") as fout:

        for line in fin:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            text = normalize_text(obj.get("text", ""))

            if len(text) < MIN_CHARS or len(text) > MAX_CHARS:
                skipped_short += 1
                continue
            if looks_noisy(text):
                skipped_noise += 1
                continue
            if is_spam(text):
                skipped_spam += 1
                continue
            if not looks_spanish(text):
                skipped_lang += 1
                continue

            obj["text"] = text          # preserve all other metadata fields
            fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            kept += 1
            file_kept += 1

    print(f"  {os.path.basename(path):<32} kept={file_kept:,}", flush=True)

print(f"\n{'='*55}")
print(f"COMMENT CLEANING COMPLETE")
print(f"  Comments read       : {total:,}")
print(f"  Comments kept       : {kept:,}")
print(f"  Skipped (length)    : {skipped_short:,}")
print(f"  Skipped (noise)     : {skipped_noise:,}")
print(f"  Skipped (spam)      : {skipped_spam:,}")
print(f"  Skipped (language)  : {skipped_lang:,}")
print(f"  Output directory    : {OUTPUT_DIR}")
print(f"{'='*55}")
