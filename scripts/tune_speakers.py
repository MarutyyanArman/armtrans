"""Offline speaker-label tuning on a `run --record` folder with a filled truth.json.

  python scripts/tune_speakers.py recordings/rec-20261101-120000        # without truth.json: echo check + matrix only
  python scripts/tune_speakers.py recordings/rec-... --match 0.3 0.4 0.5 --min-new 1.5 2.5

1. Cuts every printed system phrase out of system.wav (live boundaries from hyp.jsonl, plus the VAD's
   200 ms pre-roll and 100 ms tail) and computes its voice fingerprint once (real WeSpeaker model).
2. Separability: similarity between phrases of the SAME true speaker vs DIFFERENT true speakers.
   If the two ranges overlap, no single threshold can label this call correctly; that is the answer
   to "can tuning fix it", independent of the matching rule.
3. Echo check: similarity of each call phrase to my own voice ("Me", learned from mic.wav phrases that
   did not overlap call speech, same rule as the app). High values = my voice coming back in the call.
4. Re-runs the app's SpeakerMatcher (same code, same order: oldest phrase end first) for every
   (--match, --min-new) pair and scores it with score_speakers.score.

Numbers from this script are for one recording; record them in TESTS.md/DECISIONS.md with the folder name.
"""
import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from armtrans.config import SpeakerConfig  # noqa: E402
from armtrans.speakers import SpeakerMatcher  # noqa: E402
from score_speakers import overlap, score  # noqa: E402

SR = 16000
PREROLL_S, TAIL_S = 0.2, 0.1     # VadConfig.preroll_ms / tail_ms


def unit(v):
    v = np.asarray(v, np.float32).reshape(-1)
    return v / (np.linalg.norm(v) + 1e-9)


def cut(audio, start_s, end_s):
    a = max(0, int((start_s - PREROLL_S) * SR))
    b = min(len(audio), int((end_s + TAIL_S) * SR))
    return audio[a:b]


def true_speaker(h, truth):
    best = max(truth, key=lambda t: overlap(h["start_s"], h["end_s"], t["start_s"], t["end_s"]), default=None)
    if best is None or overlap(h["start_s"], h["end_s"], best["start_s"], best["end_s"]) <= 0:
        return None
    return best["speaker"]


def separability(embs, spks):
    """{'same': [...], 'diff': [...]} cosine similarities over all phrase pairs with a known speaker."""
    out = {"same": [], "diff": []}
    for i, j in itertools.combinations(range(len(embs)), 2):
        if spks[i] is None or spks[j] is None:
            continue
        out["same" if spks[i] == spks[j] else "diff"].append(float(embs[i] @ embs[j]))
    return out


def simulate(phrases, embs, match, min_new):
    """Run the app's SpeakerMatcher on precomputed fingerprints. phrases: hyp rows in processing order."""
    table = {}

    def embed(audio):
        return table[int(audio[0])]

    m = SpeakerMatcher(embed, SpeakerConfig(match=match, min_new_s=min_new))
    out = []
    for k, (h, e) in enumerate(zip(phrases, embs)):
        table[k] = e
        fake = np.zeros(max(1, int(h["dur_s"] * SR)), np.float32)   # length carries the duration rule
        fake[0] = k
        d = m.assign(fake)
        out.append(dict(h, label=d.label))
    return out


