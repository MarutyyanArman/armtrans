"""`run --record` (test only): recorded WAVs must line up with live times so replay reproduces the session."""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from armtrans.capture import CHUNK, ChunkRecorder, Framer, ReplaySource
from armtrans.config import VadConfig
from armtrans.vad import StreamVad

SR = 16000
ROOT = Path(__file__).resolve().parent.parent


def chunk_end(i, start):
    return start + (i + 1) * CHUNK / SR


def test_recorder_pads_start_and_passes_every_chunk_through(tmp_path):
    seen = []
    rec = ChunkRecorder(lambda c, t: seen.append(t), tmp_path / "a.wav", t0=10.0)
    for i in range(10):                         # stream starts 0.5 s after t0
        rec(np.full(CHUNK, 0.1, np.float32), chunk_end(i, 10.5))
    rec.close()
    assert len(seen) == 10
    audio, sr = sf.read(tmp_path / "a.wav", dtype="float32")
    assert sr == SR
    assert len(audio) == round(0.5 * SR) + 10 * CHUNK
    assert np.abs(audio[: round(0.5 * SR)]).max() == 0
    assert audio[round(0.5 * SR)] == pytest.approx(0.1, abs=1e-3)
    assert rec.padded_s == pytest.approx(0.5, abs=1 / SR)


def test_recorder_ignores_jitter_but_fills_real_gaps(tmp_path):
    rec = ChunkRecorder(lambda c, t: None, tmp_path / "a.wav", t0=0.0)
    rng = np.random.default_rng(0)
    rec(np.zeros(CHUNK, np.float32), chunk_end(0, 0.0))  # first chunk exactly at t0 (its offset is padded)
    for i in range(1, 50):                      # then +-10 ms arrival jitter: no silence may be added
        rec(np.zeros(CHUNK, np.float32), chunk_end(i, 0.0) + rng.uniform(-0.01, 0.01))
    assert rec.padded_s == 0
    assert rec.samples == 50 * CHUNK
    rec(np.zeros(CHUNK, np.float32), chunk_end(50, 0.0) + 1.0)   # 1 s stall
    assert rec.padded_s == pytest.approx(1.0, abs=1 / SR)
    assert rec.samples == 51 * CHUNK + round(1.0 * SR)
    rec.close()


def test_write_error_is_kept_and_live_audio_still_flows(tmp_path):
    seen = []
    rec = ChunkRecorder(lambda c, t: seen.append(t), tmp_path / "a.wav", t0=0.0)
    rec.close()
    rec(np.zeros(CHUNK, np.float32), 0.032)
    assert rec.error is not None
    assert seen == [0.032]


class LoudnessVAD:
    def __call__(self, chunk):
        return 0.9 if np.abs(chunk).mean() > 0.05 else 0.0


def test_recorded_two_streams_replay_with_live_alignment(tmp_path):
    """Live: mic starts 0.3 s after t0, system 0.7 s after; speech at known clock times.
    Replaying the two recorded files must place each phrase at (live time - t0)."""
    t0 = 100.0
    live = {"mic": [], "system": []}
    starts = {"mic": 100.3, "system": 100.7}
    speech = {"mic": (101.0, 2.0), "system": (103.0, 1.5)}       # (clock start, duration)
    for name in ("mic", "system"):
        vad = StreamVad(name, VadConfig(), live[name].append, vad_model=LoudnessVAD())
        rec = ChunkRecorder(vad, tmp_path / f"{name}.wav", t0)
        f = Framer(rec)
        n = int(5.5 * SR)
        x = np.zeros(n, np.float32)
        s0 = int((speech[name][0] - starts[name]) * SR)
        x[s0:s0 + int(speech[name][1] * SR)] = 0.3
        for pos in range(0, n, 320):            # 20 ms blocks stamped by arrival time
            f.push(x[pos:pos + 320], starts[name] + (pos + 320) / SR)
        rec.close()
    replayed = {}
    for name in ("mic", "system"):
        out = []
        vad = StreamVad(name, VadConfig(), out.append, vad_model=LoudnessVAD())
        ReplaySource(str(tmp_path / f"{name}.wav"), vad, realtime=False, start_t=0.0).run()
        replayed[name] = out
    for name in ("mic", "system"):
        assert len(live[name]) == len(replayed[name]) == 1
        assert replayed[name][0].start_t == pytest.approx(live[name][0].start_t - t0, abs=0.04)
        assert replayed[name][0].end_t == pytest.approx(live[name][0].end_t - t0, abs=0.04)


