import numpy as np
import pytest
import soundfile as sf

from armtrans.capture import CHUNK, Framer, ReplaySource
from armtrans.config import VadConfig
from armtrans.vad import CHUNK_MS, Segmenter, SileroVAD, StreamVad

SR = 16000


def test_framer_chunk_times_come_from_arrival_not_sample_count():
    out = []
    f = Framer(lambda c, t: out.append((len(c), t)))
    f.push(np.zeros(320, np.float32), 1.000)      # 20 ms block, no full chunk yet
    f.push(np.zeros(320, np.float32), 1.020)      # 640 samples -> one chunk ending 128 samples before block end
    assert out == [(CHUNK, pytest.approx(1.020 - 128 / SR))]
    # A 5 s gap (e.g. loopback silent): next chunk times follow the arrival clock, not sample count.
    # 128 leftover + 1024 new = 2 chunks; the last ends 128 samples before the block end.
    f.push(np.zeros(1024, np.float32), 6.020)
    assert [n for n, _ in out] == [CHUNK, CHUNK, CHUNK]
    assert out[-1][1] == pytest.approx(6.020 - 128 / SR)
    assert out[1][1] == pytest.approx(6.020 - (128 + CHUNK) / SR)


def run_probs(probs, cfg=None):
    """Feed a probability sequence; chunk i ends at (i+1)*32 ms.
    Default: fixed 5 s cap (max_phrase_solo_s = 5); the solo extension has its own tests."""
    cfg = cfg or VadConfig(max_phrase_solo_s=5.0)
    phrases, discards = [], []
    seg = Segmenter("mic", cfg, phrases.append, lambda *a: discards.append(a))
    for i, p in enumerate(probs):
        seg.process(np.full(CHUNK, i, np.float32), p, (i + 1) * CHUNK_MS / 1000)
    return phrases, discards


def n_chunks(ms):
    return int(round(ms / CHUNK_MS))


def test_pause_ends_phrase_after_min_silence():
    probs = [0.0] * 10 + [0.9] * n_chunks(1000) + [0.0] * n_chunks(1000)
    phrases, _ = run_probs(probs)
    assert len(phrases) == 1
    p = phrases[0]
    assert p.cut_reason == "pause"
    last_speech_idx = 10 + n_chunks(1000) - 1
    assert p.end_t == pytest.approx((last_speech_idx + 1) * CHUNK_MS / 1000)
    # Decision comes ~600 ms (rounded up to whole chunks) after the last speech chunk.
    assert 0.6 <= p.emit_t - p.end_t <= 0.6 + CHUNK_MS / 1000 + 1e-9
    # Pre-roll (200 ms) is included before the speech start.
    assert p.audio[0] < 10


def test_long_speech_is_cut_at_max_length_and_rest_is_kept():
    probs = [0.9] * n_chunks(8000) + [0.0] * n_chunks(1000)
    phrases, discards = run_probs(probs)
    assert [p.cut_reason for p in phrases] == ["max_len", "pause"]
    assert phrases[0].duration_s <= 6.0 + 0.1
    assert phrases[1].duration_s > 1.5
    assert not discards


def test_short_blip_is_discarded_and_reported():
    probs = [0.9] * 3 + [0.0] * n_chunks(1000)     # 96 ms < 250 ms
    phrases, discards = run_probs(probs)
    assert phrases == []
    assert len(discards) == 1


def test_brief_dip_does_not_split_phrase():
    probs = [0.9] * 20 + [0.0] * n_chunks(300) + [0.9] * 20 + [0.0] * n_chunks(1000)
    phrases, _ = run_probs(probs)
    assert len(phrases) == 1


def test_real_silero_model_runs_and_ignores_silence():
    from armtrans.config import VAD_MODEL
    if not VAD_MODEL.exists():
        pytest.skip("run: python scripts/download_models.py --vad")
    vad = SileroVAD()
    probs = [vad(np.zeros(CHUNK, np.float32)) for _ in range(30)]
    assert all(0.0 <= p <= 1.0 for p in probs)
    assert max(probs) < 0.5
    rng = np.random.default_rng(0)
    probs_noise = [vad((rng.standard_normal(CHUNK) * 0.01).astype(np.float32)) for _ in range(30)]
    assert max(probs_noise) < 0.5


class LoudnessVAD:
    """Test stand-in for Silero: 'speech' = loud chunk."""
    def __call__(self, chunk):
        return 0.9 if np.abs(chunk).mean() > 0.05 else 0.0


