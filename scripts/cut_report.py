"""Test-only: where does the phrase segmenter cut a recording? VAD only (no ASR/MT), fast.

    python scripts/cut_report.py recordings/<rec>/mic.wav [--smart-cut [vad|energy|asr]] [--adaptive-pause] [--window 1.5]

Per phrase: start/end in the file, length, cut reason, VAD probability at a forced cut
(low = the cut fell in a pause, high = mid-speech), and the wait from phrase end to the cut
decision (adds to latency). Same Segmenter and Silero model as the app.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from armtrans.capture import ReplaySource  # noqa: E402
from armtrans.config import VadConfig  # noqa: E402
from armtrans.vad import StreamVad  # noqa: E402


def cut(wav, cfg, word_starts=None):
    phrases = []
    ReplaySource(str(wav), StreamVad("mic", cfg, phrases.append, word_starts=word_starts),
                 realtime=False, start_t=0.0).run()
    return phrases


def summary(phrases, cfg):
    forced = [p for p in phrases if p.cut_reason in ("max_len", "smart_cut", "word_cut")]
    in_pause = sum(p.cut_prob < cfg.neg_threshold for p in forced)
    return {"phrases": len(phrases), "forced": len(forced), "forced_in_pause": in_pause,
            "short_lt_1.5s": sum(p.duration_s < 1.5 for p in phrases),
            "max_len_s": round(max(p.duration_s for p in phrases), 2),
            "max_vad_wait_s": round(max(p.emit_t - p.end_t for p in phrases), 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--smart-cut", nargs="?", const="vad", choices=["vad", "energy", "asr"])
    ap.add_argument("--adaptive-pause", action="store_true")
    ap.add_argument("--window", type=float, default=1.5, help="smart-cut search window, s")
    a = ap.parse_args()
    cfg = VadConfig(smart_cut=bool(a.smart_cut), smart_cut_by=a.smart_cut or "vad", adaptive_pause=a.adaptive_pause, smart_cut_window_s=a.window)
    ws = None
    if cfg.smart_cut and cfg.smart_cut_by == "asr":
        from armtrans.asr import FastConformerASR
        from armtrans.config import AsrConfig
        ws = FastConformerASR(AsrConfig()).word_starts       # app default ASR (RNNT)
    phrases = cut(a.wav, cfg, ws)
    for p in phrases:
        prob = "" if p.cut_prob is None else f"{p.cut_prob:.2f}"
        print(f"{p.start_t:7.2f} {p.end_t:7.2f} {p.duration_s:5.2f}s {p.cut_reason:9s} "
              f"cut_prob={prob:5s} vad_wait={p.emit_t - p.end_t:.2f}s"
              + ("" if p.cut_asr_s is None else f" cut_asr={p.cut_asr_s:.2f}s"))
    print(summary(phrases, cfg))


if __name__ == "__main__":
    main()