def test_truth_draft_lists_system_phrases_and_never_overwrites(tmp_path):
    rows = [{"source": "mic", "label": "Me", "start_s": 0.5, "end_s": 1.5, "hy": "բարեւ", "en": "hi"},
            {"source": "system", "label": "Speaker 1", "start_s": 2.0, "end_s": 4.0, "hy": "այո", "en": "yes"},
            {"source": "system", "label": "Speaker ?", "start_s": 5.0, "end_s": 6.0, "hy": "", "en": ""}]
    (tmp_path / "hyp.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                        encoding="utf-8")
    cmd = [sys.executable, str(ROOT / "scripts" / "make_truth_draft.py"), str(tmp_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    draft = json.loads((tmp_path / "truth_draft.json").read_text(encoding="utf-8"))
    assert draft == [{"start_s": 2.0, "end_s": 4.0, "speaker": "", "printed": "Speaker 1", "hy": "այո"}]
    r2 = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert r2.returncode != 0 and "not overwriting" in r2.stderr


def test_asr_export_writes_mic_and_call_clips_and_never_overwrites_refs(tmp_path):
    sf.write(tmp_path / "mic.wav", np.full(SR * 4, 0.1, np.float32), SR)
    sf.write(tmp_path / "system.wav", np.full(SR * 4, 0.2, np.float32), SR)
    rows = [{"source": "mic", "label": "Me", "start_s": 0.5, "end_s": 1.5, "hy": "բարեւ", "en": "hi"},
            {"source": "system", "label": "Speaker 1", "start_s": 2.0, "end_s": 3.0, "hy": "այո", "en": "yes"},
            {"source": "system", "label": "Speaker 1", "start_s": 3.2, "end_s": 3.5, "hy": "", "en": ""}]
    (tmp_path / "hyp.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                        encoding="utf-8")
    cmd = [sys.executable, str(ROOT / "scripts" / "export_clips.py"), str(tmp_path), "--asr"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    out = tmp_path / "asr_test"
    refs = (out / "refs.tsv").read_text(encoding="utf-8").splitlines()
    assert refs == ["mic_01_0000.5s_Me.wav\tբարեւ\t", "sys_01_0002.0s_Speaker1.wav\tայո\t"]
    clip, sr = sf.read(out / "mic_01_0000.5s_Me.wav", dtype="float32")
    assert sr == SR and len(clip) == int(1.6 * SR) - int(0.3 * SR)      # 200 ms before, 100 ms after
    r2 = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert r2.returncode != 0 and "not overwriting" in r2.stderr


def test_asr_export_prefers_exact_live_phrase_audio(tmp_path):
    sf.write(tmp_path / "system.wav", np.zeros(SR * 4, np.float32), SR)
    (tmp_path / "phrases").mkdir()
    live = np.linspace(-0.5, 0.5, SR, dtype=np.float32)          # what the models got live
    sf.write(tmp_path / "phrases" / "system_001.wav", live, SR, subtype="FLOAT")
    row = {"source": "system", "label": "Speaker 1", "start_s": 2.0, "end_s": 3.0, "hy": "այո", "en": "yes",
           "clip": "phrases/system_001.wav"}
    (tmp_path / "hyp.jsonl").write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "export_clips.py"), str(tmp_path), "--asr"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert "1 exact live clips" in r.stdout
    got, _ = sf.read(tmp_path / "asr_test" / "sys_01_0002.0s_Speaker1.wav", dtype="float32")
    assert np.array_equal(got, live)
