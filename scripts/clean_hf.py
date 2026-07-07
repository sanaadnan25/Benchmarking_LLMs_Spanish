import os
import json
import re
from glob import glob

INPUT_DIR = "data/raw/huggingface"
OUTPUT_DIR = "data/cleaned/huggingface"
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

def looks_noisy(text):
    if len(text) < 300:
        return True

    symbol_ratio = sum(
        1 for c in text if not c.isalnum() and not c.isspace()
    ) / max(len(text), 1)

    if symbol_ratio > 0.30:
        return True

    if text.count("...") > 5:
        return True

    return False

def is_boilerplate(text):
    words = text.lower().split()

    if len(words) < 80:
        return True

    unique_ratio = len(set(words)) / len(words)
    if unique_ratio < 0.45:
        return True

    spam_words = [
        "cookies", "privacidad", "suscríbete", "anuncio",
        "publicidad", "registrarte", "iniciar sesión"
    ]
    spam_hits = sum(text.lower().count(w) for w in spam_words)

    return spam_hits > 20

for path in sorted(glob(os.path.join(INPUT_DIR, "*.jsonl"))):
    base = os.path.basename(path).replace(".jsonl", "_clean.jsonl")
    out_path = os.path.join(OUTPUT_DIR, base)

    total = 0
    kept = 0

    with open(path, "r", encoding="utf-8") as fin, open(out_path, "w", encoding="utf-8") as fout:
        for line in fin:
            total += 1
            try:
                obj = json.loads(line)
            except Exception:
                continue

            text = normalize_text(obj.get("text", ""))
            if not text:
                continue

            if not looks_spanish(text):
                continue

            if looks_noisy(text):
                continue

            if is_boilerplate(text):
                continue

            obj["text"] = text
            obj["source_dataset"] = "huggingface_mc4_es"

            fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            kept += 1

    print(f"{path}: kept {kept}/{total}")