def test_replay_virtual_clock_puts_phrases_at_file_positions(tmp_path):
    audio = np.zeros(SR * 4, np.float32)
    audio[SR:SR * 2] = 0.3            # "speech" from 1.0 s to 2.0 s
    wav = tmp_path / "a.wav"
    sf.write(wav, audio, SR)
    phrases = []
    vad = StreamVad("system", VadConfig(), phrases.append, vad_model=LoudnessVAD())
    ReplaySource(str(wav), vad, realtime=False, start_t=100.0).run()
    assert len(phrases) == 1
    p = phrases[0]
    assert p.source == "system"
    assert p.start_t - 100.0 == pytest.approx(1.0, abs=0.04)
    assert p.end_t - 100.0 == pytest.approx(2.0, abs=0.04)


# --- Opt-in smarter cuts (VadConfig.smart_cut / adaptive_pause, 2026-11-01) ---

def smart_cfg(**kw):
    return VadConfig(smart_cut=True, max_phrase_solo_s=5.0, **kw)


def chunk_ids(p):
    """Chunk indices inside a phrase's audio (run_probs fills chunk i with the value i)."""
    return list(dict.fromkeys(int(v) for v in p.audio[::CHUNK]))


def test_smart_cut_lands_on_the_quietest_chunk_of_the_last_window():
    dip = n_chunks(4200)                                   # quiet spot ~0.8 s before the 5 s cap
    probs = [0.9] * n_chunks(8000) + [0.0] * n_chunks(1000)
    probs[dip] = 0.2
    probs[dip + 1] = 0.1                                   # lowest
    phrases, discards = run_probs(probs, smart_cfg())
    assert phrases[0].cut_reason == "smart_cut"
    assert chunk_ids(phrases[0])[-1] == dip + 1            # cut right after the quietest chunk
    assert phrases[0].cut_prob == pytest.approx(0.1)
    assert phrases[0].end_t == pytest.approx(dip * CHUNK_MS / 1000)   # end of the last speech chunk (dip - 1)
    assert chunk_ids(phrases[1])[0] == dip + 2             # carried audio starts the next phrase
    assert not discards


def test_smart_cut_drops_no_audio_and_keeps_the_cap():
    rng = np.random.default_rng(1)
    probs = list(rng.uniform(0.4, 1.0, n_chunks(20000))) + [0.0] * n_chunks(1000)
    phrases, discards = run_probs(probs, smart_cfg())
    assert not discards
    ids = [i for p in phrases for i in chunk_ids(p)]
    assert ids == list(range(ids[0], ids[-1] + 1))         # contiguous: nothing lost, nothing doubled
    assert ids[-1] >= n_chunks(20000) - 1                  # reaches the last speech chunk
    assert all(p.duration_s <= 5.0 + 0.2 + CHUNK_MS / 1000 for p in phrases)   # cap (+ pre-roll on the first)
    assert {p.cut_reason for p in phrases[:-1]} == {"smart_cut"}


def test_smart_cut_off_by_default_keeps_old_max_len_cut():
    assert VadConfig().smart_cut is False and VadConfig().adaptive_pause is False
    phrases, _ = run_probs([0.9] * n_chunks(8000) + [0.0] * n_chunks(1000))
    assert phrases[0].cut_reason == "max_len"


def test_adaptive_pause_ends_a_long_phrase_on_a_short_pause():
    probs = [0.9] * n_chunks(3500) + [0.0] * n_chunks(300) + [0.9] * n_chunks(1000) + [0.0] * n_chunks(1000)
    fixed, _ = run_probs(probs)
    assert len(fixed) == 1                                  # 300 ms < 600 ms: one phrase
    adaptive, _ = run_probs(probs, VadConfig(adaptive_pause=True, max_phrase_solo_s=5.0))
    assert [p.cut_reason for p in adaptive] == ["pause", "pause"]
    assert adaptive[0].end_t == pytest.approx(n_chunks(3500) * CHUNK_MS / 1000)


def test_adaptive_pause_keeps_600_ms_early_in_a_phrase():
    probs = [0.9] * n_chunks(1000) + [0.0] * n_chunks(300) + [0.9] * n_chunks(1000) + [0.0] * n_chunks(1000)
    phrases, _ = run_probs(probs, VadConfig(adaptive_pause=True, max_phrase_solo_s=5.0))
    assert len(phrases) == 1


