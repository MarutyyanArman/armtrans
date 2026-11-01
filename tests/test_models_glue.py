"""Glue tests for model wrappers without the real models (they can't be downloaded in the build workspace).
They check our code paths, not accuracy or speed."""
import numpy as np
import pytest

onnx = pytest.importorskip("onnx")
from onnx import TensorProto, helper  # noqa: E402

from armtrans import speakers as spk_mod  # noqa: E402
from armtrans.capture import _Consumer  # noqa: E402
from armtrans.config import SpeakerConfig  # noqa: E402
from armtrans.translate import output_text, source_tokens  # noqa: E402


def build_standin_wespeaker(model_dir):
    """Same interface as WeSpeaker ResNet34-LM (feats[B,T,80] -> embs[B,256]): embs = mean over time, tiled."""
    model_dir.mkdir(parents=True)
    reps = helper.make_tensor("reps", TensorProto.INT64, [2], [1, 4])          # 80*... -> tile to 320, slice 256
    starts = helper.make_tensor("starts", TensorProto.INT64, [1], [0])
    ends = helper.make_tensor("ends", TensorProto.INT64, [1], [256])
    axes = helper.make_tensor("axes", TensorProto.INT64, [1], [1])
    nodes = [
        helper.make_node("ReduceMean", ["feats"], ["m"], axes=[1], keepdims=0),    # [B,80]
        helper.make_node("Tile", ["m", "reps"], ["t"]),                             # [B,320]
        helper.make_node("Slice", ["t", "starts", "ends", "axes"], ["embs"]),        # [B,256]
    ]
    graph = helper.make_graph(
        nodes, "standin_spk",
        [helper.make_tensor_value_info("feats", TensorProto.FLOAT, ["B", "T", 80])],
        [helper.make_tensor_value_info("embs", TensorProto.FLOAT, ["B", 256])],
        initializer=[reps, starts, ends, axes])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save(model, str(model_dir / "standin.onnx"))


def test_embedder_loads_locally_through_onnx_asr(tmp_path, monkeypatch):
    monkeypatch.setattr(spk_mod, "MODELS_DIR", tmp_path)
    build_standin_wespeaker(tmp_path / "spk")
    embed = spk_mod.load_embedder(SpeakerConfig(model_dir="spk", cpu_threads=1))
    rng = np.random.default_rng(0)
    e = embed((rng.standard_normal(16000 * 2) * 0.1).astype(np.float32))
    assert e.shape == (256,) and np.isfinite(e).all()


def test_missing_speaker_model_gives_setup_instruction(tmp_path, monkeypatch):
    monkeypatch.setattr(spk_mod, "MODELS_DIR", tmp_path)
    with pytest.raises(spk_mod.ModelMissing, match="download_models.py --speakers"):
        spk_mod.load_embedder(SpeakerConfig(model_dir="none"))


class FakeSP:
    def encode(self, text, out_type=str):
        return ["▁" + w for w in text.split()]

    def decode(self, pieces):
        return "".join(pieces).replace("▁", " ").strip()


def test_nllb_token_layout():
    sp = FakeSP()
    assert source_tokens(sp, "Բարև ես") == ["hye_Armn", "▁Բարև", "▁ես", "</s>"]
    assert output_text(sp, ["eng_Latn", "▁Hello", "▁there", "</s>"]) == "Hello there"


def test_loopback_gap_is_filled_with_timed_silence():
    chunks = []
    clock = {"t": 10.0}
    c = _Consumer(16000, lambda ch, t: chunks.append(t), "test", fill_gaps=True, gap_s=0.1, now=lambda: clock["t"])
    c.put(np.zeros(320, np.float32), 10.0)
    c.step(timeout=0.01)                    # real block
    n_after_block = len(chunks)
    clock["t"] = 10.7                       # loopback silent for 0.7 s
    c.step(timeout=0.01)                    # nothing queued -> gap fill
    assert c.gap_fills == 1
    assert len(chunks) - n_after_block >= int(0.7 * 16000 / 512) - 1
    assert 10.7 - 512 / 16000 <= chunks[-1] <= 10.7    # last full chunk ends within one chunk of "now"


def test_mic_consumer_never_invents_audio():
    chunks = []
    c = _Consumer(16000, lambda ch, t: chunks.append(t), "test", fill_gaps=False, gap_s=0.1, now=lambda: 99.0)
    c.last_t = 0.0
    c.step(timeout=0.01)
    assert chunks == [] and c.gap_fills == 0


# --- NLLB short-input guard (2026-11-01) ---

class FakeTranslator:
    """Records calls; returns a fixed hypothesis and score."""
    def __init__(self, words, score):
        self.words, self.score, self.calls = words, score, 0

    def translate_batch(self, src, target_prefix=None, beam_size=4, return_scores=False):
        from types import SimpleNamespace
        self.calls += 1
        return [SimpleNamespace(hypotheses=[["eng_Latn"] + ["▁" + w for w in self.words] + ["</s>"]],
                                scores=[self.score])]


def fake_mt(words, score, **cfg):
    from armtrans.config import MtConfig
    from armtrans.translate import NllbMT
    mt = object.__new__(NllbMT)
    mt.cfg = MtConfig(**cfg)
    mt.sp = FakeSP()
    mt.translator = FakeTranslator(words, score)
    return mt


def test_common_reply_gets_fixed_english_without_the_model():
    mt = fake_mt(["No,", "I", "did", "not."], -0.79)
    assert mt.translate("Ոչ։") == "No."
    assert mt.translate("բարև ձեզ.") == "Hello."            # case, punctuation and և spelling normalised
    assert mt.translator.calls == 0


def test_question_is_not_taken_from_the_reply_list():
    mt = fake_mt(["Really?"], -0.5)
    assert mt.translate("Հա՞") == "Really?"
    assert mt.translator.calls == 1


def test_low_score_short_line_prints_unclear_not_invented_text():
    from armtrans.translate import UNCLEAR
    mt = fake_mt(["What", "is", "it?"], -1.07)
    assert mt.translate("Հ։") == UNCLEAR
    assert mt.translate("Գասս հ։") == UNCLEAR                 # 2 words: still short


def test_guard_never_touches_long_or_confident_lines():
    mt = fake_mt(["The", "sun."], -0.34)
    assert mt.translate("Արեւ։") == "The sun."               # confident short line
    low = fake_mt(["Some", "text"], -1.5)
    assert low.translate("Երեք բառից ավելի երկար տող է։") == "Some text"   # > 2 words: never replaced


def test_guard_off_restores_old_behaviour():
    mt = fake_mt(["No,", "I", "did", "not."], -1.2, short_guard=False)
    assert mt.translate("Ոչ։") == "No, I did not."
    assert mt.translate("Հ։") == "No, I did not."
