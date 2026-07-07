"""
harvest_yt_comments_do.py
─────────────────────────
Harvest YouTube comments from Dominican channels/videos as informal,
dialect-bearing Spanish text for the Dominican corpus.

WHY THIS TIER EXISTS
────────────────────
Your .do web tier is formal Spanish that happens to be Dominican. Comments are
the opposite: this is where dialect lands in the orthography itself ("klk",
"tató", "una vaina", dropped letters, code-switching) because there's no ASR in
between normalizing it to standard spelling. Each comment is messier than a news
sentence, but it carries signal your other tiers don't have.

OUTPUT SCHEMA (matches the rest of the pipeline; flows into clean_cc_do.py →
dup_do.py → balance_do.py):
{
  "text":        "<cleaned comment>",
  "url":         "https://www.youtube.com/watch?v=VIDEO_ID",
  "video_id":    "VIDEO_ID",
  "like_count":  12,
  "source":      "youtube_comments_do",
  "source_type": "social",     # vs "web"/"curated" elsewhere
  "register":    "informal",
  "country":     "DO",
  "lang":        "es"
}

REQUIREMENTS:
  pip install yt-dlp

USAGE:
  1. Put one seed per line in dominican_seeds.txt — channel URLs or video URLs:
        https://www.youtube.com/@SomeDominicanChannel
        https://www.youtube.com/watch?v=XXXXXXXXXXX
  2. python harvest_yt_comments_do.py

NOTES
─────
* Seed Dominican-audience channels: DR news, podcasts, vlogs, comedians,
  baseball/política content. Comments are only as Dominican as the audience.
* Respect YouTube's terms and rate limits. SLEEP_BETWEEN is deliberate.
* PII (emails, phone numbers, @handles) is stripped before writing. Author
  names are never stored.
* Per-video cap (MAX_COMMENTS_PER_VIDEO) is this tier's balancing analog — it
  stops one viral video from dominating, since balance_do.py keys on domain and
  every comment shares the youtube.com domain (see note at the bottom).
"""

import os
import re
import json
import time

try:
    import yt_dlp
except ImportError:
    raise SystemExit("yt-dlp not installed. Run:  pip install yt-dlp")

# ── Configuration ─────────────────────────────────────────────────────────────

OUTPUT_DIR  = "dominican_data/raw/youtube_comments"
PREFIX      = "yt_comments_do"
SEEDS_FILE  = "dominican_seeds.txt"

CHECKPOINT_FILE = os.path.join(OUTPUT_DIR, "checkpoint.json")
SEEN_VIDEOS_FILE = os.path.join(OUTPUT_DIR, "seen_videos.txt")

MAX_FILE_SIZE          = 200 * 1024 * 1024   # 200 MB shards — comments are tiny
MAX_VIDEOS_PER_CHANNEL = 60      # how many recent videos to pull from a channel
MAX_COMMENTS_PER_VIDEO = 300     # per-video cap (balancing analog)
SLEEP_BETWEEN          = 1.5      # seconds between videos

# Lenient language gate. Short dialectal comments often contain NONE of the
# formal function words, so we DEFAULT TO KEEP and only drop text that looks
# clearly English. (The channel is Dominican; assume Spanish unless proven otherwise.)
SPANISH_HINTS = [" que ", " el ", " la ", " de ", " y ", " no ", " es ", " un ",
                 " una ", " lo ", " me ", " mi ", " tu ", " pa ", " con ", " por ",
                 " jaja", " dios ", " bien ", " ese ", " esa ", " pero "]
ENGLISH_MARKERS = [" the ", " you ", " and ", " is ", " this ", " for ", " what ",
                   " with ", " love ", " so ", " that "]

MIN_CHARS = 12     # below this it's usually an emoji or "1ro"
MIN_WORDS = 2      # keep "klk manito"; drop bare single tokens unless long

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ── Shard writer (same pattern as download_gutenberg.py / Script2.py) ─────────

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
        return os.path.join(self.output_dir,
                            f"{self.prefix}_{self.shard_index:05d}.jsonl")

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
        print(f"  Shard → {os.path.basename(path)} "
              f"({self.current_size / 1024:.1f} KB)", flush=True)

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


