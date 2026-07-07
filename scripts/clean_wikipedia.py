import os
import json
import re
from glob import glob

INPUT_DIR  = "data/raw/wikipedia"
OUTPUT_DIR = "data/cleaned/wikipedia"
os.makedirs(OUTPUT_DIR, exist_ok=True)

SPANISH_HINTS = [" el ", " la ", " de ", " que ", " y ", " en ", " los ", " las "]

# ── Cleaners ────────────────────────────────────────────────────────────────

def normalize_text(text):
    """Basic whitespace and encoding cleanup."""
    text = text.replace("\x00", " ")
    text = text.replace("\xa0", " ")        # non-breaking space
    text = text.replace("\u200b", "")       # zero-width space
    text = re.sub(r"\s+", " ", text)        # collapse all whitespace
    return text.strip()


def remove_wiki_artifacts(text):
    """Strip remaining wikiextractor artifacts."""
    # Section headers left as plain text e.g. "== Historia =="
    text = re.sub(r"={2,}[^=]+={2,}", " ", text)
    # Reference markers like [1], [2], [nota 1]
    text = re.sub(r"\[\d+\]", "", text)
    text = re.sub(r"\[nota\s+\d+\]", "", text, flags=re.IGNORECASE)
    # File/Image links that wikiextractor sometimes leaves
    text = re.sub(r"\b(Archivo|File|Image|Imagen):[^\s]+", "", text, flags=re.IGNORECASE)
    # Bare URLs
    text = re.sub(r"https?://\S+", "", text)
    # Leftover template-style curly braces
    text = re.sub(r"\{\{[^}]*\}\}", " ", text)
    # Pipe characters from table remnants
    text = re.sub(r"\|", " ", text)
    # Collapse again after removals
    text = re.sub(r"\s+", " ", text).strip()
    return text


def looks_spanish(text):
    sample = " " + text[:3000].lower() + " "
    hits = sum(1 for w in SPANISH_HINTS if w in sample)
    return hits >= 3


def is_stub(text):
    """Wikipedia stubs and disambiguation pages are too short or list-like."""
    words = text.split()
    if len(words) < 100:
        return True
    # Disambiguation page patterns in Spanish
    disambig_patterns = [
        r"puede referirse a",
        r"puede hacer referencia a",
        r"esta página de desambiguación",
        r"los siguientes artículos",
    ]
    sample = text[:500].lower()
    if any(re.search(p, sample) for p in disambig_patterns):
        return True
    return False


def is_list_article(text):
    """
    Articles that are mostly bullet lists have low unique-word ratio
    and many short lines — not useful for language modeling.
    """
    words = text.lower().split()
    if len(words) < 50:
        return True
    unique_ratio = len(set(words)) / len(words)
    if unique_ratio < 0.40:
        return True
    # Count sentence-like structures — real prose has punctuation
    sentence_endings = len(re.findall(r"[.!?]", text))
    if sentence_endings < 5:
        return True
    return False


def is_noisy(text):
    """Flag articles with too many symbols (tables, math-heavy, etc.)."""
    symbol_ratio = sum(
        1 for c in text if not c.isalnum() and not c.isspace()
    ) / max(len(text), 1)
    if symbol_ratio > 0.25:
        return True
    # Too many numbers = likely a stats/data table article
    digit_ratio = sum(1 for c in text if c.isdigit()) / max(len(text), 1)
    if digit_ratio > 0.15:
        return True
    return False


def split_into_chunks(text, min_chunk=200, max_chunk=2000):
    """
    Wikipedia articles can be very long (10k+ words).
    Split on paragraph breaks into digestible chunks for benchmarking.
    Filter out chunks that are too short.
    """
    # Split on double newline (paragraph break) or ". " after long sentences
    paragraphs = re.split(r"\n{2,}", text)
    chunks = []
    for para in paragraphs:
        para = para.strip()
        if len(para) < min_chunk:
            continue
        # If paragraph is very long, split further on sentence boundaries
        if len(para) > max_chunk:
            sentences = re.split(r"(?<=[.!?])\s+", para)
            current = ""
            for sent in sentences:
                if len(current) + len(sent) < max_chunk:
                    current += " " + sent
                else:
                    if len(current.strip()) >= min_chunk:
                        chunks.append(current.strip())
                    current = sent
            if len(current.strip()) >= min_chunk:
                chunks.append(current.strip())
        else:
            chunks.append(para)
    return chunks


# ── Main ────────────────────────────────────────────────────────────────────

input_files = sorted(glob(os.path.join(INPUT_DIR, "*.jsonl")))

if not input_files:
    print(f"No JSONL files found in {INPUT_DIR}")
    print("Make sure download_wikipedia.py has finished running first.")
    raise SystemExit(1)

for path in input_files:
    base     = os.path.basename(path).replace(".jsonl", "_clean.jsonl")
    out_path = os.path.join(OUTPUT_DIR, base)

    total = kept = skipped_stub = skipped_noise = skipped_lang = skipped_list = 0

    print(f"\nProcessing {os.path.basename(path)}...", flush=True)

    with open(path, "r", encoding="utf-8") as fin, \
         open(out_path, "w", encoding="utf-8") as fout:

        for line in fin:
            total += 1
            line = line.strip()
            if not line:
                continue

            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            text  = obj.get("text", "")
            title = obj.get("title", "")
            url   = obj.get("url", "")

            # Step 1: normalize
            text = normalize_text(text)
            text = remove_wiki_artifacts(text)

            if not text:
                continue

            # Step 2: language check
            if not looks_spanish(text):
                skipped_lang += 1
                continue

            # Step 3: stub / disambiguation filter
            if is_stub(text):
                skipped_stub += 1
                continue

            # Step 4: list article filter
            if is_list_article(text):
                skipped_list += 1
                continue

            # Step 5: noise filter
            if is_noisy(text):
                skipped_noise += 1
                continue

            # Step 6: split into paragraph chunks for benchmarking
            # Each chunk becomes its own document — better for evaluation
            chunks = split_into_chunks(text)

            for chunk in chunks:
                entry = {
                    "text":        chunk,
                    "url":         url,
                    "title":       title,
                    "source":      "wikipedia_es",
                    "source_type": "curated",   # useful label for benchmarking
                }
                fout.write(json.dumps(entry, ensure_ascii=False) + "\n")
                kept += 1

            if total % 50_000 == 0:
                print(
                    f"  {total:,} articles | kept chunks={kept:,} | "
                    f"stubs={skipped_stub:,} | lists={skipped_list:,} | "
                    f"noise={skipped_noise:,} | lang={skipped_lang:,}",
                    flush=True
                )

    print(
        f"Done: {total:,} articles → {kept:,} chunks kept | "
        f"stubs={skipped_stub:,} lists={skipped_list:,} "
        f"noise={skipped_noise:,} lang={skipped_lang:,}",
        flush=True
    )