"""Audio sources -> 512-sample 16 kHz chunks, each stamped with the time its last sample arrived.

Times come from the arrival clock, never from counting samples, so gaps in a stream
(e.g. loopback delivering nothing during silence) cannot cause drift.
"""
import queue
import threading
import time
from typing import Callable, Optional

import numpy as np
import soxr

from . import SAMPLE_RATE

CHUNK = 512                 # Silero VAD window at 16 kHz (32 ms), verified from the ONNX input shape
ChunkSink = Callable[[np.ndarray, float], None]   # (512 samples, chunk_end_time)


class Framer:
    """Cuts arbitrary-size blocks into fixed 512-sample chunks with per-chunk end times."""

    def __init__(self, sink: ChunkSink, sr: int = SAMPLE_RATE):
        self.sink = sink
        self.sr = sr
        self._buf = np.zeros(0, dtype=np.float32)

    def push(self, block: np.ndarray, block_end_t: float):
        """block: 16 kHz mono float32. block_end_t: arrival time of its last sample."""
        prev = len(self._buf)
        self._buf = np.concatenate([self._buf, block.astype(np.float32, copy=False)])
        n_block = len(block)
        pos = 0
        while len(self._buf) - pos >= CHUNK:
            chunk_end_idx = pos + CHUNK                     # index in combined buffer
            idx_in_block = chunk_end_idx - prev             # index in this block (>0 by construction)
            t = block_end_t - (n_block - idx_in_block) / self.sr
            self.sink(self._buf[pos:chunk_end_idx], t)
            pos = chunk_end_idx
        self._buf = self._buf[pos:]


def to_mono(x: np.ndarray) -> np.ndarray:
    return x if x.ndim == 1 else x.mean(axis=1)


class MicCapture:
    """Live microphone via sounddevice. The audio callback only copies + timestamps."""

    def __init__(self, device: dict, sink: ChunkSink, block_ms: int = 20):
        self.device = device
        self.in_sr = device["samplerate"]
        self.block = int(self.in_sr * block_ms / 1000)
        self._consumer = _Consumer(self.in_sr, sink, "mic-consumer", fill_gaps=False)
        self._stream = None

    @property
    def overflows(self):
        return self._consumer.overflows

    def _callback(self, indata, frames, time_info, status):
        t = time.perf_counter()
        if status.input_overflow:
            self._consumer.overflows += 1
        self._consumer.put(to_mono(indata).copy(), t)

    def start(self):
        import sounddevice as sd
        self._stream = sd.InputStream(samplerate=self.in_sr, blocksize=self.block,
                                      device=self.device["index"], channels=1,
                                      dtype="float32", callback=self._callback)
        self._consumer.start()
        self._stream.start()

    def stop(self):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
        self._consumer.stop()


class _Consumer:
    """Thread that resamples incoming blocks to 16 kHz and feeds the Framer.

    fill_gaps=True (loopback): Windows loopback may send nothing while nothing is playing. Then the VAD
    would never see the pause that ends a phrase. When no block has arrived for `gap_s`, silence is
    pushed for the missing time, stamped with the clock, so phrases still end on time.
    """

    def __init__(self, in_sr: int, sink: ChunkSink, name: str, fill_gaps: bool, gap_s: float = 0.1,
                 now: Callable[[], float] = time.perf_counter):
        self.in_sr = in_sr
        self.fill_gaps = fill_gaps
        self.gap_s = gap_s
        self.now = now
        self._q: "queue.Queue[tuple[np.ndarray, float]]" = queue.Queue()
        self._rs = soxr.ResampleStream(in_sr, SAMPLE_RATE, 1, dtype="float32")
        self._framer = Framer(sink)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name=name)
        self.overflows = 0
        self.gap_fills = 0
        self.last_t: Optional[float] = None

    def put(self, mono: np.ndarray, t: float):
        self._q.put((mono, t))

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=1)

    def step(self, timeout: float = 0.05):
        """Process one queued block, or fill a gap. Separate from the thread loop so tests can drive it."""
        try:
            x, t = self._q.get(timeout=timeout)
        except queue.Empty:
            if self.fill_gaps and self.last_t is not None:
                now = self.now()
                gap = now - self.last_t
                if gap >= self.gap_s:
                    n = int(round(min(gap, 2.0) * SAMPLE_RATE))
                    self._framer.push(np.zeros(n, np.float32), now)
                    self.last_t = now
                    self.gap_fills += 1
            return
        y = self._rs.resample_chunk(x.astype(np.float32))
        if len(y):
            self._framer.push(y, t)
        self.last_t = t

    def _run(self):
        while not self._stop.is_set():
            self.step()