# ── PII stripping ─────────────────────────────────────────────────────────────

EMAIL_RE  = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
URL_RE    = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)
# DR mobiles (+1 809/829/849) and general 7–10 digit groupings
PHONE_RE  = re.compile(r"(\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}")
HANDLE_RE = re.compile(r"@[A-Za-z0-9_.]{2,}")


def strip_pii(text: str) -> str:
    text = URL_RE.sub(" ", text)
    text = EMAIL_RE.sub(" ", text)
    text = PHONE_RE.sub(" ", text)
    text = HANDLE_RE.sub("@usuario", text)   # keep the syntactic slot, drop identity
    return text


# ── Comment cleaning / filtering ──────────────────────────────────────────────

def clean_comment(text: str) -> str:
    text = text.replace("\x00", " ").replace("\xa0", " ").replace("\u200b", "")
    text = strip_pii(text)
    text = re.sub(r"\s+", " ", text).strip()     # collapse newlines/runs
    return text


def keep_language(text: str) -> bool:
    low = " " + text.lower() + " "
    if any(h in low for h in SPANISH_HINTS):
        return True
    # No Spanish marker found — only reject if it looks clearly English
    if sum(1 for m in ENGLISH_MARKERS if m in low) >= 2:
        return False
    return True   # default keep: the channel is Dominican


def keep_comment(text: str) -> bool:
    if len(text) < MIN_CHARS:
        return False
    if len(text.split()) < MIN_WORDS and len(text) < 20:
        return False
    # letter ratio guard: drops pure-emoji / number / symbol spam
    letters = sum(1 for c in text if c.isalpha())
    if letters / max(len(text), 1) < 0.5:
        return False
    return keep_language(text)


# ── Video enumeration ─────────────────────────────────────────────────────────

def collect_video_urls(seed: str, ydl, limit: int) -> list[str]:
    """
    Resolve a seed (channel, playlist, or single video) into a flat list of
    watch URLs without downloading anything.
    """
    if "watch?v=" in seed or "/shorts/" in seed:
        return [seed]

    try:
        info = ydl.extract_info(seed, download=False, process=False)
    except Exception as e:
        print(f"  ! could not resolve seed {seed}: {e}", flush=True)
        return []

    urls = []

    def walk(node):
        if len(urls) >= limit:
            return
        if not isinstance(node, dict):
            return
        entries = node.get("entries")
        if entries is not None:
            for e in entries:
                if len(urls) >= limit:
                    break
                walk(e)
            return
        vid = node.get("id") or node.get("url")
        if vid:
            if vid.startswith("http"):
                urls.append(vid)
            else:
                urls.append(f"https://www.youtube.com/watch?v={vid}")

    walk(info)
    return urls[:limit]


def fetch_comments(video_url: str, ydl) -> tuple[str, list[dict]]:
    """Return (video_id, comment dicts) for a single video, or ("", [])."""
    try:
        info = ydl.extract_info(video_url, download=False)
    except Exception as e:
        print(f"  ! comment fetch failed for {video_url}: {e}", flush=True)
        return "", []
    return info.get("id", ""), (info.get("comments") or [])


# ── Main ──────────────────────────────────────────────────────────────────────

if not os.path.exists(SEEDS_FILE):
    raise SystemExit(
        f"No seeds file at {SEEDS_FILE}. Create it with one channel/video URL "
        f"per line (DR news, podcasts, vlogs, comedians, baseball/política)."
    )

with open(SEEDS_FILE, "r", encoding="utf-8") as f:
    seeds = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]

# Resume state
seen_videos = set()
if os.path.exists(SEEN_VIDEOS_FILE):
    with open(SEEN_VIDEOS_FILE, "r", encoding="utf-8") as f:
        seen_videos = {ln.strip() for ln in f if ln.strip()}
    print(f"Loaded {len(seen_videos):,} already-processed video IDs", flush=True)

