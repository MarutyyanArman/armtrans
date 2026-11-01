"""Per-phrase latency log (JSON lines) and end-of-session summary.

The log holds timings only, never transcript text (transcript saving is out of scope).
"""
import json
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from .phrase import Phrase


def percentile(values, q):
    return float(np.percentile(np.asarray(values, dtype=float), q)) if values else float("nan")


class TimingLog:
    def __init__(self, path: Optional[Path], limit_s: float):
        self.limit_s = limit_s
        self.path = path
        self._lock = threading.Lock()
        self._f = open(path, "w", encoding="utf-8") if path else None
        self.records: list[dict] = []
        self.discards: list[dict] = []

    def _write(self, obj: dict):
        if self._f:
            self._f.write(json.dumps(obj) + "\n")
            self._f.flush()

    def discard(self, source: str, start_t: float, speech_ms: float):
        rec = {"event": "discard_short", "source": source, "start_t": round(start_t, 4),
               "speech_ms": round(speech_ms)}
        with self._lock:
            self.discards.append(rec)
            self._write(rec)

    def phrase(self, p: Phrase) -> dict:
        t = p.times
        rec = {
            "event": "phrase", "source": p.source, "seq": p.seq, "label": p.label,
            "cut_reason": p.cut_reason, "audio_s": round(p.duration_s, 3),
            "vad_wait_s": round(p.emit_t - p.end_t, 3),
            "queue_wait_s": round(t["asr_start"] - p.emit_t, 3),
            "asr_s": round(t["asr_end"] - t["asr_start"], 3),
            "spk_s": round(t["spk_end"] - t["asr_end"], 3) if "spk_end" in t else None,
            "mt_s": round(t["mt_end"] - t["mt_start"], 3) if "mt_end" in t else None,
            "latency_s": round(t["done"] - p.end_t, 3),
            "empty_asr": not p.hy_text,
        }
        if p.cut_prob is not None:
            rec["cut_prob"] = round(p.cut_prob, 3)
        if p.cut_asr_s is not None:
            rec["cut_asr_s"] = round(p.cut_asr_s, 3)
        if p.speaker is not None:   # label decision details (no transcript text)
            rec["spk_reason"] = p.speaker.reason
            rec["spk_sim"] = None if p.speaker.similarity is None else round(p.speaker.similarity, 3)
        rec["late"] = rec["latency_s"] > self.limit_s
        with self._lock:
            self.records.append(rec)
            self._write(rec)
        return rec

    def summary(self) -> str:
        with self._lock:
            recs = [r for r in self.records if not r["empty_asr"]]
            lines = [f"Phrases printed: {len(recs)} | empty ASR (not printed): "
                     f"{sum(r['empty_asr'] for r in self.records)} | short blips discarded: {len(self.discards)}"]
            for src in sorted({r["source"] for r in recs}):
                lat = [r["latency_s"] for r in recs if r["source"] == src]
                late = sum(r["late"] for r in recs if r["source"] == src)
                lines.append(
                    f"  {src:6s} n={len(lat):3d}  latency median={percentile(lat, 50):.2f}s "
                    f"p95={percentile(lat, 95):.2f}s max={max(lat):.2f}s  "
                    f"over {self.limit_s:.0f}s: {late} ({100 * late / len(lat):.0f}%)")
            if recs:
                proc = [r["asr_s"] + (r["spk_s"] or 0) + (r["mt_s"] or 0) for r in recs]
                lines.append(f"  processing per phrase (ASR + speaker + translation): median={percentile(proc, 50):.2f}s "
                             f"max={max(proc):.2f}s")
            labels = {}
            for r in recs:
                if r["source"] == "system":
                    labels[r["label"]] = labels.get(r["label"], 0) + 1
            if labels:
                lines.append("  system-audio labels: " + ", ".join(f"{k}: {v}" for k, v in sorted(labels.items())))
            return "\n".join(lines)

    def close(self):
        if self._f:
            self._f.close()
