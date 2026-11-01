"""Shared queue (oldest phrase first) -> ASR -> translation -> one printed line."""
import collections
import itertools
import queue
import threading
import time
from typing import Callable, Optional

from .clock import Clock
from .phrase import Phrase
from .timing import TimingLog

LABEL_ME = "Me"
LABEL_UNKNOWN = "Speaker ?"     # system phrase when no speaker matcher is loaded, or unclear short phrase


def format_line(clock: Clock, p: Phrase) -> str:
    return f"[{clock.to_wall(p.start_t):%H:%M:%S}] {p.label} | {p.hy_text} | {p.en_text}"


class Pipeline:
    def __init__(self, asr, mt, timing: TimingLog, clock: Clock,
                 printer: Callable[[str], None] = print,
                 on_done: Optional[Callable[[Phrase], None]] = None,
                 speakers=None):
        self.asr = asr
        self.mt = mt
        self.speakers = speakers          # SpeakerMatcher for system audio, or None
        self.timing = timing
        self.clock = clock
        self.printer = printer
        self.on_done = on_done
        self._q: "queue.PriorityQueue" = queue.PriorityQueue()
        self._tie = itertools.count()
        self._call_spans = collections.deque(maxlen=50)   # (start_t, end_t) of recent system phrases
        self._stop = threading.Event()
        self.error: Optional[Exception] = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="asr-mt-worker")

    def submit(self, p: Phrase):
        # Oldest phrase end first, so a phrase never waits behind a newer one.
        if p.source == "system":
            self._call_spans.append((p.start_t, p.end_t))
        self._q.put((p.end_t, next(self._tie), p))

    def start(self):
        self._thread.start()

    def pending(self) -> int:
        return self._q.qsize()

    def drain_and_stop(self, timeout: float = 60):
        """Finish queued phrases (unless the worker already failed), then stop."""
        deadline = time.perf_counter() + timeout
        while self._q.unfinished_tasks and self.error is None and time.perf_counter() < deadline:
            time.sleep(0.05)
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self):
        while not self._stop.is_set():
            try:
                _, _, p = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self._process(p)
            except Exception as e:   # visible, never swallowed: the app stops on the first failure
                self.error = e
                self.printer(f"[ERROR] phrase {p.source}#{p.seq} failed: {type(e).__name__}: {e}")
                self._stop.set()
            finally:
                self._q.task_done()
        # Unblock drain_and_stop if we stopped because of an error.
        while True:
            try:
                self._q.get_nowait()
                self._q.task_done()
            except queue.Empty:
                break

    def _process(self, p: Phrase):
        now = self.clock.now
        p.times["asr_start"] = now()
        if p.context is not None:
            p.hy_text = self.asr.transcribe_with_context(p.audio, p.context)
        else:
            p.hy_text = self.asr.transcribe(p.audio)
        p.times["asr_end"] = now()
        if p.source == "mic":
            p.label = LABEL_ME
            if p.hy_text and self.speakers is not None:
                m = 0.5
                over = any(a < p.end_t + m and b > p.start_t - m for a, b in list(self._call_spans))
                p.speaker = self.speakers.assign_mic(p.audio, over)
                p.label = p.speaker.label
                p.times["spk_end"] = now()
        else:
            p.label = LABEL_UNKNOWN
            # Only phrases with recognised speech reach the matcher, so noise can't create speakers.
            if p.hy_text and self.speakers is not None:
                p.speaker = self.speakers.assign(p.audio)
                p.label = p.speaker.label
                p.times["spk_end"] = now()
        if p.hy_text:
            p.times["mt_start"] = now()
            p.en_text = self.mt.translate(p.hy_text)
            p.times["mt_end"] = now()
            self.printer(format_line(self.clock, p))
        p.times["done"] = now()
        self.timing.phrase(p)
        if self.on_done:
            self.on_done(p)
