"""
bpb_eval.py
===========
Compute bits-per-byte (BPB), perplexity, and tokenizer fertility for one or more
GGUF models on a plain-text evaluation file, using llama.cpp's llama-perplexity.

BPB is the cross-model metric: it normalizes the model's negative log-likelihood
by the BYTES of the text instead of by tokens, so it is comparable across models
with different tokenizers. Formula (Gao et al., 2020; Biderman et al., 2024):

        BPB = (N_tokens / N_bytes) * log2(PPL)

where N_tokens is the number of tokens scored and N_bytes is the UTF-8 byte
length of the evaluation text. Lower BPB = better fit.

Is BPB native to llama.cpp?  No. `llama-perplexity` natively reports PERPLEXITY
(and related diagnostics), not BPB. This script derives BPB from the reported
perplexity and the token/byte counts. For a fully native BPB implementation,
EleutherAI's lm-evaluation-harness provides a `bits_per_byte` metric
(Biderman et al., 2024); it requires a PyTorch/transformers stack rather than a
GGUF runtime, so this llama.cpp-based script is the lightweight server option.

------------------------------------------------------------------------------
REQUIREMENTS
  - llama.cpp binaries (provides llama-perplexity[.exe])
  - one or more GGUF models
  - a plain-text eval file (one document/comment per line)
  Pure standard-library Python; no ML dependencies.

EXAMPLE (Windows)
  python bpb_eval.py ^
    --perplexity-exe C:\Users\research\llamacpp\llama-perplexity.exe ^
    --eval-file C:\Users\research\dominican\dominican_eval.txt ^
    --model qwen2.5-7b=C:\Users\research\models\qwen2.5-7b-q4_k_m-imat.gguf ^
    --model salamandra-2b=C:\Users\research\models\salamandra-2b.Q4_K_M.gguf ^
    --ngl 99 --csv results.csv

EXAMPLE (Linux/macOS server)
  python bpb_eval.py \
    --perplexity-exe ./llama.cpp/build/bin/llama-perplexity \
    --eval-file ./data/dominican_eval.txt \
    --model qwen2.5-7b=./models/qwen2.5-7b.gguf \
    --model salamandra-2b=./models/salamandra-2b.gguf \
    --ngl 99 --csv results.csv

NOTES / CAVEATS
  - Token count is taken from llama-perplexity's "over N chunks, n_ctx=M" line
    (tokens = N * M = the tokens actually scored). Up to (n_ctx - 1) trailing
    tokens of the file are not scored; for typical eval files this is <1% and
    affects all models similarly. For a publication-grade number, cross-check
    with lm-evaluation-harness's native bits_per_byte.
  - Use BASE (not instruct/chat) checkpoints for clean language-model perplexity.
  - Lower --ngl (e.g. 0) if a model does not fit in VRAM; CPU is slower but fine
    for small eval files.
"""

import argparse
import subprocess
import re
import os
import math
import sys


def parse_args():
    p = argparse.ArgumentParser(description="Compute BPB / PPL / fertility for GGUF models.")
    p.add_argument("--perplexity-exe", required=True,
                   help="Path to llama-perplexity[.exe]")
    p.add_argument("--eval-file", required=True,
                   help="Plain-text eval file (one document per line)")
    p.add_argument("--model", action="append", required=True, metavar="NAME=PATH",
                   help="Model as NAME=PATH (repeatable). PATH is a .gguf file.")
    p.add_argument("--ngl", type=int, default=99, help="GPU layers to offload (0=CPU)")
    p.add_argument("--ctx", type=int, default=512, help="Context window for scoring")
    p.add_argument("--csv", default=None, help="Optional path to write results as CSV")
    return p.parse_args()


def run_perplexity(exe, model_path, eval_file, ngl, ctx):
    cmd = [exe, "-m", model_path, "-f", eval_file, "-ngl", str(ngl), "-c", str(ctx)]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    out = proc.stdout + "\n" + proc.stderr

    m_ppl = re.search(r"PPL\s*=\s*([0-9.]+)", out)
    m_chunks = re.search(r"over\s+([0-9]+)\s+chunks", out)
    m_nctx = re.search(r"n_ctx\s*=\s*([0-9]+)", out)

    if not m_ppl:
        return None, None, out
    ppl = float(m_ppl.group(1))
    tokens = (int(m_chunks.group(1)) * int(m_nctx.group(1))) if (m_chunks and m_nctx) else None
    return ppl, tokens, out


def main():
    args = parse_args()

    if not os.path.exists(args.perplexity_exe):
        sys.exit(f"llama-perplexity not found: {args.perplexity_exe}")
    if not os.path.exists(args.eval_file):
        sys.exit(f"eval file not found: {args.eval_file}")

    with open(args.eval_file, "r", encoding="utf-8") as f:
        text = f.read()
    eval_words = len(text.split())
    eval_bytes = len(text.encode("utf-8"))
    print(f"Eval file: {args.eval_file}")
    print(f"  {eval_bytes:,} bytes, {eval_words:,} words\n", flush=True)

    models = {}
    for spec in args.model:
        if "=" not in spec:
            sys.exit(f"--model must be NAME=PATH, got: {spec}")
        name, path = spec.split("=", 1)
        models[name] = path

    results = {}
    for name, path in models.items():
        if not os.path.exists(path):
            print(f"  !! {name}: model not found -> {path}\n")
            continue
        print(f"Scoring {name} ...", flush=True)
        ppl, tokens, out = run_perplexity(args.perplexity_exe, path,
                                          args.eval_file, args.ngl, args.ctx)
        if ppl is None:
            print(f"  !! {name}: could not parse PPL. Last lines:")
            print("\n".join(out.strip().splitlines()[-10:]) + "\n")
            continue
        if tokens:
            bpb = (tokens / eval_bytes) * (math.log(ppl) / math.log(2))
            fertility = tokens / eval_words
        else:
            bpb = fertility = float("nan")
            print(f"  (note: token count not found; BPB/fertility unavailable)")
        results[name] = {"ppl": ppl, "bpb": bpb, "fertility": fertility, "tokens": tokens}
        print(f"  PPL={ppl:.3f}  BPB={bpb:.4f}  fertility={fertility:.3f}  tokens={tokens}\n",
              flush=True)

    if not results:
        sys.exit("No models scored.")

    # ranking table (lower BPB = better)
    print("=" * 78)
    print(f"{'MODEL':<30}{'PPL':>10}{'BPB':>10}{'FERTILITY':>12}{'TOKENS':>14}")
    print(f"{'(rank on BPB, lower=better)':<30}{'':>10}{'<<<':>10}{'tok/word':>12}{'':>14}")
    print("-" * 78)
    ranked = sorted(results.items(),
                    key=lambda kv: (kv[1]['bpb'] if not math.isnan(kv[1]['bpb']) else kv[1]['ppl']))
    for name, r in ranked:
        tok = f"{r['tokens']:,}" if r['tokens'] else "n/a"
        print(f"{name:<30}{r['ppl']:>10.3f}{r['bpb']:>10.4f}{r['fertility']:>12.3f}{tok:>14}")
    print("=" * 78)

    if args.csv:
        with open(args.csv, "w", encoding="utf-8") as f:
            f.write("model,ppl,bpb,fertility,tokens\n")
            for name, r in ranked:
                f.write(f"{name},{r['ppl']:.4f},{r['bpb']:.6f},"
                        f"{r['fertility']:.4f},{r['tokens']}\n")
        print(f"\nWrote {args.csv}")


if __name__ == "__main__":
    main()
