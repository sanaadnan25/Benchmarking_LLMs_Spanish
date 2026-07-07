"""
clean_gutenberg.py
──────────────────
Clean the raw Project Gutenberg Spanish books produced by download_gutenberg.py.

What this script does (in order)
──────────────────────────────────
1.  Strip the Project Gutenberg legal header and footer that appears in
    every plain-text book. These are boilerplate and not part of the
    original literary text.
2.  Remove common transcription/scanning artefacts left by volunteers
    (chapter markers, illustration tags, blank line runs, etc.).
3.  Normalise whitespace.
4.  Language-check the cleaned body text (same heuristic as clean_hf.py).
5.  Filter books that are too short after stripping (stubs, indexes,
    appendices mistakenly listed as separate books).
6.  Filter books with too many symbols (OCR errors, tables, code).
7.  Split very long books into chapter-level chunks for benchmarking
    (same philosophy as clean_wikipedia.py's paragraph chunking).
8.  Write each chunk as a separate JSONL object, preserving full book
    metadata (title, authors, subjects, book_id, url).

Output schema
─────────────
{
  "text":     "<cleaned chunk of book text>",
  "url":      "https://www.gutenberg.org/ebooks/{id}",
  "title":    "...",
  "authors":  "...",
  "subjects": "...",
  "book_id":  12345,
  "chunk":    0,           # chunk index within book (0-indexed)
  "source":   "gutenberg_es",
  "source_type": "curated"  # literary/curated text, like wikipedia
}
"""

import os
import re
import json
from glob import glob

INPUT_DIR  = "data/raw/gutenberg"
OUTPUT_DIR = "data/cleaned/gutenberg"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Same Spanish function-word list used across all cleaners in this pipeline
SPANISH_HINTS = [" el ", " la ", " de ", " que ", " y ", " en ", " los ", " las "]

# Books shorter than this (after stripping) are indexes, tables of contents,
# appendices, or mis-catalogued non-prose entries
MIN_BOOK_CHARS = 3_000

# Chunk size bounds (characters). Books are split at blank lines / chapter
# headings to produce chunks of roughly this size.
MIN_CHUNK_CHARS = 500
MAX_CHUNK_CHARS = 8_000    # longer than Wikipedia chunks — books have longer
                            # coherent passages than encyclopedia articles


# ── PG header / footer stripping ─────────────────────────────────────────────

# These patterns are standardised across all PG plain-text releases.
# Reference: https://www.gutenberg.org/policy/license.html

HEADER_PATTERN = re.compile(
    r"^\*{3}\s*START OF (?:THE |THIS )?PROJECT GUTENBERG.*?\*{3}",
    re.IGNORECASE | re.MULTILINE
)
FOOTER_PATTERN = re.compile(
    r"\*{3}\s*END OF (?:THE |THIS )?PROJECT GUTENBERG.*$",
    re.IGNORECASE | re.MULTILINE | re.DOTALL
)


def strip_pg_boilerplate(text: str) -> str:
    """
    Remove everything before the START marker and everything from the
    END marker onwards.  If markers are absent (some old books lack them),
    return the text unchanged — the boilerplate filter below will catch
    egregious cases.
    """
    # Find header end
    hm = HEADER_PATTERN.search(text)
    if hm:
        text = text[hm.end():]

    # Find footer start
    fm = FOOTER_PATTERN.search(text)
    if fm:
        text = text[:fm.start()]

    return text.strip()


# ── Artefact removal ──────────────────────────────────────────────────────────

