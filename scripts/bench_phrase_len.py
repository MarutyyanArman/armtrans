"""Test-only: processing time (ASR + speaker embedding + translation) vs phrase length, on real speech.

    python scripts/bench_phrase_len.py <wav> --start 43.9 52.0 --lengths 4 5 6 7 8 9 10 --reps 3

Cuts clips of each length from continuous speech in <wav> and times the app's own models
(same settings as `run`). Used to pick a phrase length cap that keeps the 5 s limit.
"""
import argparse
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from armtrans.config import AppConfig  # noqa: E402
from armtrans.asr import FastConformerASR  # noqa: E402
from armtrans.speakers import load_embedder  # noqa: E402
from armtrans.translate import NllbMT  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--start", type=float, nargs="+", required=True)
    ap.add_argument("--lengths", type=float, nargs="+", default=[4, 5, 6, 7, 8])
    ap.add_argument("--reps", type=int, default=3)
    a = ap.parse_args()
    cfg = AppConfig()
    asr, mt, emb = FastConformerASR(cfg.asr), NllbMT(cfg.mt), load_embedder(cfg.speakers)
    asr.warm_up(); mt.warm_up(); emb(np.zeros(16000, np.float32))
    audio, sr = sf.read(a.wav, dtype="float32")
    assert sr == 16000, sr
    print("len_s start   asr_s  spk_s  mt_s  total_s(median of reps / max)  words")
    for L in a.lengths:
        for s0 in a.start:
            clip = audio[int(s0 * sr):int((s0 + L) * sr)]
            tot, parts = [], None
            for _ in range(a.reps):
                t0 = time.perf_counter(); text = asr.transcribe(clip); t1 = time.perf_counter()
                emb(clip); t2 = time.perf_counter()
                mt.translate(text); t3 = time.perf_counter()
                tot.append(t3 - t0); parts = (t1 - t0, t2 - t1, t3 - t2)
            print(f"{L:5.1f} {s0:6.1f}  {parts[0]:.2f}  {parts[1]:.2f}  {parts[2]:.2f}  "
                  f"{statistics.median(tot):.2f} / {max(tot):.2f}  {len(text.split())}")


if __name__ == "__main__":
    main()