class SystemCapture:
    """System audio (what the laptop plays, i.e. the call) via WASAPI loopback with PyAudioWPatch.

    PyAudioWPatch API verified on the laptop 2026-10-31 (0.2.12.9): get_default_wasapi_loopback(),
    get_loopback_device_info_generator(); loopback devices are input devices with isLoopbackDevice=True.
    """

    def __init__(self, sink: ChunkSink, index: Optional[int] = None, block_ms: int = 20):
        import pyaudiowpatch as pa
        self._pa_mod = pa
        self._pa = pa.PyAudio()
        if index is None:
            dev = self._pa.get_default_wasapi_loopback()
        else:
            dev = self._pa.get_device_info_by_index(index)
            if not dev.get("isLoopbackDevice"):
                self._pa.terminate()
                raise ValueError(f"Device {index} ({dev['name']}) is not a loopback device. "
                                 f"Run `python -m armtrans devices` and pick one from the loopback list.")
        self.device = dev
        self.channels = int(dev["maxInputChannels"])
        self.in_sr = int(dev["defaultSampleRate"])
        self.block = int(self.in_sr * block_ms / 1000)
        self._consumer = _Consumer(self.in_sr, sink, "system-consumer", fill_gaps=True)
        self._stream = None

    @property
    def gap_fills(self):
        return self._consumer.gap_fills

    @property
    def overflows(self):
        return self._consumer.overflows

    def _callback(self, in_data, frame_count, time_info, status):
        t = time.perf_counter()
        if status & self._pa_mod.paInputOverflow:
            self._consumer.overflows += 1
        x = np.frombuffer(in_data, dtype=np.float32).reshape(-1, self.channels)
        self._consumer.put(x.mean(axis=1), t)
        return (None, self._pa_mod.paContinue)

    def start(self):
        self._stream = self._pa.open(format=self._pa_mod.paFloat32, channels=self.channels, rate=self.in_sr,
                                     input=True, input_device_index=int(self.device["index"]),
                                     frames_per_buffer=self.block, stream_callback=self._callback)
        self._consumer.start()
        self._stream.start_stream()

    def stop(self):
        if self._stream is not None:
            self._stream.stop_stream()
            self._stream.close()
        self._consumer.stop()
        self._pa.terminate()


