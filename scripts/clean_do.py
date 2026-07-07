import os
import json
import re
from glob import glob

# Dominican pipeline — reads the fresh .do crawl produced by dominican.py
INPUT_DIR = "dominican_data/raw/commoncrawl"
OUTPUT_DIR = "dominican_data/cleaned"
os.makedirs(OUTPUT_DIR, exist_ok=True)

SPANISH_HINTS = [" el ", " la ", " de ", " que ", " y ", " en ", " los ", " las "]

def normalize_text(text):
    text = text.replace("\x00", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text

def looks_spanish(text):
    sample = " " + text[:3000].lower() + " "
    hits = sum(1 for w in SPANISH_HINTS if w in sample)
    return hits >= 3

def is_boilerplate(text):
    words = text.lower().split()

    if len(words) < 100:
        return True

    # too repetitive
    unique_ratio = len(set(words)) / len(words)
    if unique_ratio < 0.5:
        return True

    # too many repeated phrases like "traducción", "servicios"
    common_spam_words = [
        "traducción", "servicios", "clientes", "empresa",
        "contacto", "presupuesto", "gratis"
    ]

    spam_hits = sum(text.lower().count(w) for w in common_spam_words)
    if spam_hits > 20:
        return True

    return False


def looks_noisy(text):
    # too many symbols = garbage
    symbol_ratio = sum(1 for c in text if not c.isalnum() and not c.isspace()) / max(len(text), 1)
    if symbol_ratio > 0.3:
        return True

    # too many short repeated segments
    if text.count("...") > 5:
        return True

    return False

for path in sorted(glob(os.path.join(INPUT_DIR, "*.jsonl"))):
    out_path = os.path.join(OUTPUT_DIR, os.path.basename(path))

    kept = 0
    total = 0

    with open(path, "r", encoding="utf-8") as fin, open(out_path, "w", encoding="utf-8") as fout:
        for line in fin:
            total += 1
            try:
                obj = json.loads(line)
            except:
                continue

            text = normalize_text(obj.get("text", ""))

            if len(text) < 400:
                continue

            if not looks_spanish(text):
                continue

            if is_boilerplate(text):
                continue

            obj["text"] = text
            fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            kept += 1

    print(f"{path}: kept {kept}/{total}")