def stats(xs):
    if not xs:
        return "n=0"
    xs = np.array(xs)
    return f"n={len(xs)} min {xs.min():.2f} p10 {np.percentile(xs, 10):.2f} median {np.median(xs):.2f} " \
           f"p90 {np.percentile(xs, 90):.2f} max {xs.max():.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--truth", help="default: <folder>/truth.json")
    ap.add_argument("--match", type=float, nargs="+", default=[0.3, 0.35, 0.4, 0.45, 0.5, 0.55])
    ap.add_argument("--min-new", type=float, nargs="+", default=[1.5, 2.0, 2.5, 3.0])
    a = ap.parse_args()
    import soundfile as sf
    from armtrans.speakers import load_embedder

    folder = Path(a.folder)
    truth_path = Path(a.truth) if a.truth else folder / "truth.json"
    truth = json.load(open(truth_path, encoding="utf-8")) if truth_path.exists() else None
    hyp = [json.loads(l) for l in open(folder / "hyp.jsonl", encoding="utf-8") if l.strip()]
    sys_audio, sr = sf.read(folder / "system.wav", dtype="float32")
    assert sr == SR, sr
    embed = load_embedder(SpeakerConfig())

    phrases = sorted([h for h in hyp if h["source"] == "system" and h["hy"]], key=lambda h: h["end_s"])
    for h in phrases:
        h["dur_s"] = len(cut(sys_audio, h["start_s"], h["end_s"])) / SR
    embs = [unit(embed(cut(sys_audio, h["start_s"], h["end_s"]))) for h in phrases]
    spks = [true_speaker(h, truth) if truth else None for h in phrases]

    if truth is None:
        print(f"{len(phrases)} call phrases. No truth file ({truth_path.name}): echo check and similarity matrix only.")
        print("\nSimilarity matrix between call phrases (x100; rows/cols = phrase start seconds):")
        print("        " + " ".join(f"{h['start_s']:5.0f}" for h in phrases))
        for h, e in zip(phrases, embs):
            print(f"{h['start_s']:6.1f}  " + " ".join(f"{int(round(float(e @ f) * 100)):5d}" for f in embs)
                  + f"   {h['label']}")
    else:
        print(f"{len(phrases)} call phrases, true speakers: "
              + ", ".join(f"{s}={spks.count(s)}" for s in sorted({s for s in spks if s}))
              + (f", unmatched={spks.count(None)}" if None in spks else ""))
        sep = separability(embs, spks)
        print("\nSeparability (cosine between phrases):")
        print("  same speaker:      " + stats(sep["same"]))
        print("  different speaker: " + stats(sep["diff"]))
        if sep["same"] and sep["diff"]:
            if min(sep["same"]) > max(sep["diff"]):
                print("  -> ranges do not overlap: a threshold between them separates every pair.")
            else:
                print("  -> ranges OVERLAP: no single threshold separates all pairs on this recording.")

    me = None
    mic_path = folder / "mic.wav"
    if mic_path.exists():
        mic_audio, _ = sf.read(mic_path, dtype="float32")
        spans = [(h["start_s"], h["end_s"]) for h in phrases]
        mine = [h for h in hyp if h["source"] == "mic" and h["hy"] and h["end_s"] - h["start_s"] >= 1.5
                and not any(overlap(h["start_s"] - 0.5, h["end_s"] + 0.5, s, e) > 0 for s, e in spans)]
        if mine:
            me = unit(np.mean([unit(embed(cut(mic_audio, h["start_s"], h["end_s"]))) for h in mine], axis=0))
            print(f"\nEcho check (call phrase vs my voice, Me learned from {len(mine)} mic phrases):")
            for h, e, s in zip(phrases, embs, spks):
                sim = float(me @ e)
                flag = "  <- sounds like Me" if sim >= 0.5 else ""
                print(f"  {h['start_s']:7.2f}s {h['dur_s']:4.1f}s true={s or '-':10s} printed={h['label']:10s} "
                      f"sim(Me)={sim:.2f}{flag}")

    if truth is None:
        print("\nNo truth: label count and sequence per setting (compare with the real number of remote speakers;")
        print("a correct COUNT does not prove correct ASSIGNMENT).")
        print("match  min_new  labels  sequence")
        for match, min_new in itertools.product(a.match, a.min_new):
            seq = [h["label"].replace("Speaker ", "") for h in simulate(phrases, embs, match, min_new)]
            print(f"{match:5.2f}  {min_new:7.1f}  {len(set(seq)):6d}  {' '.join(seq)}")
        print("live   (as printed)  " + f"{len({h['label'] for h in phrases}):3d}  "
              + " ".join(h["label"].replace("Speaker ", "") for h in phrases))
        return
    print("\nmatch  min_new  labels  spurious  purity   swaps")
    for match, min_new in itertools.product(a.match, a.min_new):
        sim_hyp = simulate(phrases, embs, match, min_new)
        r = score(truth, sim_hyp, show=lambda *_: None)
        print(f"{match:5.2f}  {min_new:7.1f}  {r['labels']:6d}  {r['spurious']:8d}  "
              f"{r['purity_ok']:3d}/{r['numbered']:<3d}  {r['swaps']:5d}")
    live = score(truth, phrases, show=lambda *_: None)
    print(f"live   (as printed)  {live['labels']:3d}  {live['spurious']:8d}  "
          f"{live['purity_ok']:3d}/{live['numbered']:<3d}  {live['swaps']:5d}")


if __name__ == "__main__":
    main()
