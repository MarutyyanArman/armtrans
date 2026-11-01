"""scripts/bench_lm.py: the pure-Python ARPA reader must give KenLM's back-off scores."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
bl = pytest.importorskip("bench_lm")

ARPA = """\\data\\
ngram 1=5
ngram 2=3
ngram 3=1

\\1-grams:
-1.0\t<unk>\t0
-99\t<s>\t-0.30
-0.70\t</s>\t0
-0.50\tա\t-0.20
-0.60\tբ\t-0.10

\\2-grams:
-0.25\t<s> ա\t-0.15
-0.40\tա բ\t0
-0.35\tբ </s>\t0

\\3-grams:
-0.05\t<s> ա բ

\\end\\
"""


@pytest.fixture
def lm(tmp_path):
    p = tmp_path / "t.arpa"
    p.write_text(ARPA, encoding="utf-8")
    return bl.ArpaLM(str(p))


def score_seq(lm, words):
    s, st = 0.0, bl.State()
    lm.BeginSentenceWrite(st)
    for w in words:
        out = bl.State()
        s += lm.BaseScore(st, w, out)
        st = out
    return s


def test_reads_orders_and_vocab(lm):
    assert lm.order == 3 and "ա" in lm and "գ" not in lm


def test_backoff_maths_matches_kenlm_definition(lm):
    # <s> ա : bigram hit                                  = -0.25
    # ա | <s> ա -> "<s> ա բ" trigram hit                   = -0.05
    assert score_seq(lm, ["ա", "բ"]) == pytest.approx(-0.25 + -0.05)
    # "<s> ա ա": no trigram, no bigram "ա ա" -> bo(<s> ա) + bo(ա) + p(ա) = -0.15 - 0.20 - 0.50
    assert score_seq(lm, ["ա", "ա"]) == pytest.approx(-0.25 + (-0.15 - 0.20 - 0.50))
    # unknown word: back off to <unk>
    assert score_seq(lm, ["գ"]) == pytest.approx(-0.30 + -1.0)


def test_greedy_merges_repeats_and_bpe_marks():
    labels = ["▁ա", "բ", ""]
    logp = np.log(np.array([[.9, .05, .05], [.9, .05, .05], [.05, .05, .9], [.05, .9, .05]], np.float32))
    assert bl.greedy(logp, labels) == "աբ"


def test_labels_restore_word_start_mark_removed_by_onnx_asr():
    import types
    inner = types.SimpleNamespace(_vocab={0: "<unk>", 1: " ես", 2: "ում", 3: " ", 4: "<blk>"}, _blank_idx=4)
    labels = bl.labels_for(inner)
    assert labels == ["⁇", "▁ես", "ում", "▁", ""]
    from pyctcdecode.alphabet import Alphabet
    assert Alphabet.build_alphabet(labels).is_bpe