state = {"saved_comments": 0, "videos_done": 0, "shard_index": 1}
if os.path.exists(CHECKPOINT_FILE):
    with open(CHECKPOINT_FILE) as f:
        state.update(json.load(f))
    print(f"Resuming: saved={state['saved_comments']:,} "
          f"videos={state['videos_done']:,}", flush=True)

# yt-dlp options. We cap comments at the extractor level (with headroom for
# filtering) and sort by 'top' — top comments are more representative and
# less spammy than newest.
ydl_opts = {
    "skip_download": True,
    "quiet": True,
    "no_warnings": True,
    "getcomments": True,
    "extractor_args": {
        "youtube": {
            "comment_sort": ["top"],
            # max-comments, max-parents, max-replies, max-replies-per-thread
            "max_comments": [str(MAX_COMMENTS_PER_VIDEO * 2), "all", "all", "20"],
        }
    },
}

writer = JsonlShardWriter(OUTPUT_DIR, PREFIX, MAX_FILE_SIZE, state["shard_index"])
saved   = state["saved_comments"]
vids    = state["videos_done"]

# A lightweight ydl for flat enumeration (no comments)
flat_opts = {"skip_download": True, "quiet": True, "no_warnings": True,
             "extract_flat": "in_playlist"}

with yt_dlp.YoutubeDL(flat_opts) as flat_ydl, \
     yt_dlp.YoutubeDL(ydl_opts) as ydl, \
     open(SEEN_VIDEOS_FILE, "a", encoding="utf-8") as seen_f:

    for seed in seeds:
        print(f"\nSeed: {seed}", flush=True)
        video_urls = collect_video_urls(seed, flat_ydl, MAX_VIDEOS_PER_CHANNEL)
        print(f"  {len(video_urls)} video(s) to scan", flush=True)

        for vurl in video_urls:
            vid_guess = vurl.split("watch?v=")[-1].split("&")[0]
            if vid_guess in seen_videos:
                continue

            video_id, comments = fetch_comments(vurl, ydl)
            time.sleep(SLEEP_BETWEEN)

            if not video_id:
                continue
            if video_id in seen_videos:
                continue

            kept_here = 0
            canonical = f"https://www.youtube.com/watch?v={video_id}"

            for c in comments:
                if kept_here >= MAX_COMMENTS_PER_VIDEO:
                    break
                raw = c.get("text", "") or ""
                text = clean_comment(raw)
                if not keep_comment(text):
                    continue

                writer.write_obj({
                    "text":        text,
                    "url":         canonical,
                    "video_id":    video_id,
                    "like_count":  c.get("like_count", 0),
                    "source":      "youtube_comments_do",
                    "source_type": "social",
                    "register":    "informal",
                    "country":     "DO",
                    "lang":        "es",
                })
                kept_here += 1

            saved += kept_here
            vids  += 1
            seen_videos.add(video_id)
            seen_f.write(video_id + "\n")
            seen_f.flush()
            writer.flush()

            print(f"  [{vids}] {video_id}: kept {kept_here:,} "
                  f"(total {saved:,})", flush=True)

            with open(CHECKPOINT_FILE, "w", encoding="utf-8") as cp:
                json.dump({"saved_comments": saved, "videos_done": vids,
                           "shard_index": writer.shard_index}, cp, indent=2)

writer.flush()
writer.close()

print(f"\n{'='*55}")
print(f"HARVEST COMPLETE")
print(f"  Videos processed : {vids:,}")
print(f"  Comments saved   : {saved:,}")
print(f"  Output dir       : {OUTPUT_DIR}")
print(f"{'='*55}")
print("\nNext: this raw tier flows into your cleaning chain. But note the")
print("400-char minimum in clean_cc_do.py will delete almost every comment —")
print("see the message accompanying this script for the one tweak you need.")
