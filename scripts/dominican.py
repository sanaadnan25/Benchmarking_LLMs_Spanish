import requests
import json
import os
import time
from urllib.parse import quote_plus
from warcio.archiveiterator import ArchiveIterator
from bs4 import BeautifulSoup

OUTPUT_DIR = "dominican_data/raw/commoncrawl"   
PREFIX     = "spanish_cc_do_text"
CHECKPOINT_FILE = os.path.join(OUTPUT_DIR, "checkpoint.json")
SEEN_URLS_FILE = os.path.join(OUTPUT_DIR, "seen_urls.txt")

MAX_FILE_SIZE = 800 * 1024 * 1024  # 800 MB
TARGET_DOCS = 5_000_000

os.makedirs(OUTPUT_DIR, exist_ok=True)

CRAWLS = [
    "CC-MAIN-2024-51",
    "CC-MAIN-2024-42",
    "CC-MAIN-2024-33",
    "CC-MAIN-2024-22",
    "CC-MAIN-2024-10",
    "CC-MAIN-2023-50",
    "CC-MAIN-2023-40",
    "CC-MAIN-2023-23",
]

DOMAINS = [
    "*.do/*",
    "*.com.do/*",
    "*.gob.do/*",
    "*.edu.do/*",
    "*.org.do/*",
    "*.net.do/*",
]


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

        print(f"Writing to: {self.current_path} ({self.current_size / (1024**2):.2f} MB)")

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


def save_checkpoint(state):
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def search_index(crawl, domain):
    encoded = quote_plus(domain)
    url = f"http://index.commoncrawl.org/{crawl}-index?url={encoded}&output=json&limit=100000"

    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200 and r.text.strip():
            return [json.loads(line) for line in r.text.strip().split("\n")]
    except Exception as e:
        print(f"Index error for {crawl} {domain}: {e}")

    return []


def fetch_raw_html(record):
    try:
        offset = int(record["offset"])
        length = int(record["length"])
        file_url = "https://data.commoncrawl.org/" + record["filename"]
        headers = {"Range": f"bytes={offset}-{offset+length-1}"}

        r = requests.get(file_url, headers=headers, stream=True, timeout=60)
        if r.status_code == 206:
            for warc_record in ArchiveIterator(r.raw):
                if warc_record.rec_type == "response":
                    content = warc_record.content_stream().read()
                    if content:
                        return content.decode("utf-8", "ignore")
    except Exception:
        pass

    return None


def html_to_text(html):
    try:
        soup = BeautifulSoup(html, "lxml")

        # remove junk
        for tag in soup(["script", "style", "noscript", "svg", "iframe", "header", "footer"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)

        # normalize whitespace
        text = " ".join(text.split())

        return text
    except Exception:
        return ""


# ----------------------------
# Resume state
# ----------------------------
seen_urls = set()
if os.path.exists(SEEN_URLS_FILE):
    with open(SEEN_URLS_FILE, "r", encoding="utf-8") as f:
        seen_urls = set(line.strip() for line in f if line.strip())
    print(f"Loaded {len(seen_urls):,} seen URLs")

state = {
    "saved_docs": 0,
    "attempted": 0,
    "crawl_index": 0,
    "domain_index": 0,
    "record_index": 0,
    "shard_index": 1
}

if os.path.exists(CHECKPOINT_FILE):
    with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
        state.update(json.load(f))
    print(f"Resuming from checkpoint: {state}")
else:
    print("Starting fresh...")

writer = JsonlShardWriter(
    output_dir=OUTPUT_DIR,
    prefix=PREFIX,
    max_file_size=MAX_FILE_SIZE,
    start_shard=state["shard_index"]
)

saved_docs = state["saved_docs"]
attempted = state["attempted"]

print(f"Starting Common Crawl Spanish text download. Target: {TARGET_DOCS:,} docs")

with open(SEEN_URLS_FILE, "a", encoding="utf-8") as url_f:
    for crawl_i in range(state["crawl_index"], len(CRAWLS)):
        crawl = CRAWLS[crawl_i]

        start_domain = state["domain_index"] if crawl_i == state["crawl_index"] else 0

        for domain_i in range(start_domain, len(DOMAINS)):
            domain = DOMAINS[domain_i]

            if saved_docs >= TARGET_DOCS:
                break

            print(f"\nSearching {crawl} for {domain}...")
            records = search_index(crawl, domain)
            print(f"Found {len(records):,} records")

            start_record = state["record_index"] if (
                crawl_i == state["crawl_index"] and domain_i == state["domain_index"]
            ) else 0

            for record_i in range(start_record, len(records)):
                if saved_docs >= TARGET_DOCS:
                    break

                record = records[record_i]
                url = record.get("url", "")

                if not url or url in seen_urls:
                    continue

                seen_urls.add(url)
                url_f.write(url + "\n")
                attempted += 1

                html = fetch_raw_html(record)

                if attempted % 100 == 0:
                    print(f"Attempted: {attempted:,} | Saved: {saved_docs:,} | Last URL: {url[:70]}")

                if html:
                    text = html_to_text(html)

                    if len(text.strip()) > 200:
                        entry = {
                            "url": url,
                            "crawl": crawl,
                            "text": text
                        }
                        writer.write_obj(entry)
                        saved_docs += 1

                        if saved_docs % 1000 == 0:
                            print(f"✓ {saved_docs:,} pages saved")

                if attempted % 100 == 0:
                    writer.flush()
                    url_f.flush()
                    save_checkpoint({
                        "saved_docs": saved_docs,
                        "attempted": attempted,
                        "crawl_index": crawl_i,
                        "domain_index": domain_i,
                        "record_index": record_i + 1,
                        "shard_index": writer.shard_index
                    })

                time.sleep(0.2)

            state["record_index"] = 0

        state["domain_index"] = 0

writer.flush()
writer.close()

save_checkpoint({
    "saved_docs": saved_docs,
    "attempted": attempted,
    "crawl_index": len(CRAWLS),
    "domain_index": 0,
    "record_index": 0,
    "shard_index": writer.shard_index
})

print(f"\nDone! {saved_docs:,} text documents saved.")