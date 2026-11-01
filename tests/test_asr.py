"""ASR wrapper tests with a tiny stand-in ONNX model that has the same interface as the
FastConformer CTC export (inputs audio_signal[B,80,T], length[B]; output logprobs[B,T,V]).

This exercises our wrapper + the real onnx-asr loading, NeMo 80-mel preprocessing and CTC
decoding. It says nothing about recognition accuracy; that needs the real model on the laptop.
"""
import numpy as np
import pytest

onnx = pytest.importorskip("onnx")
from onnx import TensorProto, helper  # noqa: E402

from armtrans import asr as asr_mod  # noqa: E402
from armtrans.config import AsrConfig  # noqa: E402


def build_standin(model_dir):
    """Every frame strongly predicts token 1 ('▁բարև'); CTC collapses the repeats to one word."""
    model_dir.mkdir(parents=True)
    logits = helper.make_tensor("logits", TensorProto.FLOAT, [1, 1, 3], [0.0, 10.0, 0.0])
    zero = helper.make_tensor("zero", TensorProto.FLOAT, [], [0.0])
    nodes = [
        helper.make_node("ReduceMean", ["audio_signal"], ["m"], axes=[1], keepdims=1),   # [B,1,T]
        helper.make_node("Transpose", ["m"], ["mt"], perm=[0, 2, 1]),                     # [B,T,1]
        helper.make_node("Mul", ["mt", "zero"], ["z"]),                                   # zeros [B,T,1]
        helper.make_node("Add", ["z", "logits"], ["l"]),                                  # [B,T,3]
        helper.make_node("LogSoftmax", ["l"], ["logprobs"], axis=-1),
    ]
    graph = helper.make_graph(
        nodes, "standin",
        [helper.make_tensor_value_info("audio_signal", TensorProto.FLOAT, ["B", 80, "T"]),
         helper.make_tensor_value_info("length", TensorProto.INT64, ["B"])],
        [helper.make_tensor_value_info("logprobs", TensorProto.FLOAT, ["B", "T", 3])],
        initializer=[logits, zero])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save(model, str(model_dir / "model.onnx"))
    (model_dir / "vocab.txt").write_text("<unk> 0\n▁բարև 1\n<blk> 2\n", encoding="utf-8")


def test_wrapper_loads_locally_and_decodes(tmp_path, monkeypatch):
    monkeypatch.setattr(asr_mod, "MODELS_DIR", tmp_path)
    build_standin(tmp_path / "fc")
    asr = asr_mod.FastConformerASR(AsrConfig(model_dir="fc", kind="nemo-conformer-ctc", cpu_threads=1))
    asr.warm_up()
    assert asr.transcribe(np.zeros(16000 * 2, np.float32)) == "բարև"
    # Phrase-length audio (6 s, the max phrase) also works.
    assert asr.transcribe(np.zeros(16000 * 6, np.float32)) == "բարև"


def test_missing_model_gives_setup_instruction(tmp_path, monkeypatch):
    monkeypatch.setattr(asr_mod, "MODELS_DIR", tmp_path)
    with pytest.raises(asr_mod.ModelMissing, match="download_models.py --asr"):
        asr_mod.FastConformerASR(AsrConfig(model_dir="nothing-here"))     # default = RNNT


def test_default_is_rnnt_and_unknown_type_is_refused():
    assert AsrConfig().kind == "nemo-conformer-rnnt" and AsrConfig().model_dir == "fastconformer-hy-rnnt"
    with pytest.raises(ValueError, match="Unknown ASR model type"):
        asr_mod.FastConformerASR(AsrConfig(kind="something-else"))


def _asr_with_fake_timestamps(tokens, times, tol=0.08, lead=0.0):
    import threading
    from types import SimpleNamespace
    asr = object.__new__(asr_mod.FastConformerASR)
    asr.cfg = AsrConfig(context_tol_s=tol, context_lead_s=lead)
    asr._lock = threading.Lock()
    seen = []

    def recognize(a, sample_rate):
        seen.append(len(a))
        return SimpleNamespace(tokens=tokens, timestamps=times)
    asr._ts_model = SimpleNamespace(recognize=recognize)
    return asr, seen


def test_context_keeps_only_words_reaching_into_the_new_audio():
    # context = 1.0 s. " Ես" and " ուզ"+"ում" lie in the context (first 0.56 < 1.0, last 1.04 < 1.08) -> dropped;
    # " քերական"+"ությունից" was cut in half by the previous phrase -> printed whole (capitalised).
    tokens = [" Ես", " ուզ", "ում", " քերական", "ությունից", ",", " ներկա"]
    times = [0.20, 0.56, 1.04, 1.04, 1.20, 1.40, 1.60]
    asr, seen = _asr_with_fake_timestamps(tokens, times)
    text = asr.transcribe_with_context(np.zeros(16000, np.float32), np.zeros(16000, np.float32))
    assert text == "Քերականությունից, ներկա"
    assert seen == [32000]                                  # context + audio recognised together


def test_context_with_nothing_new_gives_empty_text():
    asr, _ = _asr_with_fake_timestamps([" Ես", " եմ"], [0.2, 0.5])
    assert asr.transcribe_with_context(np.zeros(8000, np.float32), np.zeros(16000, np.float32)) == ""


def test_context_keeps_a_short_word_starting_right_at_the_join():
    # " Իմ" is one token starting 0.04 s before the 1.0 s join (timestamps are 80 ms steps).
    tokens, times = [" եմ", " Իմ", " գիտ", "ելիք"], [0.70, 0.96, 1.12, 1.28]
    asr, _ = _asr_with_fake_timestamps(tokens, times, lead=0.0)
    assert asr.transcribe_with_context(np.zeros(16000, np.float32), np.zeros(16000, np.float32)) == "Գիտելիք"
    asr, _ = _asr_with_fake_timestamps(tokens, times, lead=0.12)
    assert asr.transcribe_with_context(np.zeros(16000, np.float32), np.zeros(16000, np.float32)) == "Իմ գիտելիք"


def test_context_keeps_a_one_token_word_starting_just_after_the_join():
    # Measured case (2026-11-01): " Շատ" at the join was lost by a last-token-only rule.
    asr, _ = _asr_with_fake_timestamps([" լսումը", " Շատ", " շնորհ", "ակալ"], [0.60, 1.04, 1.20, 1.36])
    assert asr.transcribe_with_context(np.zeros(16000, np.float32), np.zeros(16000, np.float32)) == "Շատ շնորհակալ"
