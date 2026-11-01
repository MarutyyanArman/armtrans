"""PLAN.md step 1: measure speed (and accuracy if references exist) on THIS laptop.

  python scripts/benchmark.py --clips eval/fleurs_hy/clean --asr rnnt --show   (after scripts/make_eval_sets.py)
  python scripts/benchmark.py --clips recordings/rec-A/asr_test recordings/rec-B/asr_test --no-mt --show

A clips folder holds WAV files (phrase-sized clips are most app-like; FLEURS sentences are 5-29 s).
Optional <clips dir>/refs.tsv: <file.wav><TAB><Armenian reference><TAB><English reference (may be empty)>
Several --clips folders can be given (e.g. asr_test/ of several recordings, see scripts/export_clips.py --asr).
Clip names starting with mic_ / sys_ also get WER/CER per source. Only clips listed in refs.tsv are scored.

Prints a table and writes results/benchmark-<timestamp>.json. Nothing is estimated:
every number printed was measured in this run. Close other heavy apps, or deliberately
keep a call app open to measure under realistic load, and say which in DECISIONS.md.
"""
import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import soxr  # noqa: E402

from armtrans import SAMPLE_RATE  # noqa: E402
from armtrans.config import AsrConfig, MtConfig  # noqa: E402
from armtrans.metrics import cer, chrf, wer  # noqa: E402

# --asr presets: name -> (folder under models/, onnx-asr model type)
ASR_MODELS = {
    "ctc": ("fastconformer-hy-ctc", "nemo-conformer-ctc"),      # current app model (NVIDIA hy, CTC branch)
    "rnnt": ("fastconformer-hy-rnnt", "nemo-conformer-rnnt"),   # same NVIDIA model, RNNT branch (Vana-Labs export)
    "v2rnnt": ("fastconformer-arm-nopc-rnnt", "nemo-conformer-rnnt"),  # mheryerznka no_pc (YSU), RNNT, own export
    "v2ctc": ("fastconformer-arm-nopc-ctc", "nemo-conformer-ctc"),      # same model, CTC branch
    "omni": ("omnilingual-ctc-300m", "omnilingual-ctc"),         # Meta Omnilingual ASR CTC 300M v2, ONNX fp32
    "omni8": ("omnilingual-ctc-300m", "omnilingual-ctc-int8"),   # same, int8
    "omnihy": ("omnilingual-ctc-300m", "omnilingual-ctc-hy"),    # fp32, output limited to Armenian letters + space
}


