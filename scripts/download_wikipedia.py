from datasets import load_dataset
import json
import os

OUTPUT_DIR = "data/raw/wikipedia"
PREFIX     = "spanish_wiki"
MAX_FILE_SIZE = 800 * 1024 * 1024

os.makedirs(OUTPUT_DIR, exist_ok=True)


class JsonlShardWriter:
    def __init__(self, output_dir, prefix, max_file_size, start_shard=1):
        self.output_dir = output_dir
        self.prefix = prefix
        self.max_file_size = max_file_size
        self.shard_index = start_shard
        self.file = None
        self.current_size = 0
        self._open_new_file()

    def _make_path(self):
        return os.path.join(self.output_dir, f"{self.prefix}_{self.shard_index:05d}.jsonl")

    def _open_new_file(self):
        if self.file:
            self.file.close()
        path = self._make_path()
        if os.path.exists(path):
            self.current_size = os.path.getsize(path)
            self.file = open(path, "a", encoding="utf-8")
        else:
            self.current_size = 0
            self.file = open(path, "w", encoding="utf-8")
        print(f"  Writing to: {os.path.basename(path)}", flush=True)

    def write_obj(self, obj):
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        encoded = line.encode("utf-8")
        if self.current_size + len(encoded) > self.max_file_size:
            self.shard_index += 1
            self._open_new_file()
        self.file.write(line)
        self.current_size += len(encoded)

    def flush(self): self.file and self.file.flush()
    def close(self): self.file and self.file.close()


# Clean up any broken dump files from previous attempts
dump_dir = "data/raw/wikipedia/dump"
if os.path.exists(dump_dir):
    import shutil
    shutil.rmtree(dump_dir)
    print("Removed old broken dump directory.", flush=True)

print("Loading Spanish Wikipedia via HuggingFace (wikimedia/wikipedia)...", flush=True)
print("Streaming ~1.8M articles — no local dump needed.\n", flush=True)

dataset = load_dataset(
    "wikimedia/wikipedia",
    "20231101.es",
    split="train",
    streaming=True,
)

writer  = JsonlShardWriter(OUTPUT_DIR, PREFIX, MAX_FILE_SIZE)
saved   = 0
skipped = 0

for i, doc in enumerate(dataset):
    text  = (doc.get("text")  or "").strip()
    title = (doc.get("title") or "")
    url   = (doc.get("url")   or "")

    if not text or len(text) < 300:
        skipped += 1
        continue

    entry = {
        "text":   text,
        "url":    url,
        "title":  title,
        "source": "wikipedia_es",
    }
    writer.write_obj(entry)
    saved += 1

    if i % 10_000 == 0 and i > 0:
        writer.flush()
        print(f"  {i:,} seen | {saved:,} saved | {skipped:,} skipped "
              f"| shard={writer.shard_index}", flush=True)

writer.flush()
writer.close()
print(f"\nDone! {saved:,} Wikipedia articles saved.", flush=True)