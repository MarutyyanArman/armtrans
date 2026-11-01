import subprocess
import sys
from pathlib import Path

import pytest

from armtrans.metrics import cer, chrf, normalize_hy, wer

ROOT = Path(__file__).resolve().parent.parent


def test_normalize_removes_armenian_punctuation_and_case():
    assert normalize_hy("Բարև, ինչպե՞ս ես։") == "բարեւ ինչպես ես"
    assert normalize_hy("«Այո»․") == "այո"
    assert normalize_hy("Լա՛վ, գնացի՜նք") == "լավ գնացինք"     # in-word marks deleted, word kept whole


def test_wer_and_cer_counts():
    assert wer("բարեւ ինչպես ես", "Բարև, ինչպե՞ս ես։") == (0, 3)
    assert wer("բարեւ ինչպես ես", "բարեւ ես") == (1, 3)
    e, n = cer("այո", "այա")
    assert (e, n) == (1, 3)


def test_chrf_identity():
    assert chrf(["hello world"], ["hello world"]) == pytest.approx(100.0)


def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "armtrans", *args], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", timeout=120)


def test_replay_without_asr_model_fails_clearly():
    if (ROOT / "models" / "fastconformer-hy-ctc" / "model.onnx").exists():
        pytest.skip("real ASR model is installed")
    r = run_cli("replay", "--mic-wav", "x.wav")
    assert r.returncode != 0
    assert "download_models.py" in (r.stderr + r.stdout)
    assert "Traceback" not in r.stderr
