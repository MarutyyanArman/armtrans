"""Compare translation models on the same Armenian sentences (PLAN.md step 5: improve the weakest part).

  python scripts/compare_mt.py                     # all models found in models/
  python scripts/compare_mt.py --models opus nllb-600m --beam 2 4

Input: eval/mt_8.tsv  (id, hy_ref = what was said, hy_asr = what the ASR produced, en_ref = reference English).
The en_ref column of eval/mt_8.tsv was written by Claude on 2026-10-31; the owner should check it.

For each model it translates both hy_ref (pure translation quality) and hy_asr (what you'd see live),
prints every output, chrF against en_ref, and per-sentence time (after a warm-up).
Writes results/compare-mt-<timestamp>.json. All numbers are measured in this run.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from armtrans.config import MODELS_DIR, MtConfig  # noqa: E402
from armtrans.metrics import chrf  # noqa: E402

NLLB_DIRS = {"nllb-600m": "nllb-600m-ct2", "nllb-1.3b": "nllb-1.3b-ct2"}


class Opus:
    """OPUS-MT hy-en (CTranslate2), kept here only so the comparison stays reproducible (removed from the app)."""

    def __init__(self, threads: int, beam: int):
        import ctranslate2
        import sentencepiece as spm
        d = MODELS_DIR / "opus-mt-hy-en-ct2"
        if not (d / "model.bin").exists():
            raise SystemExit(f"{d} not found (OPUS-MT was removed from the laptop on 2026-11-01; results in DECISIONS.md)")
        self.tr = ctranslate2.Translator(str(d), device="cpu", compute_type="int8", intra_threads=threads)
        self.src = spm.SentencePieceProcessor(model_file=str(d / "source.spm"))
        self.tgt = spm.SentencePieceProcessor(model_file=str(d / "target.spm"))
        self.beam = beam

    def translate(self, text: str) -> str:
        res = self.tr.translate_batch([self.src.encode(text, out_type=str) + ["</s>"]], beam_size=self.beam)
        return self.tgt.decode([t for t in res[0].hypotheses[0] if t != "</s>"])


def load(name: str, threads: int, beam: int):
    """Uses the app's own NLLB code (armtrans/translate.py), so results match what the app prints."""
    if name == "opus":
        return Opus(threads, beam)
    from armtrans.translate import NllbMT
    if not (MODELS_DIR / NLLB_DIRS[name] / "model.bin").exists():
        raise SystemExit(f"{MODELS_DIR / NLLB_DIRS[name]} not found (nllb-600m was removed on 2026-11-01)")
    return NllbMT(MtConfig(model_dir=NLLB_DIRS[name], intra_threads=threads, beam_size=beam))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "eval" / "mt_8.tsv"))
    ap.add_argument("--models", nargs="+", default=None, help="opus nllb-600m nllb-1.3b (default: all present)")
    ap.add_argument("--beam", nargs="+", type=int, default=[2])
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args()

    lines = Path(a.data).read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    rows = [dict(zip(header, l.split("\t"))) for l in lines[1:] if l.strip()]
    present = (["opus"] if (MODELS_DIR / "opus-mt-hy-en-ct2" / "model.bin").exists() else []) + \
        [k for k, d in NLLB_DIRS.items() if (MODELS_DIR / d / "model.bin").exists()]
    models = a.models or present
    results = []
    for name in models:
        for beam in a.beam:
            t0 = time.perf_counter()
            m = load(name, a.threads, beam)
            load_s = time.perf_counter() - t0
            m.translate("Բարև")                       # warm-up
            print(f"\n=== {name}  beam={beam}  threads={a.threads}  load={load_s:.1f}s")
            res = {"model": name, "beam": beam, "threads": a.threads, "load_s": load_s}
            for col in ("hy_ref", "hy_asr"):
                outs, times = [], []
                for r in rows:
                    t1 = time.perf_counter()
                    outs.append(m.translate(r[col]))
                    times.append(time.perf_counter() - t1)
                score = chrf([r["en_ref"] for r in rows], outs)
                res[col] = {"chrf": score, "time_max_s": max(times), "time_mean_s": sum(times) / len(times),
                            "outputs": outs}
                print(f"  input={col}: chrF={score:.1f}  time mean={res[col]['time_mean_s']:.3f}s "
                      f"max={res[col]['time_max_s']:.3f}s")
                for r, o in zip(rows, outs):
                    print(f"    {r['id']}. {o}")
            results.append(res)
            del m

    print("\nSummary (chrF on exact text / on ASR output, worst time per sentence):")
    for res in results:
        print(f"  {res['model']:10s} beam={res['beam']}  {res['hy_ref']['chrf']:5.1f} / {res['hy_asr']['chrf']:5.1f}   "
              f"max {max(res['hy_ref']['time_max_s'], res['hy_asr']['time_max_s']):.2f}s")
    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"compare-mt-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"date": datetime.now().isoformat(), "data": a.data, "results": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
