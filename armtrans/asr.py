"""Armenian speech recognition: NVIDIA FastConformer-Hybrid Large (hy) (nvidia/stt_hy_fastconformer_hybrid_large_pc,
CC-BY-4.0), ONNX, run with onnx-asr on ONNX Runtime (no NeMo, no PyTorch). Loads only from models/.

Default since 2026-11-01: RNNT (transducer) branch, export Vana-Labs/stt-fastconformer-armenian @ ec92cef5
(folder fastconformer-hy-rnnt). Selectable: CTC branch, export OpenVoiceOS/stt_hy_fastconformer_hybrid_large_pc_onnx
(folder fastconformer-hy-ctc, `--asr ctc`). Same acoustic model; the decoders differ (DECISIONS.md 2026-11-01).

onnx-asr API verified on 2026-10-31 (onnx-asr 0.12.0): load_model("nemo-conformer-ctc", path=<dir>) reads
model.onnx + vocab.txt (+ config.json) from <dir> and stays offline when <dir> exists;
.recognize(np.float32 array, sample_rate=16000) -> str.

Other model types (benchmark/comparison, AsrConfig.kind):
- "nemo-conformer-rnnt": RNNT (transducer) branch of the same NVIDIA model, ONNX export
  Vana-Labs/stt-fastconformer-armenian (encoder-model.onnx + decoder_joint-model.onnx + vocab.txt, CC-BY-4.0).
"""
import threading

import numpy as np

from .config import MODELS_DIR, AsrConfig


class ModelMissing(Exception):
    pass


def model_dir(cfg: AsrConfig):
    return MODELS_DIR / cfg.model_dir


class FastConformerASR:
    def __init__(self, cfg: AsrConfig):
        import onnx_asr
        import onnxruntime as ort
        path = model_dir(cfg)
        need = {"nemo-conformer-ctc": ("model.onnx", "vocab.txt"),
                "nemo-conformer-rnnt": ("encoder-model.onnx", "decoder_joint-model.onnx", "vocab.txt")}
        if cfg.kind not in need:
            raise ValueError(f"Unknown ASR model type {cfg.kind!r}; known: {', '.join(need)}")
        for f in need[cfg.kind]:
            if not (path / f).exists():
                flag = "--asr-ctc" if cfg.kind == "nemo-conformer-ctc" else "--asr"
                raise ModelMissing(f"ASR model file {path / f} missing. Run: python scripts/download_models.py {flag}")
        self.cfg = cfg
        so = ort.SessionOptions()
        so.intra_op_num_threads = cfg.cpu_threads
        so.inter_op_num_threads = 1
        self.model = onnx_asr.load_model(cfg.kind, path=str(path), sess_options=so,
                                         providers=["CPUExecutionProvider"])
        self._ts_model = None
        # The worker thread (transcribe) and, with `--smart-cut asr`, the capture thread (word_starts)
        # share the model: one call at a time.
        self._lock = threading.Lock()

    def transcribe(self, audio: np.ndarray) -> str:
        with self._lock:
            return self.model.recognize(audio.astype(np.float32), sample_rate=16000).strip()

    def _timestamped(self, audio: np.ndarray):
        with self._lock:
            if self._ts_model is None:
                self._ts_model = self.model.with_timestamps()
            return self._ts_model.recognize(audio.astype(np.float32), sample_rate=16000)

    def transcribe_with_context(self, audio: np.ndarray, context: np.ndarray) -> str:
        """Recognise context + audio together; return only the words that reach into `audio`.

        A word (tokens from one starting with a space up to the next) is kept from the first word
        that has a token starting at or after boundary + context_tol_s, or whose first token starts
        at or after boundary - context_lead_s (short words right at the join). A word cut in half by
        the previous phrase is therefore printed whole here (the previous line may show its first half).
        Verified 2026-11-01 on 28 recorded phrases: ''.join(tokens) == recognize() text (RNNT).
        """
        r = self._timestamped(np.concatenate([context, audio]))
        b = len(context) / 16000
        words = []                                   # [[tokens], first token start, last token start]
        for tok, t in zip(r.tokens, r.timestamps):
            if tok.startswith(" ") or not words:
                words.append([[tok], float(t), float(t)])
            else:
                words[-1][0].append(tok)
                words[-1][2] = float(t)
        keep = next((i for i, w in enumerate(words)
                     if w[2] >= b + self.cfg.context_tol_s or w[1] >= b - self.cfg.context_lead_s), len(words))
        text = "".join(tok for w in words[keep:] for tok in w[0]).strip()
        return text[:1].upper() + text[1:]          # the model writes it mid-sentence (lower case)

    def word_starts(self, audio: np.ndarray) -> list:
        """Start times (s, within `audio`) of recognised words, for cutting phrases between words.

        onnx-asr 0.12.0 verified 2026-11-01: model.with_timestamps().recognize(...) returns .tokens and
        .timestamps (token start, 80 ms steps); a token beginning with a space starts a word.
        """
        r = self._timestamped(audio)
        return [float(t) for tok, t in zip(r.tokens, r.timestamps) if tok.startswith(" ")]

    def warm_up(self):
        rng = np.random.default_rng(0)
        self.transcribe((rng.standard_normal(16000) * 0.01).astype(np.float32))
