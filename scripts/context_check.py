"""Test-only: does ASR context after a forced cut help? Uses the exact phrase audio saved by `run --record`.

    python scripts/context_check.py recordings/<rec> [recordings/<rec> ...] [--context-s 1.5]

For every phrase that directly follows a forced cut on the same stream, prints the previous line,
the plain ASR text and the ASR text with the previous phrase's last `--context-s` seconds as context
(exactly what the app now does live). No reference text: judge by reading / against refs.tsv.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from armtrans.asr import FastConformerASR  # noqa: E402
from armtrans.config import AsrConfig  # noqa: E402

FORCED = ("max_len", "smart_cut", "word_cut")


def pairs(rec: Path):
    if not (rec / "meta.json").exists():
        print(f"(skipped {rec.name}: no meta.json - session did not finish normally)")
        return
    meta = json.loads((rec / "meta.json").read_text(encoding="utf-8"))
    tlog = Path(meta["timing_log"].replace("\\", "/"))
    tlog = ROOT / "logs" / tlog.name
    reasons = {}
    for line in tlog.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("event") == "phrase":
            reasons[(r["source"], r["seq"])] = r["cut_reason"]
    clips = {}
    for line in (rec / "hyp.jsonl").read_text(encoding="utf-8").splitlines():
        h = json.loads(line)
        if h.get("clip"):
            src, seq = Path(h["clip"]).stem.rsplit("_", 1)
            clips[(src, int(seq))] = h
    for (src, seq), h in sorted(clips.items()):
        prev = clips.get((src, seq - 1))
        if prev and reasons.get((src, seq - 1)) in FORCED:
            yield prev, h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("recs", nargs="+")
    ap.add_argument("--context-s", type=float, default=1.5)
    ap.add_argument("--lead", type=float, nargs="+", default=[AsrConfig().context_lead_s],
                    help="context_lead_s values to compare")
    a = ap.parse_args()
    asr = FastConformerASR(AsrConfig())
    n_ctx = int(a.context_s * 16000)
    n = 0
    times = []
    for rec in map(Path, a.recs):
        for prev, h in pairs(rec):
            pa, _ = sf.read(rec / prev["clip"], dtype="float32")
            ca, _ = sf.read(rec / h["clip"], dtype="float32")
            plain = asr.transcribe(ca)
            n += 1
            print(f"--- {rec.name} {h['clip']}  ({len(ca) / 16000:.1f} s)")
            print(f"  prev   : {prev['hy']}")
            print(f"  plain  : {plain}")
            for lead in a.lead:
                asr.cfg.context_lead_s = lead
                t0 = time.perf_counter()
                with_ctx = asr.transcribe_with_context(ca, pa[-n_ctx:])
                times.append(time.perf_counter() - t0)
                print(f"  ctx {lead:.2f}: {with_ctx}")
    if times:
        print(f"\n{n} phrases after a forced cut; ASR with context {min(times):.2f}-{max(times):.2f} s")


if __name__ == "__main__":
    main()
