"""Stable speaker labels: "Me" for the mic, "Speaker N" for system audio (the call).

Rule (PLAN.md section 6; reworked 2026-10-31, DECISIONS.md):
- every system phrase is compared with the known speakers (cosine similarity of voice fingerprints);
- only phrases >= min_new_s may create a new speaker or update a known speaker's profile;
- a long phrase joins the best match if similarity >= match, otherwise it becomes a new speaker;
- a short phrase gets the closest known speaker (never "Speaker ?");
- mic phrases: see assign_mic ("Me", learned automatically; call bleed gets the call speaker's label);
- labels are never changed or merged after printing.

Voice fingerprints: WeSpeaker ResNet34-LM ONNX (input feats[B,T,80] -> embs[B,256], verified on the laptop
2026-10-31), run through onnx-asr 0.12.0's speaker-embedding support (Manager().create_se(local_dir=...)).
"""
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from .config import MODELS_DIR, SpeakerConfig

UNKNOWN = "Speaker ?"


class ModelMissing(Exception):
    pass


def load_embedder(cfg: SpeakerConfig) -> Callable[[np.ndarray], np.ndarray]:
    import onnxruntime as ort
    from onnx_asr.loader import Manager
    path = MODELS_DIR / cfg.model_dir
    if not list(path.glob("*.onnx")):
        raise ModelMissing(f"Speaker model missing in {path}. Run: python scripts/download_models.py --speakers")
    so = ort.SessionOptions()
    so.intra_op_num_threads = cfg.cpu_threads
    so.inter_op_num_threads = 1
    se = Manager(sess_options=so, providers=["CPUExecutionProvider"]).create_se(local_dir=str(path), offline=True)
    return lambda audio: se.embedding(audio.astype(np.float32), sample_rate=16000)


@dataclass
class Speaker:
    label: str
    centroid: np.ndarray        # unit vector
    n: int = 1                  # number of long phrases averaged into it


@dataclass
class Decision:
    label: str
    similarity: Optional[float]     # best similarity to an existing speaker (None if there were none)
    reason: str                     # new | match | short_match | me | me_unenrolled | bleed


class SpeakerMatcher:
    def __init__(self, embed: Callable[[np.ndarray], np.ndarray], cfg: SpeakerConfig):
        self.embed = embed
        self.cfg = cfg
        self.speakers: list[Speaker] = []
        self.me: Optional[np.ndarray] = None
        self.me_n = 0

    @staticmethod
    def _unit(v: np.ndarray) -> np.ndarray:
        v = np.asarray(v, dtype=np.float32).reshape(-1)
        return v / (np.linalg.norm(v) + 1e-9)

    def _best(self, e):
        if not self.speakers:
            return None, None
        sims = [float(s.centroid @ e) for s in self.speakers]
        i = int(np.argmax(sims))
        return self.speakers[i], sims[i]

    def assign_mic(self, audio: np.ndarray, overlaps_call: bool) -> Decision:
        """Label a mic phrase. Normally "Me". While call speech plays at the same time (laptop speakers),
        the mic also picks up the call; if the voice is not mine and is closer to a call speaker, it gets that
        speaker's label instead. "Me" is learned automatically from mic phrases heard while the call is silent."""
        dur = len(audio) / 16000.0
        if not overlaps_call and dur < self.cfg.min_new_s:
            return Decision("Me", None, "me")
        e = self._unit(self.embed(audio))
        if not overlaps_call:
            self.me = e if self.me is None else self._unit(self.me * self.me_n + e)
            self.me_n += 1
            return Decision("Me", None, "me")
        if self.me is None:
            return Decision("Me", None, "me_unenrolled")
        sim_me = float(self.me @ e)
        best, best_sim = self._best(e)
        if best is not None and sim_me < self.cfg.match and best_sim > sim_me:
            return Decision(best.label, best_sim, "bleed")
        return Decision("Me", sim_me, "me")

    def assign(self, audio: np.ndarray) -> Decision:
        dur = len(audio) / 16000.0
        e = self._unit(self.embed(audio))
        best, best_sim = None, None
        if self.speakers:
            sims = [float(s.centroid @ e) for s in self.speakers]
            i = int(np.argmax(sims))
            best, best_sim = self.speakers[i], sims[i]

        if dur >= self.cfg.min_new_s:
            if best is not None and best_sim >= self.cfg.match:
                best.centroid = self._unit(best.centroid * best.n + e)
                best.n += 1
                return Decision(best.label, best_sim, "match")
            spk = Speaker(f"Speaker {len(self.speakers) + 1}", e)
            self.speakers.append(spk)
            return Decision(spk.label, best_sim, "new")

        # Short phrase: always the closest known speaker (never "Speaker ?"); it never creates or updates one.
        if best is not None:
            return Decision(best.label, best_sim, "short_match")
        spk = Speaker("Speaker 1", e)       # nobody known yet: the first voice heard is Speaker 1
        self.speakers.append(spk)
        return Decision(spk.label, None, "new")
