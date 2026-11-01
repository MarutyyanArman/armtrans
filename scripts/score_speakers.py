"""Score speaker labels from `replay --hyp-out` against a truth file.

  python scripts/score_speakers.py --truth eval/tts_call/truth.json --hyp results/hyp.jsonl

truth.json: [{"start_s": 1.0, "end_s": 3.2, "speaker": "David"}, ...]  (system-audio phrases)
Each hypothesis phrase is matched to the truth phrase it overlaps most. Reports, per phrase, the true
speaker and the printed label, then:
- label purity: share of labelled phrases whose label's majority true speaker is their true speaker;
- spurious speakers: labels beyond the number of true speakers;
- "Speaker ?" count; swaps: a true speaker getting a different numbered label later.
"""
import argparse
import json
from collections import Counter, defaultdict


def overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def score(truth, hyp, show=print):
    """Score system-audio labels. Returns dict(matched, purity_ok, numbered, labels, n_true, spurious, unknown, swaps)."""
    hyp = [h for h in hyp if h["source"] == "system"]
    pairs = []
    for h in hyp:
        best = max(truth, key=lambda t: overlap(h["start_s"], h["end_s"], t["start_s"], t["end_s"]))
        ov = overlap(h["start_s"], h["end_s"], best["start_s"], best["end_s"])
        pairs.append((h, best if ov > 0 else None))
        show(f"  {h['start_s']:6.2f}-{h['end_s']:6.2f}s  true={best['speaker'] if ov > 0 else '-':8s} label={h['label']}")

    numbered = [(h, t) for h, t in pairs if t is not None and h["label"] != "Speaker ?"]
    by_label = defaultdict(Counter)
    for h, t in numbered:
        by_label[h["label"]][t["speaker"]] += 1
    majority = {lab: c.most_common(1)[0][0] for lab, c in by_label.items()}
    correct = sum(1 for h, t in numbered if majority[h["label"]] == t["speaker"])
    n_true = len({t["speaker"] for t in truth})
    first_label = {}
    swaps = 0
    for h, t in numbered:
        if t["speaker"] in first_label and first_label[t["speaker"]] != h["label"]:
            swaps += 1
        first_label.setdefault(t["speaker"], h["label"])
    return {"matched": sum(1 for _, t in pairs if t), "purity_ok": correct, "numbered": len(numbered),
            "labels": len(by_label), "n_true": n_true, "spurious": max(0, len(by_label) - n_true),
            "unknown": sum(1 for h, _ in pairs if h["label"] == "Speaker ?"), "swaps": swaps}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", required=True)
    ap.add_argument("--hyp", required=True)
    a = ap.parse_args()
    truth = json.load(open(a.truth, encoding="utf-8"))
    hyp = [json.loads(l) for l in open(a.hyp, encoding="utf-8") if l.strip()]
    r = score(truth, hyp)
    print(f"\nphrases matched: {r['matched']} / truth {len(truth)}")
    print(f"label purity: {r['purity_ok']}/{r['numbered']}")
    print(f"labels used: {r['labels']} for {r['n_true']} true speakers -> spurious: {r['spurious']}")
    print(f"'Speaker ?': {r['unknown']}   label swaps: {r['swaps']}")


if __name__ == "__main__":
    main()
