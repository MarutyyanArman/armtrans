import json
import time

import numpy as np

from armtrans.clock import Clock
from armtrans.phrase import Phrase
from armtrans.pipeline import Pipeline
from armtrans.timing import TimingLog


class FakeASR:
    def __init__(self, delay=0.0, text="Բարև", fail=False):
        self.delay, self.text, self.fail, self.seen = delay, text, fail, []

    def transcribe(self, audio):
        if self.fail:
            raise RuntimeError("boom")
        self.seen.append(len(audio))
        time.sleep(self.delay)
        return self.text


class FakeMT:
    def translate(self, text):
        return "Hello" if text else ""


def phrase(source, end_t, n=16000, seq=1):
    return Phrase(source=source, audio=np.zeros(n, np.float32), start_t=end_t - 1.0,
                  end_t=end_t, emit_t=end_t + 0.6, cut_reason="pause", seq=seq)


def run(phrases, asr, limit=5.0, tmp_path=None):
    lines = []
    clock = Clock()
    log = TimingLog(tmp_path / "t.jsonl" if tmp_path else None, limit)
    pipe = Pipeline(asr, FakeMT(), log, clock, printer=lines.append)
    for p in phrases:
        pipe.submit(p)
    pipe.start()
    pipe.drain_and_stop(timeout=10)
    log.close()
    return lines, log, pipe


def test_labels_and_line_format():
    now = time.perf_counter()
    lines, log, _ = run([phrase("mic", now), phrase("system", now + 0.01)], FakeASR())
    assert len(lines) == 2
    assert "] Me | Բարև | Hello" in lines[0]
    assert "] Speaker ? | Բարև | Hello" in lines[1]
    assert lines[0].startswith("[") and lines[0][9] == "]"      # [HH:MM:SS]


def test_oldest_phrase_is_processed_first():
    now = time.perf_counter()
    asr = FakeASR()
    newer = phrase("mic", now, n=100)
    older = phrase("system", now - 1.0, n=200)
    run([newer, older], asr)
    assert asr.seen == [200, 100]


def test_late_phrase_is_still_printed_and_counted(tmp_path):
    now = time.perf_counter()
    lines, log, _ = run([phrase("mic", now)], FakeASR(delay=0.3), limit=0.1, tmp_path=tmp_path)
    assert len(lines) == 1
    rec = json.loads((tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["late"] is True
    assert rec["latency_s"] >= 0.3
    assert "over 0s: 1 (100%)" in log.summary()


def test_log_contains_timings_but_no_transcript_text(tmp_path):
    lines, log, _ = run([phrase("mic", time.perf_counter())], FakeASR(), tmp_path=tmp_path)
    raw = (tmp_path / "t.jsonl").read_text(encoding="utf-8")
    assert "Բարև" not in raw and "Hello" not in raw
    rec = json.loads(raw.splitlines()[0])
    for k in ("vad_wait_s", "queue_wait_s", "asr_s", "mt_s", "latency_s", "late"):
        assert k in rec


def test_empty_asr_is_not_printed_but_is_logged(tmp_path):
    lines, log, _ = run([phrase("mic", time.perf_counter())], FakeASR(text=""), tmp_path=tmp_path)
    assert lines == []
    assert log.records[0]["empty_asr"] is True
    assert "empty ASR (not printed): 1" in log.summary()


def test_worker_error_is_shown_and_stops_pipeline():
    lines, _, pipe = run([phrase("mic", time.perf_counter())], FakeASR(fail=True))
    assert pipe.error is not None
    assert any(l.startswith("[ERROR]") for l in lines)


class ContextASR(FakeASR):
    def __init__(self):
        super().__init__()
        self.with_ctx = []

    def transcribe_with_context(self, audio, context):
        self.with_ctx.append((len(audio), len(context)))
        return "նոր"


def test_phrase_with_context_is_recognised_with_it():
    now = time.perf_counter()
    p1 = phrase("mic", now, seq=1)
    p2 = phrase("mic", now + 0.01, n=8000, seq=2)
    p2.context = np.zeros(24000, np.float32)
    asr = ContextASR()
    lines, _, _ = run([p1, p2], asr)
    assert asr.seen == [16000]                  # plain call for the phrase without context
    assert asr.with_ctx == [(8000, 24000)]
    assert "| նոր | Hello" in lines[1]
