"""The record passed between modules."""
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class Phrase:
    source: str                 # "mic" or "system"
    audio: np.ndarray           # 16 kHz mono float32
    start_t: float              # perf_counter time of first speech sample
    end_t: float                # perf_counter time the last speech chunk reached the laptop
    emit_t: float               # perf_counter time the VAD decided the phrase ended
    cut_reason: str             # "pause", "max_len", "smart_cut" or "word_cut"
    seq: int = 0                # per-stream counter
    cut_prob: Optional[float] = None   # VAD probability at a forced cut point (None for pauses)
    cut_asr_s: Optional[float] = None  # time of the extra ASR call that found the cut (smart_cut_by='asr')
    context: Optional[np.ndarray] = None   # end of the previous phrase when this one continues a forced cut
    label: Optional[str] = None
    hy_text: Optional[str] = None
    en_text: Optional[str] = None
    speaker: Optional[object] = None   # speakers.Decision for system phrases
    times: dict = field(default_factory=dict)   # stage name -> perf_counter time

    @property
    def duration_s(self) -> float:
        return len(self.audio) / 16000.0
