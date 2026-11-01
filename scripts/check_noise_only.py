"""Test-only: does the app print anything when nobody speaks? (PLAN.md section 7 hard case "noise, silence")

    python scripts/check_noise_only.py [eval/hard/noise_only]

Runs each WAV through the app's own path: Silero VAD + Segmenter (default VadConfig) -> ASR (app default)
-> translation (with the short-input guard). Counts phrases found, phrases with ASR text (= lines the app
would print). Expected: 0 printed lines.
"""
import os
import sys
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from armtrans.asr import FastConformerASR  # noqa: E402
from armtrans.capture import ReplaySource  # noqa: E402
from armtrans.config import AppConfig  # noqa: E402
from armtrans.translate import NllbMT  # noqa: E402
from armtrans.vad import StreamVad  # noqa: E402


def main():
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "eval" / "hard" / "noise_only"
    cfg = AppConfig()
    asr, mt = FastConformerASR(cfg.asr), NllbMT(cfg.mt)
    asr.warm_up(); mt.warm_up()
    files = sorted(folder.glob("*.wav"))
    total_s = n_phr = n_lines = 0
    for f in files:
        phrases = []
        src = ReplaySource(str(f), StreamVad("mic", cfg.vad, phrases.append), realtime=False)
        total_s += src.duration_s
        src.run()
        for p in phrases:
            n_phr += 1
            hy = asr.transcribe(p.audio)
            if hy:
                n_lines += 1
                print(f"PRINTED  {f.name}  {p.duration_s:.1f}s  | {hy} | {mt.translate(hy)}")
        if not phrases:
            print(f"ok       {f.name}  (no phrase)")
        elif not any(asr.transcribe(p.audio) for p in phrases):
            print(f"ok       {f.name}  ({len(phrases)} phrase(s), no ASR text)")
    print(f"\n{len(files)} files, {total_s:.0f} s of no-speech audio: {n_phr} phrases found by VAD, "
          f"{n_lines} lines would be printed (target 0)")


if __name__ == "__main__":
    main()