def test_energy_smart_cut_splits_inside_a_quiet_gap_and_keeps_every_sample():
    total = n_chunks(8000)
    audio = np.full(total * CHUNK, 0.3, np.float32)
    gap = int(4.3 * SR) + 37                           # 10 ms near-silent gap, not on a chunk edge
    audio[gap:gap + 160] = 0.001
    cfg = VadConfig(smart_cut=True, smart_cut_by="energy", max_phrase_solo_s=5.0)
    phrases, discards = [], []
    seg = Segmenter("mic", cfg, phrases.append, lambda *a: discards.append(a))
    for i in range(total):                             # VAD says "speech" everywhere
        seg.process(audio[i * CHUNK:(i + 1) * CHUNK], 0.9, (i + 1) * CHUNK_MS / 1000)
    for i in range(n_chunks(1000)):
        seg.process(np.zeros(CHUNK, np.float32), 0.0, (total + i + 1) * CHUNK_MS / 1000)
    first = phrases[0]
    assert first.cut_reason == "smart_cut"
    assert gap <= len(first.audio) <= gap + 160        # cut inside the gap
    assert first.end_t == pytest.approx(len(first.audio) / SR)
    assert type(first.end_t) is float and type(phrases[1].start_t) is float   # numpy types broke the JSON log
    assert phrases[1].start_t == pytest.approx(first.end_t)
    assert sum(len(p.audio) for p in phrases) >= total * CHUNK   # nothing lost (last one adds its tail)
    joined = np.concatenate([p.audio for p in phrases])[:total * CHUNK]
    assert np.array_equal(joined, audio)               # nothing doubled or reordered
    assert not discards


def test_unknown_smart_cut_mode_fails_loudly():
    with pytest.raises(ValueError):
        run_probs([0.9] * n_chunks(6000), VadConfig(smart_cut=True, smart_cut_by="magic", max_phrase_solo_s=5.0))


def _feed(seg, audio, total, probs=0.9):
    for i in range(total):
        seg.process(audio[i * CHUNK:(i + 1) * CHUNK], probs, (i + 1) * CHUNK_MS / 1000)
    for i in range(n_chunks(1000)):
        seg.process(np.zeros(CHUNK, np.float32), 0.0, (total + i + 1) * CHUNK_MS / 1000)


def test_asr_word_cut_cuts_just_before_the_last_word_start_and_keeps_every_sample():
    total = n_chunks(12000)
    audio = np.full(total * CHUNK, 0.3, np.float32)
    gap = int(4.30 * SR)
    audio[gap:gap + 320] = 0.001                      # 20 ms gap just before a word at ~4.32 s
    audio[int(3.7 * SR):int(3.7 * SR) + 160] = 0.0005  # quieter dip elsewhere in the window: must NOT win
    calls = []

    def word_starts(a):
        calls.append(len(a))
        return [0.5, 2.0, 4.32] if len(calls) == 1 else []

    phrases = []
    seg = Segmenter("mic", VadConfig(smart_cut=True, smart_cut_by="asr", max_phrase_solo_s=5.0), phrases.append,
                    word_starts=word_starts)
    _feed(seg, audio, total)
    first = phrases[0]
    assert first.cut_reason == "word_cut"
    assert gap <= len(first.audio) <= gap + 320      # quietest point near the word start, not the 3.7 s dip
    assert first.cut_asr_s is not None and first.cut_asr_s >= 0
    joined = np.concatenate([p.audio for p in phrases])[:total * CHUNK]
    assert np.array_equal(joined, audio)
    assert phrases[1].cut_reason == "smart_cut"       # 2nd cut: no word start in window -> quietest 10 ms, labelled


def test_asr_word_cut_needs_a_word_start_function():
    with pytest.raises(ValueError):
        Segmenter("mic", VadConfig(smart_cut=True, smart_cut_by="asr"), lambda p: None)


def test_asr_word_starts_keeps_only_tokens_that_begin_a_word():
    import threading
    from types import SimpleNamespace
    from armtrans.asr import FastConformerASR
    asr = object.__new__(FastConformerASR)
    asr._lock = threading.Lock()
    asr._ts_model = SimpleNamespace(recognize=lambda a, sample_rate: SimpleNamespace(
        tokens=[" Կ", "իր", " բեր", ",", " ու"], timestamps=[0.0, 0.16, 1.04, 1.44, 1.6]))
    assert asr.word_starts(np.zeros(SR, np.float32)) == [0.0, 1.04, 1.6]


