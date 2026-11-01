"""Test-only: does NLLB invent content on very short inputs, and which decoding change stops it?

    python scripts/mt_short_check.py [--max-words 2] [--variants base nostop lp0 cap] > results/mt_short_<date>.txt

Inputs: every distinct ASR line of <= --max-words words found in recordings/*/hyp.jsonl and results/*.jsonl
(what the app really printed), plus CONTROL: common correct short replies that must stay right.
Variants (same model and beam as the app):
  base   = app today (beam 4, length_penalty 1)
  nostop = trailing Armenian full stop "։" / "." removed before translation
  lp0    = length_penalty 0 (raw log-probability: shorter outputs are not penalised less)
  cap    = max_decoding_length = source pieces + 2
  app    = NllbMT.translate as the app runs it (short-input guard on, 2026-11-01)
Prints the English per variant and the base hypothesis score (avg log-prob). Judging is manual.
"""
import argparse
import glob
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from armtrans.config import MtConfig  # noqa: E402
from armtrans.translate import NllbMT, TGT_LANG, output_text, source_tokens  # noqa: E402

CONTROL = ["Այո։", "Ոչ։", "Հա։", "Չէ։", "Լավ։", "Իհարկե։", "Շնորհակալություն։", "Ճիշտ է։", "Գիտեմ։",
           "Հասկացա։", "Բարեւ։", "Ցտեսություն։", "Լսում եմ։", "Մի րոպե։", "Համաձայն եմ։"]


def collect(max_words):
    seen = {}
    for f in sorted(glob.glob(str(ROOT / "recordings/*/hyp.jsonl"))) + sorted(glob.glob(str(ROOT / "results/*.jsonl"))):
        for line in open(f, encoding="utf-8"):
            try:
                h = json.loads(line)
            except ValueError:
                continue
            hy = (h.get("hy") or "").strip()
            if hy and len(hy.split()) <= max_words:
                seen.setdefault(hy, None)
    return list(seen)


def run(mt, text, variant):
    if variant == "app":
        return mt.translate(text), None
    src_text = text.rstrip("։.:") if variant == "nostop" else text
    if not src_text.strip():
        return "", None
    src = source_tokens(mt.sp, src_text)
    kw = dict(target_prefix=[[TGT_LANG]], beam_size=mt.cfg.beam_size, return_scores=True)
    if variant == "lp0":
        kw["length_penalty"] = 0.0
    if variant == "cap":
        kw["max_decoding_length"] = len(src) + 2
    r = mt.translator.translate_batch([src], **kw)[0]
    return output_text(mt.sp, r.hypotheses[0]), r.scores[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-words", type=int, default=2)
    ap.add_argument("--variants", nargs="+", default=["base", "nostop", "lp0", "cap"])
    a = ap.parse_args()
    mt = NllbMT(MtConfig())
    mt.warm_up()
    groups = [("ASR", collect(a.max_words)), ("CONTROL", CONTROL)]
    t0 = time.perf_counter()
    n = 0
    for name, items in groups:
        print(f"===== {name} ({len(items)})")
        for text in items:
            outs = {v: run(mt, text, v) for v in a.variants}
            n += len(a.variants)
            score = outs["base"][1] if "base" in outs else None
            print(f"{text}\t" + (f"score={score:.2f}\t" if score is not None else "")
                  + "\t".join(f"{v}: {outs[v][0]}" for v in a.variants))
    print(f"\n{n} translations in {time.perf_counter() - t0:.1f} s")


if __name__ == "__main__":
    main()
