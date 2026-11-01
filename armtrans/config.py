"""All tunable values in one place. Defaults are starting points from PLAN.md, not measured optima."""
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_ROOT / "models"
LOGS_DIR = PROJECT_ROOT / "logs"
VAD_MODEL = MODELS_DIR / "silero_vad.onnx"    # official Silero VAD (MIT), extracted by scripts/download_models.py


@dataclass
class VadConfig:
    threshold: float = 0.5           # speech starts when probability >= this
    neg_threshold: float = 0.35      # speech continues while probability >= this
    min_silence_ms: int = 600        # pause that ends a phrase (PLAN.md section 3)
    max_phrase_s: float = 5.0        # forced cut; 5 s (was 6) to keep margin with NLLB-1.3B (DECISIONS.md)
    max_phrase_solo_s: float = 8.0   # 2026-11-01: a phrase may grow past max_phrase_s up to this ONLY while every
                                     # other stream is silent (5 s rule safe: DECISIONS.md). = max_phrase_s disables.
    min_speech_ms: int = 250         # shorter blips are discarded (and logged, never silently)
    preroll_ms: int = 200            # audio kept before speech start
    tail_ms: int = 100               # audio kept after last speech chunk
    # Opt-in smarter cuts (2026-11-01, default off until the owner decides; DECISIONS.md).
    adaptive_pause: bool = False     # pause needed to end a phrase shrinks as the phrase grows
    pause_steps: tuple = ((3.0, 250), (4.0, 120))   # (phrase length s, pause ms); untuned starting values
    smart_cut: bool = False          # at max_phrase_s cut at the quietest chunk of the last window, carry the rest
    smart_cut_window_s: float = 1.5
    smart_cut_by: str = "vad"         # "vad": lowest Silero chunk; "energy": quietest 10 ms; "asr": before last word


@dataclass
class AsrConfig:
    # RNNT branch of NVIDIA FastConformer hy since 2026-11-01 (fewer errors on real calls, DECISIONS.md).
    # The CTC branch stays selectable with `--asr ctc` (folder fastconformer-hy-ctc, kind nemo-conformer-ctc).
    model_dir: str = "fastconformer-hy-rnnt"  # under models/ (Vana-Labs ONNX export, fp32)
    kind: str = "nemo-conformer-rnnt"         # onnx-asr model type
    cpu_threads: int = 4
    # 2026-11-01: a phrase that continues a forced cut is recognised together with the last
    # `context_s` of the previous phrase; only words reaching into the new audio are printed.
    context_s: float = 1.5           # 0 = off (old behaviour)
    context_tol_s: float = 0.08      # a word counts as new if one of its tokens starts >= boundary + this
    context_lead_s: float = 0.0      # ... or if its first token starts >= boundary - this (set by measurement)


@dataclass
class MtConfig:
    model_dir: str = "nllb-1.3b-ct2"          # under models/ (NLLB-200 distilled 1.3B, CTranslate2 int8)
    compute_type: str = "int8"
    intra_threads: int = 4
    beam_size: int = 4
    # 2026-11-01: short-input guard (translate.py). Threshold chosen on the same 58 lines it was measured on.
    short_guard: bool = True
    short_max_words: int = 2
    short_min_score: float = -0.9    # average log-prob of NLLB's best hypothesis


@dataclass
class SpeakerConfig:
    """System-audio speaker labels (PLAN.md section 6 rule). Thresholds are untuned starting values."""
    model_dir: str = "wespeaker-resnet34"     # under models/ (WeSpeaker ResNet34-LM, ONNX, 256-dim)
    min_new_s: float = 1.5           # only phrases at least this long may create or update a speaker
    match: float = 0.5               # cosine similarity: long phrase joins the best speaker at or above this
    cpu_threads: int = 2


@dataclass
class AppConfig:
    vad: VadConfig = field(default_factory=VadConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    mt: MtConfig = field(default_factory=MtConfig)
    speakers: SpeakerConfig = field(default_factory=SpeakerConfig)
    latency_limit_s: float = 5.0
