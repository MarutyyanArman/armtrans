import numpy as np

from armtrans.config import SpeakerConfig
from armtrans.pipeline import Pipeline
from armtrans.clock import Clock
from armtrans.phrase import Phrase
from armtrans.speakers import SpeakerMatcher

SR = 16000
A = np.array([1.0, 0.0, 0.0], np.float32)
B = np.array([0.0, 1.0, 0.0], np.float32)
C = np.array([0.0, 0.0, 1.0], np.float32)


def near(v, other, w):
    """Unit vector with cosine similarity ~ (1-w) to v (mixes in a bit of `other`)."""
    x = (1 - w) * v + w * other
    return x / np.linalg.norm(x)


class FakeEmbedder:
    """Returns the embedding stored in the audio's first sample slot via a lookup table."""
    def __init__(self):
        self.table = {}

    def make(self, emb, seconds):
        key = len(self.table) + 1
        self.table[key] = emb
        audio = np.zeros(int(seconds * SR), np.float32)
        audio[0] = key
        return audio

    def __call__(self, audio):
        return self.table[int(audio[0])]


def matcher():
    e = FakeEmbedder()
    return e, SpeakerMatcher(e, SpeakerConfig(min_new_s=1.5, match=0.5))


def test_long_phrases_create_and_reuse_speakers():
    e, m = matcher()
    labels = [m.assign(e.make(v, 3)).label for v in (A, B, near(A, B, 0.2), C, near(B, C, 0.1))]
    assert labels == ["Speaker 1", "Speaker 2", "Speaker 1", "Speaker 3", "Speaker 2"]


def test_first_phrase_short_becomes_speaker_1():
    e, m = matcher()
    d = m.assign(e.make(A, 1.0))
    assert d.label == "Speaker 1"
    assert len(m.speakers) == 1


def test_short_phrase_gets_closest_speaker_never_unknown():
    e, m = matcher()
    m.assign(e.make(A, 3))
    m.assign(e.make(B, 3))
    assert m.assign(e.make(near(A, B, 0.1), 1.0)).label == "Speaker 1"
    assert m.assign(e.make(near(B, C, 0.1), 1.0)).label == "Speaker 2"


def test_mic_is_me_and_learns_my_voice_without_creating_speakers():
    e, m = matcher()
    assert m.assign_mic(e.make(A, 3), False).label == "Me"
    assert m.assign_mic(e.make(A, 1.0), False).label == "Me"
    assert m.speakers == []


def test_mic_hearing_the_call_gets_the_call_speaker_label():
    e, m = matcher()
    m.assign_mic(e.make(A, 3), False)
    m.assign(e.make(B, 3))
    m.assign(e.make(C, 3))
    assert m.assign_mic(e.make(near(C, B, 0.1), 2), True).label == "Speaker 2"
    assert m.assign_mic(e.make(near(A, B, 0.1), 2), True).label == "Me"
    assert len(m.speakers) == 2


def test_short_phrases_never_update_or_create():
    e, m = matcher()
    m.assign(e.make(A, 3))
    before = m.speakers[0].centroid.copy()
    m.assign(e.make(near(A, B, 0.2), 1.0))
    m.assign(e.make(C, 1.0))
    assert len(m.speakers) == 1
    assert np.allclose(m.speakers[0].centroid, before)


def test_labels_are_never_renumbered():
    e, m = matcher()
    seq = [A, B, C, A, B, C, A]
    labels = [m.assign(e.make(v, 2)).label for v in seq]
    assert labels == ["Speaker 1", "Speaker 2", "Speaker 3", "Speaker 1", "Speaker 2", "Speaker 3", "Speaker 1"]


class FakeASR:
    def __init__(self, text="Բարև"):
        self.text = text

    def transcribe(self, audio):
        return self.text


class FakeMT:
    def translate(self, text):
        return "Hello"


def run_pipeline(phrases, asr, speakers):
    lines = []
    from armtrans.timing import TimingLog
    log = TimingLog(None, 5.0)
    pipe = Pipeline(asr, FakeMT(), log, Clock(), printer=lines.append, speakers=speakers)
    for p in phrases:
        pipe.submit(p)
    pipe.start()
    pipe.drain_and_stop(timeout=10)
    return lines, log


def ph(source, audio, end_t):
    return Phrase(source=source, audio=audio, start_t=end_t - 1, end_t=end_t, emit_t=end_t + 0.6, cut_reason="pause")


def test_pipeline_labels_system_audio_and_keeps_mic_as_me():
    import time
    e, m = matcher()
    now = time.perf_counter()
    lines, log = run_pipeline([ph("system", e.make(A, 3), now), ph("mic", e.make(B, 3), now + 0.1),
                               ph("system", e.make(B, 3), now + 0.2)], FakeASR(), m)
    assert ["] Speaker 1 |" in lines[0], "] Me |" in lines[1], "] Speaker 2 |" in lines[2]] == [True] * 3
    assert len(m.speakers) == 2            # the mic phrase never reached the matcher
    assert log.records[0]["spk_reason"] == "new" and log.records[0]["spk_s"] is not None
    assert "system-audio labels: Speaker 1: 1, Speaker 2: 1" in log.summary()


def test_noise_with_no_recognised_text_cannot_create_a_speaker():
    import time
    e, m = matcher()
    lines, _ = run_pipeline([ph("system", e.make(A, 3), time.perf_counter())], FakeASR(text=""), m)
    assert lines == [] and m.speakers == []


def test_pipeline_mic_bleed_during_call_speech_is_labelled_as_call_speaker():
    import time
    e, m = matcher()
    now = time.perf_counter()
    lines, _ = run_pipeline([ph("mic", e.make(A, 3), now - 20),
                             ph("system", e.make(B, 3), now),
                             ph("mic", e.make(near(B, C, 0.05), 3), now + 0.1),
                             ph("mic", e.make(near(A, C, 0.05), 3), now + 30)],
                            FakeASR(), m)
    assert ["] Me |" in lines[0], "] Speaker 1 |" in lines[1], "] Speaker 1 |" in lines[2], "] Me |" in lines[3]] == [True] * 4
