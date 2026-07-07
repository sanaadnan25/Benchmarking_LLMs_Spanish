from datasets import load_dataset
import json
import os

OUTPUT_DIR = "spanish_data/huggingface"
PREFIX = "spanish_hf"
CHECKPOINT_FILE = os.path.join(OUTPUT_DIR, "checkpoint.json")
SEEN_URLS_FILE = os.path.join(OUTPUT_DIR, "seen_urls.txt")

TARGET_DOCS = 10_000_000
MAX_FILE_SIZE = 800 * 1024 * 1024  # 800 MB

os.makedirs(OUTPUT_DIR, exist_ok=True)

class JsonlShardWriter:
    def __init__(self, output_dir, prefix, max_file_size, start_shard=1):
        self.output_dir = output_dir
        self.prefix = prefix
        self.max_file_size = max_file_size
        self.shard_index = start_shard
        self.file = None
        self.current_size = 0
        self.current_path = None
        self._open_new_file()

    def _make_path(self):
        return os.path.join(
            self.output_dir,
            f"{self.prefix}_{self.shard_index:05d}.jsonl"
        )

    def _open_new_file(self):
        if self.file:
            self.file.close()

        self.current_path = self._make_path()

        if os.path.exists(self.current_path):
            self.current_size = os.path.getsize(self.current_path)
            self.file = open(self.current_path, "a", encoding="utf-8")
        else:
            self.current_size = 0
            self.file = open(self.current_path, "w", encoding="utf-8")

        print(f"Writing to: {self.current_path} (current size: {self.current_size / (1024**2):.2f} MB)")

    def write_obj(self, obj):
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        encoded = line.encode("utf-8")
        line_size = len(encoded)

        if self.current_size + line_size > self.max_file_size:
            self.shard_index += 1
            self._open_new_file()

        self.file.write(line)
        self.current_size += line_size

    def flush(self):
        if self.file:
            self.file.flush()

    def close(self):
        if self.file:
            self.file.close()

# ----------------------------
# Resume state
# ----------------------------
seen_urls = set()
if os.path.exists(SEEN_URLS_FILE):
    with open(SEEN_URLS_FILE, "r", encoding="utf-8") as f:
        seen_urls = set(line.strip() for line in f if line.strip())
    print(f"Loaded {len(seen_urls):,} seen URLs")

state = {
    "processed_docs": 0,   # how many streamed docs have been iterated over
    "saved_docs": 0,       # how many docs have actually been saved
    "dupes": 0,
    "shard_index": 1
}

if os.path.exists(CHECKPOINT_FILE):
    with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
        state.update(json.load(f))
    print(
        f"Resuming: processed={state['processed_docs']:,}, "
        f"saved={state['saved_docs']:,}, dupes={state['dupes']:,}, "
        f"shard={state['shard_index']}"
    )
else:
    print("Starting fresh...")


def save_checkpoint(state_dict):
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as cp:
        json.dump(state_dict, cp, ensure_ascii=False, indent=2)


writer = JsonlShardWriter(
    output_dir=OUTPUT_DIR,
    prefix=PREFIX,
    max_file_size=MAX_FILE_SIZE,
    start_shard=state["shard_index"]
)

print("Loading mC4 Spanish stream...")
dataset = load_dataset(
    "allenai/c4",
    "es",
    split="train",
    streaming=True
)

processed_docs = state["processed_docs"]
saved_docs = state["saved_docs"]
dupes = state["dupes"]

print(f"Target: {TARGET_DOCS:,} docs total")

with open(SEEN_URLS_FILE, "a", encoding="utf-8") as url_f:
    for i, doc in enumerate(dataset):
        if i < processed_docs:
            continue

        url = doc.get("url", "")
        text = doc.get("text", "")

        # mark as processed as soon as this document is reached
        processed_docs += 1

        if url and url in seen_urls:
            dupes += 1
        elif text and len(text.strip()) > 100:
            entry = {
                "text": text,
                "url": url,
                "timestamp": str(doc.get("timestamp", ""))
            }

            writer.write_obj(entry)

            if url:
                seen_urls.add(url)
                url_f.write(url + "\n")

            saved_docs += 1

        if processed_docs % 10_000 == 0:
            writer.flush()
            url_f.flush()
            state = {
                "processed_docs": processed_docs,
                "saved_docs": saved_docs,
                "dupes": dupes,
                "shard_index": writer.shard_index
            }
            save_checkpoint(state)
            print(
                f"processed={processed_docs:,} | "
                f"saved={saved_docs:,} | dupes={dupes:,} | "
                f"current shard={writer.shard_index}"
            )

        if saved_docs >= TARGET_DOCS:
            break

writer.flush()
writer.close()

state = {
    "processed_docs": processed_docs,
    "saved_docs": saved_docs,
    "dupes": dupes,
    "shard_index": writer.shard_index
}
save_checkpoint(state)

print(f"Done! {saved_docs:,} unique documents saved. {dupes:,} duplicates skipped.")