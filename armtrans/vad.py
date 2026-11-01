"""Speech detection: official Silero VAD ONNX (MIT, from the silero-vad 6.2.3 package; no PyTorch) + phrase segmenter.

ONNX signature verified on 2026-10-31 (silero_vad.onnx from silero-vad 6.2.3):
  inputs  input[batch, 576] (64 context + 512 new samples at 16 kHz), state[2, batch, 128], sr (int64 scalar)
  outputs output[batch, 1] (speech probability), stateN[2, batch, 128]
"""
import time
from collections import deque
from typing import Callable, Optional

import numpy as np

from . import SAMPLE_RATE
from .capture import CHUNK
from .config import VAD_MODEL, VadConfig
from .phrase import Phrase

CONTEXT = 64
CHUNK_MS = CHUNK * 1000 / SAMPLE_RATE      # 32 ms


class ModelMissing(Exception):
    pass


class SileroVAD:
    """Streaming wrapper: one 512-sample chunk in, one speech probability out. One instance per stream."""

    def __init__(self, path=VAD_MODEL):
        import onnxruntime as ort
        if not path.exists():
            raise ModelMissing(f"VAD model {path} missing. Run: python scripts/download_models.py --vad")
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        self.session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"], sess_options=opts)
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), np.float32)
        self.context = np.zeros(CONTEXT, np.float32)

    def __call__(self, chunk: np.ndarray) -> float:
        assert len(chunk) == CHUNK
        x = np.concatenate([self.context, chunk.astype(np.float32)])[None, :]
        prob, self.state = self.session.run(None, {"input": x, "state": self.state, "sr": self._sr})
        self.context = chunk[-CONTEXT:].astype(np.float32)
        return float(np.asarray(prob).reshape(-1)[0])