class ChunkRecorder:
    """Test only (`run --record`): tees the 16 kHz chunk stream that goes to the VAD into a WAV file.

    All streams of one session share one start time `t0`, so `replay --mic-wav --system-wav` plays the
    files in the same alignment they had live. Each file starts with silence up to its first chunk.
    Small arrival-time jitter is ignored (audio is written back to back, so jitter cannot add drift);
    a jump of more than `gap_s` (lost audio, device stall) is filled with silence so later audio keeps
    its position. A write error is kept in `.error` (the caller stops the app); the live pipeline still
    gets every chunk.
    """

    def __init__(self, sink: ChunkSink, path, t0: float, gap_s: float = 0.25):
        import soundfile as sf
        self.sink = sink
        self.path = str(path)
        self.t0 = t0
        self.gap_s = gap_s
        self._f = sf.SoundFile(self.path, "w", samplerate=SAMPLE_RATE, channels=1, subtype="PCM_16")
        self._lock = threading.Lock()
        self.samples = 0
        self.padded_s = 0.0         # silence inserted (start offset + gaps)
        self.max_lead_s = 0.0       # how far written audio ran ahead of its arrival time (jitter/drift)
        self.max_lag_s = 0.0        # how far it fell behind without counting as a gap
        self.error: Optional[Exception] = None

    def __call__(self, chunk: np.ndarray, t: float):
        if self.error is None:
            try:
                self._write(chunk, t)
            except Exception as e:      # reported by the caller, never swallowed
                self.error = e
        self.sink(chunk, t)

    def _write(self, chunk: np.ndarray, t: float):
        with self._lock:
            if self._f is None:
                raise RuntimeError("recording already closed")
            target = int(round((t - len(chunk) / SAMPLE_RATE - self.t0) * SAMPLE_RATE))
            behind = target - self.samples
            if behind > 0 and (self.samples == 0 or behind > self.gap_s * SAMPLE_RATE):
                self._f.write(np.zeros(behind, np.float32))
                self.samples += behind
                self.padded_s += behind / SAMPLE_RATE
            elif behind > 0:
                self.max_lag_s = max(self.max_lag_s, behind / SAMPLE_RATE)
            else:
                self.max_lead_s = max(self.max_lead_s, -behind / SAMPLE_RATE)
            self._f.write(np.clip(chunk, -1.0, 1.0))
            self.samples += len(chunk)

    @property
    def duration_s(self) -> float:
        return self.samples / SAMPLE_RATE

    def close(self):
        with self._lock:
            if self._f is not None:
                self._f.close()
                self._f = None


def list_loopback_devices():
    """[(index, name, channels, samplerate, is_default)] or raises ImportError if PyAudioWPatch is missing."""
    import pyaudiowpatch as pa
    p = pa.PyAudio()
    try:
        default = p.get_default_wasapi_loopback()["index"]
        return [(d["index"], d["name"], d["maxInputChannels"], d["defaultSampleRate"], d["index"] == default)
                for d in p.get_loopback_device_info_generator()]
    finally:
        p.terminate()


class ReplaySource:
    """Test-only: feed a WAV file through the same path as a live device.

    realtime=True  -> paced at real speed, stamped with perf_counter (latency tests).
    realtime=False -> no sleeping, stamped on a virtual clock (fast, deterministic unit tests).
    """

    def __init__(self, path: str, sink: ChunkSink, realtime: bool = True,
                 block_ms: int = 20, start_t: float = 0.0):
        import soundfile as sf
        audio, sr = sf.read(path, dtype="float32", always_2d=True)
        audio = to_mono(audio)
        if sr != SAMPLE_RATE:
            audio = soxr.resample(audio, sr, SAMPLE_RATE).astype(np.float32)
        self.audio = audio
        self.realtime = realtime
        self.block = int(SAMPLE_RATE * block_ms / 1000)
        self._framer = Framer(sink)
        self._virtual_start = start_t
        self.started_at = None
        self.finished = threading.Event()

    @property
    def duration_s(self) -> float:
        return len(self.audio) / SAMPLE_RATE

    def run(self):
        """Blocking. Pushes all audio, then 1.5 s of silence so a trailing phrase can end."""
        audio = np.concatenate([self.audio, np.zeros(int(1.5 * SAMPLE_RATE), np.float32)])
        t0 = time.perf_counter() if self.realtime else self._virtual_start
        self.started_at = t0
        for pos in range(0, len(audio), self.block):
            block = audio[pos:pos + self.block]
            due = t0 + (pos + len(block)) / SAMPLE_RATE
            if self.realtime:
                delay = due - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                t = time.perf_counter()
            else:
                t = due
            self._framer.push(block, t)
        self.finished.set()
