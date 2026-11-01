"""scripts/tune_speakers.py: offline re-run of the app's matcher on stored fingerprints, and separability."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import tune_speakers as ts  # noqa: E402


def row(start, end, dur):
    return {"source": "system", "start_s": start, "end_s": end, "dur_s": dur, "hy": "x", "label": "?"}


A, B = ts.unit([1, 0, 0]), ts.unit([0, 1, 0])
A2, B2 = ts.unit([1, 0.3, 0]), ts.unit([0.2, 1, 0])          # cos(A,A2)=0.96, cos(B,B2)=0.98


def test_simulate_uses_app_rules_threshold_and_min_length():
    phrases = [row(0, 2, 2.0), row(3, 5, 2.0), row(6, 8, 2.0), row(9, 10, 1.0)]
    embs = [A, B, A2, B2]
    labels = [h["label"] for h in ts.simulate(phrases, embs, match=0.5, min_new=1.5)]
    assert labels == ["Speaker 1", "Speaker 2", "Speaker 1", "Speaker 2"]
    # min_new above every phrase: only the first (no speaker yet) creates one, the rest join it
    labels = [h["label"] for h in ts.simulate(phrases, embs, match=0.5, min_new=3.0)]
    assert labels == ["Speaker 1"] * 4


def test_separability_splits_same_and_different_pairs():
    sep = ts.separability([A, A2, B], ["x", "x", "y"])
    assert len(sep["same"]) == 1 and abs(sep["same"][0] - float(A @ A2)) < 1e-6
    assert sorted(round(v, 3) for v in sep["diff"]) == sorted([round(float(A @ B), 3), round(float(A2 @ B), 3)])


def test_cut_adds_vad_preroll_and_tail():
    audio = np.zeros(16000 * 10, np.float32)
    assert len(ts.cut(audio, 2.0, 4.0)) == int(4.1 * 16000) - int(1.8 * 16000)