class Segmenter:
    """Turns (chunk, prob, chunk_end_t) into Phrases.

    A phrase ends after `min_silence_ms` of low probability ("pause"), or is cut at
    `max_phrase_s` ("max_len"). While every other stream (`others`) is silent, the cut waits
    up to `max_phrase_solo_s` (a pause may still end the phrase earlier); as soon as another
    stream is in speech, a phrase already past `max_phrase_s` is cut at once. Blips shorter than `min_speech_ms` are reported to
    `on_discard` instead of being dropped silently.

    Opt-in (VadConfig, default off; DECISIONS.md 2026-11-01 "Smarter phrase cuts"):
    - adaptive_pause: the pause that ends a phrase shrinks as the phrase grows (`pause_steps`).
    - smart_cut: at `max_phrase_s`, cut at the lowest-probability chunk of the last
      `smart_cut_window_s` ("smart_cut"; quietest = lowest VAD probability, or lowest
      10 ms loudness with smart_cut_by="energy", or just before the last ASR word start with
      smart_cut_by="asr": "word_cut") and carry the audio after it into the next phrase.
      Nothing is dropped; the 5 s cap still holds because the carried audio counts toward
      the next phrase's length.
    """

    def __init__(self, source: str, cfg: VadConfig, on_phrase: Callable[[Phrase], None],
                 on_discard: Optional[Callable[[str, float, float], None]] = None,
                 now: Callable[[], float] = None,
                 word_starts: Optional[Callable[[np.ndarray], list]] = None,
                 context_s: float = 0.0):
        self.source = source
        self.cfg = cfg
        self.word_starts = word_starts
        self.others: list = []             # Segmenters of the other streams (set by the app)
        self._context_n = int(round(context_s * SAMPLE_RATE))
        self._ctx = None                   # tail of the previous phrase, for the phrase that continues it
        if cfg.smart_cut and cfg.smart_cut_by == "asr" and word_starts is None:
            raise ValueError("smart_cut_by='asr' needs an ASR word_starts function")
        self.on_phrase = on_phrase
        self.on_discard = on_discard or (lambda *a: None)
        self.now = now
        self.seq = 0
        self._preroll = deque(maxlen=max(1, int(cfg.preroll_ms / CHUNK_MS)))   # (chunk, prob, end_t)
        self._tail_chunks = int(round(cfg.tail_ms / CHUNK_MS))
        self._window_chunks = max(1, int(round(cfg.smart_cut_window_s * 1000 / CHUNK_MS)))
        self._reset()

    def _reset(self):
        self.in_speech = False
        self.chunks: list[np.ndarray] = []
        self.probs: list[float] = []
        self.ends: list[float] = []        # chunk end times
        self.start_t = 0.0
        self.last_speech_t = 0.0
        self.last_speech_idx = -1          # index into self.chunks
        self.speech_chunks = 0
        self.silence_ms = 0.0
        self.continuation = False
        self.first_idx = 0                 # first non-pre-roll chunk
        self._ctx = None

    def _may_extend(self, length_s: float) -> bool:
        """Past max_phrase_s: keep growing only while every other stream is silent, up to max_phrase_solo_s."""
        if length_s >= self.cfg.max_phrase_solo_s:
            return False
        return not any(o.in_speech for o in self.others)

    def required_silence_ms(self, length_s: float) -> float:
        ms = self.cfg.min_silence_ms
        if self.cfg.adaptive_pause:
            for after_s, pause_ms in self.cfg.pause_steps:
                if length_s >= after_s:
                    ms = min(ms, pause_ms)
        return ms

    def process(self, chunk: np.ndarray, prob: float, chunk_end_t: float):
        cfg = self.cfg
        if not self.in_speech:
            if prob >= cfg.threshold:
                self.in_speech = True
                pre = list(self._preroll)
                self.chunks = [c for c, _, _ in pre] + [chunk]
                self.probs = [q for _, q, _ in pre] + [prob]
                self.ends = [t for _, _, t in pre] + [chunk_end_t]
                self.start_t = chunk_end_t - CHUNK_MS / 1000
                self.last_speech_t = chunk_end_t
                self.last_speech_idx = len(self.chunks) - 1
                self.speech_chunks = 1
                self.first_idx = len(pre)
                self.silence_ms = 0.0
                self._preroll.clear()
            else:
                self._preroll.append((chunk, prob, chunk_end_t))
            return

        self.chunks.append(chunk)
        self.probs.append(prob)
        self.ends.append(chunk_end_t)
        if prob >= cfg.neg_threshold:
            self.last_speech_t = chunk_end_t
            self.last_speech_idx = len(self.chunks) - 1
            self.speech_chunks += 1
            self.silence_ms = 0.0
        else:
            self.silence_ms += CHUNK_MS

        length_s = chunk_end_t - self.start_t
        if self.silence_ms >= self.required_silence_ms(length_s):
            self._finish("pause", chunk_end_t)
        elif length_s >= cfg.max_phrase_s and not self._may_extend(length_s):
            if cfg.smart_cut:
                self._smart_cut(chunk_end_t)
            else:
                self._finish("max_len", chunk_end_t, continue_speech=True)

    @staticmethod
    def _quietest(audio: np.ndarray, a: int, b: int) -> int:
        """Sample position in the middle of the quietest 10 ms frame of audio[a:b] (latest on ties)."""
        f = SAMPLE_RATE // 100
        m = (b - a) // f
        if m < 1:
            return a
        x = audio[a:a + m * f].reshape(m, f).astype(np.float64)
        rms = np.sqrt(np.mean(x ** 2, axis=1))
        j = m - 1 - int(np.argmin(rms[::-1]))
        return int(a + j * f + f // 2)

    def _emit(self, audio, end_t, speech_chunks, reason, decided_t, cut_prob, cut_asr_s=None):
        speech_ms = speech_chunks * CHUNK_MS
        emit_t = self.now() if self.now else decided_t
        # The rest of a force-cut phrase is kept even if short (it is the end of real speech),
        # unless it contains no speech at all.
        too_short = (speech_chunks == 0 if self.continuation
                     else speech_ms < self.cfg.min_speech_ms)
        if too_short:
            self.on_discard(self.source, self.start_t, speech_ms)
        else:
            self.seq += 1
            ctx = self._ctx if (self.continuation and self._ctx is not None and len(self._ctx)) else None
            self.on_phrase(Phrase(source=self.source, audio=audio, start_t=self.start_t,
                                  end_t=end_t, emit_t=emit_t, cut_reason=reason, seq=self.seq,
                                  cut_prob=cut_prob, cut_asr_s=cut_asr_s, context=ctx))
        # After a forced cut the next phrase continues this audio: keep its end as context.
        if reason != "pause" and self._context_n > 0:
            prev = self._ctx if self.continuation and self._ctx is not None else np.zeros(0, np.float32)
            self._ctx = np.concatenate([prev, audio])[-self._context_n:]

    def _finish(self, reason: str, decided_t: float, continue_speech: bool = False):
        keep = min(len(self.chunks), self.last_speech_idx + 1 + self._tail_chunks)
        cut_prob = self.probs[-1] if reason == "max_len" else None
        self._emit(np.concatenate(self.chunks[:keep]), self.last_speech_t, self.speech_chunks,
                   reason, decided_t, cut_prob)
        if continue_speech:
            # Forced cut: the speaker is still talking; start the next phrase right away.
            self.chunks, self.probs, self.ends = [], [], []
            self.start_t = decided_t
            self.last_speech_t = decided_t
            self.last_speech_idx = -1
            self.speech_chunks = 0
            self.silence_ms = 0.0
            self.continuation = True
            self.first_idx = 0
        else:
            self._reset()

    def _smart_cut(self, decided_t: float):
        n = len(self.chunks)
        lo = max(1, n - self._window_chunks)
        lens = [len(c) for c in self.chunks]
        offs = np.concatenate([[0], np.cumsum(lens)]).astype(int)    # sample offset of each chunk
        full = np.concatenate(self.chunks)
        mode, reason, cut_asr_s = self.cfg.smart_cut_by, "smart_cut", None
        if mode == "energy":
            # Quietest 10 ms frame in the window (word gaps dip in loudness even when VAD says speech).
            c = self._quietest(full, int(offs[lo]), len(full))
        elif mode == "vad":
            # Quietest chunk in the window; on ties the latest one (keeps the carried part short).
            k = min(range(lo, n), key=lambda i: (self.probs[i], -i))
            c = int(offs[k + 1])
        elif mode == "asr":
            # Cut just before the last word that starts inside the window (it may be unfinished),
            # at the quietest 10 ms within -120..+40 ms of the ASR word start (timestamps are 80 ms steps).
            t0 = time.perf_counter()
            starts = self.word_starts(full)
            cut_asr_s = time.perf_counter() - t0
            cands = [int(round(t * SAMPLE_RATE)) for t in starts]
            cands = [w for w in cands if offs[lo] <= w < len(full)]
            if cands:
                w = cands[-1]
                c = self._quietest(full, max(1, w - int(0.12 * SAMPLE_RATE)), min(len(full), w + int(0.04 * SAMPLE_RATE)))
                reason = "word_cut"
            else:
                # No word starts in the window (one long word / no text): quietest 10 ms instead.
                # Visible as cut_reason "smart_cut" in the timing log, not hidden.
                c = self._quietest(full, int(offs[lo]), len(full))
        else:
            raise ValueError(f"unknown smart_cut_by {mode!r} (use 'vad', 'energy' or 'asr')")
        c = max(1, c)
        # Chunk i holds the cut sample (or ends exactly at it).
        i = int(np.searchsorted(offs, c, side="left")) - 1
        inside = int(c - offs[i])                                     # samples of chunk i kept in the head
        cut_t = float(self.ends[i] - (lens[i] - inside) / SAMPLE_RATE)   # plain float: goes into JSON logs
        neg = self.cfg.neg_threshold
        # Count speech like streaming does: pre-roll chunks never count.
        head_speech = [x for x in range(self.first_idx, i + 1) if self.probs[x] >= neg]
        if head_speech and head_speech[-1] == i:
            head_end_t = cut_t
        elif head_speech:
            head_end_t = self.ends[head_speech[-1]]
        else:
            head_end_t = cut_t
        self._emit(full[:c], head_end_t, len(head_speech), reason, decided_t, float(self.probs[i]), cut_asr_s)

        # Carry the audio after the cut into the next phrase.
        if inside < lens[i]:
            rest_c = [self.chunks[i][inside:]] + self.chunks[i + 1:]
            rest_p, rest_e = self.probs[i:], self.ends[i:]
        else:
            rest_c, rest_p, rest_e = self.chunks[i + 1:], self.probs[i + 1:], self.ends[i + 1:]
        self.chunks, self.probs, self.ends = rest_c, rest_p, rest_e
        self.start_t = cut_t if rest_c else decided_t
        speech_idx = [x for x, q in enumerate(rest_p) if q >= neg]
        self.speech_chunks = len(speech_idx)
        self.last_speech_idx = speech_idx[-1] if speech_idx else -1
        self.last_speech_t = rest_e[self.last_speech_idx] if speech_idx else self.start_t
        trailing = len(rest_p) - 1 - self.last_speech_idx
        self.silence_ms = trailing * CHUNK_MS
        self.continuation = True
        self.first_idx = 0


def link_streams(vads):
    """Let each stream's segmenter see whether the others are in speech (for max_phrase_solo_s)."""
    for v in vads:
        v.seg.others = [o.seg for o in vads if o is not v]


class StreamVad:
    """Glue: chunk sink for capture -> VAD prob -> segmenter. One per audio stream."""

    def __init__(self, source: str, cfg: VadConfig, on_phrase, on_discard=None,
                 vad_model=None, now=None, word_starts=None, context_s: float = 0.0):
        self.vad = vad_model if vad_model is not None else SileroVAD()
        self.seg = Segmenter(source, cfg, on_phrase, on_discard, now=now, word_starts=word_starts,
                             context_s=context_s)

    def __call__(self, chunk: np.ndarray, chunk_end_t: float):
        self.seg.process(chunk, self.vad(chunk), chunk_end_t)