def remove_gutenberg_artefacts(text: str) -> str:
    """
    Strip volunteer transcription markers and scanning artefacts that
    appear inside the body of PG plain-text books.
    """
    # Illustration / image tags:  [Illustration: caption]  or  [Ilustración]
    text = re.sub(r"\[Ilustraci[oó]n[^\]]*\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[Illustration[^\]]*\]",    "", text, flags=re.IGNORECASE)

    # Transcriber / editor notes:  [Note: ...]  [Nota: ...]
    text = re.sub(r"\[Nota[^\]]{0,200}\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[Note[^\]]{0,200}\]", "", text, flags=re.IGNORECASE)

    # Page numbers left by OCR:  [pg 42]  [p. 42]  or standalone digits on a line
    text = re.sub(r"\[p(?:g|age)?\.?\s*\d+\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)

    # Excessive underscores / equals used as decorative rules
    text = re.sub(r"[_=]{4,}", " ", text)

    # Asterisk lines used as section dividers
    text = re.sub(r"^\s*\*[\s\*]+\*\s*$", "", text, flags=re.MULTILINE)

    # Multiple consecutive blank lines → two newlines
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ── Text normalisation ────────────────────────────────────────────────────────

def normalize_text(text: str) -> str:
    """Shared normalisation: null bytes, non-breaking spaces, whitespace runs."""
    text = text.replace("\x00", " ")
    text = text.replace("\xa0", " ")     # non-breaking space
    text = text.replace("\u200b", "")    # zero-width space
    text = text.replace("\r\n", "\n")    # Windows line endings
    text = text.replace("\r", "\n")
    # Collapse horizontal whitespace within lines (preserve paragraph breaks)
    text = re.sub(r"[^\S\n]+", " ", text)
    # Normalise multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Quality filters ───────────────────────────────────────────────────────────

def looks_spanish(text: str) -> bool:
    """Identical to the heuristic in clean_hf.py / clean_wikipedia.py."""
    sample = " " + text[:3000].lower() + " "
    hits   = sum(1 for w in SPANISH_HINTS if w in sample)
    return hits >= 3


def is_noisy(text: str) -> bool:
    """
    High symbol ratio → OCR garbage, heavily formatted tables, or
    non-prose content (sheet music, code listings, etc.).
    Threshold is slightly looser than Wikipedia (0.25) because literary
    text can include more punctuation (dialogue, poetry).
    """
    symbol_ratio = sum(
        1 for c in text if not c.isalnum() and not c.isspace()
    ) / max(len(text), 1)
    return symbol_ratio > 0.30


def is_too_short(text: str) -> bool:
    return len(text) < MIN_BOOK_CHARS


# ── Chapter-level chunking ────────────────────────────────────────────────────

# Patterns that typically mark chapter / section boundaries in Spanish books
CHAPTER_PATTERN = re.compile(
    r"^\s*(?:"
    r"CAP[IÍ]TULO\s+[IVXLCDM\d]+"     # CAPÍTULO I, CAPITULO 2
    r"|CAP\.\s*[IVXLCDM\d]+"           # CAP. I
    r"|PARTE\s+[IVXLCDM\d]+"           # PARTE I
    r"|LIBRO\s+[IVXLCDM\d]+"           # LIBRO I
    r"|CANTO\s+[IVXLCDM\d]+"           # CANTO I  (epic poetry)
    r"|JORNADA\s+[IVXLCDM\d]+"         # JORNADA I  (theatre)
    r"|\d+\.\s+\S.*"                   # 1. El Comienzo  (numbered heading line)
    r")\s*$",
    re.IGNORECASE | re.MULTILINE
)


def split_into_chunks(text: str) -> list[str]:
    """
    Split a book into chapter-level chunks suitable for benchmarking.

    Strategy:
    1. Try to split at chapter headings (CAPÍTULO, PARTE, etc.).
    2. If no headings found, fall back to splitting at paragraph boundaries.
    3. Merge or further split segments to stay within [MIN, MAX] char bounds.
    """
    # Try chapter-heading split first
    splits = CHAPTER_PATTERN.split(text)

    # If the regex gives nothing useful (< 2 parts), fall back to paragraphs
    if len(splits) < 2:
        splits = re.split(r"\n{2,}", text)

    chunks = []
    current = ""

    for segment in splits:
        segment = segment.strip()
        if not segment:
            continue

        if len(current) + len(segment) <= MAX_CHUNK_CHARS:
            current += ("\n\n" if current else "") + segment
        else:
            # Flush current buffer if it's long enough
            if len(current) >= MIN_CHUNK_CHARS:
                chunks.append(current.strip())
            # If segment itself is too large, split at sentence boundaries
            if len(segment) > MAX_CHUNK_CHARS:
                sentences = re.split(r"(?<=[.!?¿¡])\s+", segment)
                sub = ""
                for sent in sentences:
                    if len(sub) + len(sent) <= MAX_CHUNK_CHARS:
                        sub += (" " if sub else "") + sent
                    else:
                        if len(sub) >= MIN_CHUNK_CHARS:
                            chunks.append(sub.strip())
                        sub = sent
                if len(sub) >= MIN_CHUNK_CHARS:
                    chunks.append(sub.strip())
                current = ""
            else:
                current = segment

    if len(current) >= MIN_CHUNK_CHARS:
        chunks.append(current.strip())

    return chunks


# ── Main ──────────────────────────────────────────────────────────────────────

input_files = sorted(glob(os.path.join(INPUT_DIR, "*.jsonl")))
print(f"Found {len(input_files)} raw shard(s) in {INPUT_DIR}\n", flush=True)

if not input_files:
    print("No input files found. Run download_gutenberg.py first.")
    raise SystemExit(1)

total_books = 0
kept_chunks = 0
skipped_short = 0
skipped_lang  = 0
skipped_noise = 0

for file_path in input_files:
    base     = os.path.basename(file_path).replace(".jsonl", "_clean.jsonl")
    out_path = os.path.join(OUTPUT_DIR, base)

    print(f"Processing {os.path.basename(file_path)} …", flush=True)

    with open(file_path, "r", encoding="utf-8") as fin, \
         open(out_path, "w", encoding="utf-8") as fout:

        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            total_books += 1
            raw_text = obj.get("text", "")

            # Step 1: strip PG legal header/footer
            text = strip_pg_boilerplate(raw_text)

            # Step 2: remove transcription artefacts
            text = remove_gutenberg_artefacts(text)

            # Step 3: normalise whitespace
            text = normalize_text(text)

            if not text:
                skipped_short += 1
                continue

            # Step 4: too short?
            if is_too_short(text):
                skipped_short += 1
                continue

            # Step 5: language check
            if not looks_spanish(text):
                skipped_lang += 1
                continue

            # Step 6: noise check
            if is_noisy(text):
                skipped_noise += 1
                continue

            # Step 7: split into chunks
            chunks = split_into_chunks(text)

            # Step 8: write each chunk as its own JSONL record
            for chunk_idx, chunk in enumerate(chunks):
                entry = {
                    "text":        chunk,
                    "url":         obj.get("url", ""),
                    "title":       obj.get("title", ""),
                    "authors":     obj.get("authors", ""),
                    "subjects":    obj.get("subjects", ""),
                    "book_id":     obj.get("book_id", ""),
                    "chunk":       chunk_idx,
                    "source":      "gutenberg_es",
                    "source_type": "curated",  # literary/curated, mirrors Wikipedia tag
                }
                fout.write(json.dumps(entry, ensure_ascii=False) + "\n")
                kept_chunks += 1

    print(f"  Done — running totals: "
          f"books={total_books:,} | chunks={kept_chunks:,} | "
          f"short={skipped_short:,} | lang={skipped_lang:,} | "
          f"noise={skipped_noise:,}", flush=True)

print(f"\n{'='*60}")
print(f"CLEANING COMPLETE")
print(f"  Total books processed : {total_books:,}")
print(f"  Chunks kept           : {kept_chunks:,}")
print(f"  Skipped (too short)   : {skipped_short:,}")
print(f"  Skipped (language)    : {skipped_lang:,}")
print(f"  Skipped (noisy)       : {skipped_noise:,}")
print(f"  Output directory      : {OUTPUT_DIR}")