class OmniCTC:
    """Test-only runner for OpenVoiceOS/omnilingual-asr-ctc-300m-onnx (onnx-asr 0.12 has no such model type).
    Graph per its README: input x [batch, samples] raw 16 kHz -> logits [batch, frames, 10288]; blank = id 0;
    tokens.txt "<token> <id>" with a real space token. Greedy CTC: argmax, merge repeats, drop blanks.
    Not language-conditioned: may answer in another script."""

    def __init__(self, folder: Path, threads: int, int8: bool, armenian_only: bool = False):
        import re
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        f = folder / ("model.int8.onnx" if int8 else "model.onnx")
        if not f.exists():
            sys.exit(f"Missing {f}")
        self.sess = ort.InferenceSession(str(f), so, providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name
        self.tok = {}
        for line in (folder / "tokens.txt").read_text(encoding="utf-8").splitlines():
            m = re.match(r"^(.*) (\d+)$", line)
            self.tok[int(m.group(2))] = m.group(1)
        self.mask = None
        if armenian_only:       # allowed: blank, space, tokens made only of Armenian script (U+0531-U+058F)
            ok = [i for i, t in self.tok.items()
                  if i == 0 or t == " " or (t and all("\u0531" <= c <= "\u058F" for c in t))]
            self.mask = np.full(len(self.tok), -np.inf, np.float32)
            self.mask[ok] = 0.0

    def transcribe(self, audio):
        logits = self.sess.run(None, {self.inp: audio.astype(np.float32)[None, :]})[0][0]
        if self.mask is not None:
            logits = logits + self.mask[: logits.shape[-1]]
        ids = logits.argmax(-1)
        out, prev = [], -1
        for i in ids:
            if i != prev and i != 0 and not self.tok.get(int(i), "").startswith("<"):
                out.append(self.tok[int(i)])
            prev = i
        return " ".join("".join(out).split())

    def warm_up(self):
        self.transcribe((np.random.default_rng(0).standard_normal(16000) * 0.01).astype(np.float32))
TARGET_RTF = 0.33          # PLAN.md section 5 (derived target, not a measurement)
TARGET_PHRASE_S = 2.0      # processing budget for one max-length phrase


def load_clip(path: Path) -> np.ndarray:
    a, sr = sf.read(str(path), dtype="float32", always_2d=True)
    a = a.mean(axis=1)
    return soxr.resample(a, sr, SAMPLE_RATE).astype(np.float32) if sr != SAMPLE_RATE else a


def load_refs(clips_dir: Path) -> dict:
    refs = {}
    f = clips_dir / "refs.tsv"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            refs[parts[0]] = {"hy": parts[1] if len(parts) > 1 else "", "en": parts[2] if len(parts) > 2 else ""}
    return refs


def bench_vad(clips):
    from armtrans.capture import CHUNK
    from armtrans.vad import SileroVAD
    vad = SileroVAD()
    n, t0 = 0, time.perf_counter()
    for a in clips.values():
        for i in range(0, len(a) - CHUNK + 1, CHUNK):
            vad(a[i:i + CHUNK])
            n += 1
    return 1000 * (time.perf_counter() - t0) / max(n, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", nargs="+", default=[str(ROOT / "eval" / "fleurs_hy" / "clean")],
                    help="folders of WAV clips (+ refs.tsv); default: FLEURS set from scripts/make_eval_sets.py")
    ap.add_argument("--threads", nargs="+", type=int, default=[4], help="ASR thread counts to compare")
    ap.add_argument("--asr", nargs="+", default=["ctc"], choices=sorted(ASR_MODELS), help="ASR models to compare")
    ap.add_argument("--no-mt", action="store_true")
    ap.add_argument("--show", action="store_true", help="print each clip's Armenian and English output")
    a = ap.parse_args()

    clips, refs = {}, {}
    for d in map(Path, a.clips):
        files = sorted(d.glob("*.wav"))
        if not files:
            sys.exit(f"No .wav files in {d}")
        tag = d.parent.name + "/" if len(a.clips) > 1 else ""      # keep names unique across recordings
        clips.update({tag + f.name: load_clip(f) for f in files})
        refs.update({tag + k: v for k, v in load_refs(d).items()})
    clips_dir = ", ".join(a.clips)
    total_audio = sum(len(x) for x in clips.values()) / SAMPLE_RATE
    print(f"{len(clips)} clips, {total_audio:.1f}s audio, refs for {len(refs)} clips")
    print(f"Machine: {platform.processor() or platform.machine()} | {os.cpu_count()} logical CPUs | {platform.platform()}")
    print(f"Silero VAD: {bench_vad(clips):.2f} ms per 32 ms chunk\n")

    mt = None
    if not a.no_mt:
        from armtrans.translate import NllbMT
        mt = NllbMT(MtConfig())
        mt.warm_up()

    from armtrans.asr import FastConformerASR
    results = []
    import itertools
    for (size, th) in itertools.product(a.asr, a.threads):
        folder, kind = ASR_MODELS[size]
        t0 = time.perf_counter()
        if kind.startswith("omnilingual"):
            from armtrans.config import MODELS_DIR
            asr = OmniCTC(MODELS_DIR / folder, th, int8=kind.endswith("int8"), armenian_only=kind.endswith("-hy"))
        else:
            asr = FastConformerASR(AsrConfig(cpu_threads=th, model_dir=folder, kind=kind))
        load_s = time.perf_counter() - t0
        asr.warm_up()
        rows = []
        for name, audio in clips.items():
            t1 = time.perf_counter()
            hy = asr.transcribe(audio)
            t2 = time.perf_counter()
            en = mt.translate(hy) if mt else ""
            t3 = time.perf_counter()
            rows.append({"clip": name, "audio_s": len(audio) / SAMPLE_RATE, "asr_s": t2 - t1,
                         "mt_s": t3 - t2, "hy": hy, "en": en})
        proc = [(r["asr_s"] + r["mt_s"]) for r in rows]
        rtf = [p / r["audio_s"] for p, r in zip(proc, rows)]
        res = {"asr": size, "threads": th, "load_s": load_s,
               "rtf_median": float(np.median(rtf)), "rtf_max": float(np.max(rtf)),
               "proc_max_s": float(np.max(proc)), "rows": rows}
        scored = [r for r in rows if r["clip"] in refs]
        if scored:
            we = [wer(refs[r["clip"]]["hy"], r["hy"]) for r in scored]
            ce = [cer(refs[r["clip"]]["hy"], r["hy"]) for r in scored]
            res["wer"] = sum(e for e, _ in we) / max(sum(n for _, n in we), 1)
            res["cer"] = sum(e for e, _ in ce) / max(sum(n for _, n in ce), 1)
            res["ref_words"] = sum(n for _, n in we)
            by_src = {}
            for r, w, c in zip(scored, we, ce):
                src = r["clip"].split("/")[-1].split("_")[0]
                if src in ("mic", "sys"):
                    by_src.setdefault(src, []).append((w, c))
            res["by_source"] = {k: {"clips": len(v), "ref_words": sum(w[1] for w, _ in v),
                                    "wer": sum(w[0] for w, _ in v) / max(sum(w[1] for w, _ in v), 1),
                                    "cer": sum(c[0] for _, c in v) / max(sum(c[1] for _, c in v), 1)}
                                for k, v in by_src.items()}
            en_pairs = [(refs[r["clip"]]["en"], r["en"]) for r in scored if refs[r["clip"]]["en"]]
            if en_pairs and mt:
                res["chrf"] = chrf([p[0] for p in en_pairs], [p[1] for p in en_pairs])
        results.append(res)
        del asr
        print(f"{size:5s} threads={th:2d}  load={load_s:5.1f}s  RTF median={res['rtf_median']:.2f} "
              f"max={res['rtf_max']:.2f} ({'meets' if res['rtf_max'] <= TARGET_RTF else 'MISSES'} {TARGET_RTF})  "
              f"worst clip={res['proc_max_s']:.2f}s"
              + (f"  WER={res['wer']:.1%} CER={res['cer']:.1%} ({len(scored)} clips, {res['ref_words']} words)"
                 if "wer" in res else "")
              + (f"  chrF={res['chrf']:.1f}" if "chrf" in res else ""))
        for k, v in res.get("by_source", {}).items():
            print(f"    {k}: WER={v['wer']:.1%} CER={v['cer']:.1%} ({v['clips']} clips, {v['ref_words']} words)")
        if a.show:
            for r in rows:
                print(f"    {r['clip']} ({r['audio_s']:.1f}s, asr {r['asr_s']:.2f}s): {r['hy']} | {r['en']}")

    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"benchmark-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"date": datetime.now().isoformat(), "machine": platform.platform(),
                               "cpu": platform.processor(), "clips_dir": str(clips_dir),
                               "results": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nSaved {out}. Copy the summary lines into DECISIONS.md with today's date.")


if __name__ == "__main__":
    main()