# --- Longer phrases while the other stream is silent (max_phrase_solo_s, 2026-11-01) ---

def _linked(probs_a, probs_b, cfg=None):
    """Two linked streams fed chunk by chunk in step (like live capture)."""
    from armtrans.vad import link_streams
    cfg = cfg or VadConfig()
    out = {"mic": [], "system": []}
    vads = []
    for name in ("mic", "system"):
        v = object.__new__(StreamVad)
        v.seg = Segmenter(name, cfg, out[name].append)
        vads.append(v)
    link_streams(vads)
    for i in range(max(len(probs_a), len(probs_b))):
        t = (i + 1) * CHUNK_MS / 1000
        for v, probs in zip(vads, (probs_a, probs_b)):
            if i < len(probs):
                v.seg.process(np.full(CHUNK, i, np.float32), probs[i], t)
    return out["mic"], out["system"]


def test_alone_a_phrase_may_grow_to_the_solo_cap_and_ends_on_its_pause():
    phrases, _ = run_probs([0.9] * n_chunks(7000) + [0.0] * n_chunks(1000), VadConfig())
    assert [p.cut_reason for p in phrases] == ["pause"]
    assert phrases[0].duration_s == pytest.approx(7.0 + 0.1, abs=0.05)    # + tail (speech starts at chunk 0: no pre-roll)


def test_alone_the_solo_cap_is_a_hard_limit():
    phrases, _ = run_probs([0.9] * n_chunks(12000) + [0.0] * n_chunks(1000), VadConfig())
    assert phrases[0].cut_reason == "max_len"
    assert 8.0 <= phrases[0].duration_s <= 8.0 + 0.2 + 0.05
    assert all(p.duration_s <= 8.25 for p in phrases)


def test_other_stream_starting_to_talk_cuts_a_long_phrase_at_once():
    a = [0.9] * n_chunks(10000) + [0.0] * n_chunks(1000)
    b = [0.0] * n_chunks(6000) + [0.9] * n_chunks(1000) + [0.0] * n_chunks(1000)
    mic, system = _linked(a, b)
    assert mic[0].cut_reason == "max_len"
    b_start_end = (n_chunks(6000) + 1) * CHUNK_MS / 1000          # end of B's first speech chunk
    assert b_start_end <= mic[0].end_t <= b_start_end + CHUNK_MS / 1000 + 1e-6   # cut on A's next chunk
    assert len(system) == 1


def test_both_talking_keeps_the_5_s_cut():
    a = [0.9] * n_chunks(9000) + [0.0] * n_chunks(1000)
    mic, system = _linked(a, list(a))
    assert mic[0].cut_reason == system[0].cut_reason == "max_len"
    assert mic[0].duration_s <= 5.0 + 0.2 + CHUNK_MS / 1000
    assert system[0].duration_s <= 5.0 + 0.2 + CHUNK_MS / 1000


def test_solo_cap_equal_to_max_phrase_restores_old_behaviour():
    phrases, _ = run_probs([0.9] * n_chunks(8000) + [0.0] * n_chunks(1000), VadConfig(max_phrase_solo_s=5.0))
    assert phrases[0].cut_reason == "max_len" and phrases[0].duration_s <= 5.0 + 0.2 + CHUNK_MS / 1000


# --- ASR context after a forced cut (AsrConfig.context_s, 2026-11-01) ---

def test_phrase_after_a_forced_cut_carries_the_end_of_the_previous_one_as_context():
    phrases = []
    seg = Segmenter("mic", VadConfig(max_phrase_solo_s=5.0), phrases.append, context_s=1.5)
    probs = [0.9] * n_chunks(8000) + [0.0] * n_chunks(1000) + [0.9] * n_chunks(1000) + [0.0] * n_chunks(1000)
    for i, p in enumerate(probs):
        seg.process(np.full(CHUNK, i, np.float32), p, (i + 1) * CHUNK_MS / 1000)
    assert [p.cut_reason for p in phrases] == ["max_len", "pause", "pause"]
    assert phrases[0].context is None                       # nothing before it
    ctx = phrases[1].context
    assert len(ctx) == int(1.5 * SR)
    assert np.array_equal(ctx, phrases[0].audio[-len(ctx):])  # exactly the end of the previous phrase
    assert phrases[2].context is None                       # after a pause: no context


def test_no_context_when_switched_off():
    phrases, _ = run_probs([0.9] * n_chunks(8000) + [0.0] * n_chunks(1000))
    assert all(p.context is None for p in phrases)
