"""Armenian -> English with NLLB-200 distilled 1.3B, converted to CTranslate2 int8. Loads only from models/.

Tokenisation (no `transformers` at runtime): source = ["hye_Armn"] + SentencePiece pieces + ["</s>"],
target_prefix = ["eng_Latn"]; the output's first token (the language code) and "</s>" are dropped.
On 2026-10-31 these source tokens were identical to transformers' NllbTokenizer on 18/18 test strings
(laptop); scripts/download_models.py --mt re-checks source AND decoded output after conversion.
Model choice and measurements: DECISIONS.md, 2026-10-31.

Short-input guard (2026-11-01, DECISIONS.md "NLLB invents content on 1-2 word inputs"; MtConfig.short_guard):
- a line that is exactly one of SHORT_REPLIES (after removing punctuation; not a question) gets that fixed English;
- otherwise a line of <= short_max_words words whose best hypothesis scores below short_min_score
  (average log-probability) prints UNCLEAR instead of a fluent invented sentence. The line is still printed.
"""
import re
from .config import MODELS_DIR, MtConfig

SRC_LANG = "hye_Armn"
TGT_LANG = "eng_Latn"
UNCLEAR = "(unclear)"

# Common one-line replies where NLLB adds content (measured 2026-11-01: Ոչ -> "No, I did not.",
# Իհարկե -> "Of course you do.", Օքեյ -> "Okay, here we go.", Բարեւ -> "How are you?"). Keys are normalised.
SHORT_REPLIES = {
    "այո": "Yes.", "ոչ": "No.", "չէ": "No.", "հա": "Yeah.", "լավ": "Okay.", "օքեյ": "Okay.",
    "իհարկե": "Of course.", "բարեւ": "Hello.", "բարեւ ձեզ": "Hello.", "ողջույն": "Hi.",
    "ցտեսություն": "Goodbye.", "հասկացա": "Got it.", "շնորհակալություն": "Thank you.",
    "շնորհակալ եմ": "Thank you.", "ճիշտ է": "That's right.", "մի րոպե": "Just a minute.",
    "վա": "Wow.", "հաջողություն": "Good luck.",
}
_PUNCT = re.compile(r"[\u0589\u055D\u055E\u055C\u055B\u055A\u2026.,!?:;\"'()\-]+")


def normalise(text: str) -> str:
    """Lower case, Armenian/Latin punctuation removed, "և" spelled "եւ" (as the ASR writes it), single spaces."""
    t = _PUNCT.sub(" ", text.lower().replace("և", "եւ"))
    return " ".join(t.split())


class ModelMissing(Exception):
    pass


def source_tokens(sp, text: str) -> list[str]:
    return [SRC_LANG] + sp.encode(text, out_type=str) + ["</s>"]


def output_text(sp, tokens: list[str]) -> str:
    pieces = [t for t in tokens if t not in (TGT_LANG, "</s>")]
    return sp.decode(pieces)


class NllbMT:
    def __init__(self, cfg: MtConfig):
        import ctranslate2
        import sentencepiece as spm
        path = MODELS_DIR / cfg.model_dir
        for f in ("model.bin", "sentencepiece.bpe.model"):
            if not (path / f).exists():
                raise ModelMissing(f"Translation model file {path / f} missing. Run: python scripts/download_models.py --mt")
        self.cfg = cfg
        self.translator = ctranslate2.Translator(str(path), device="cpu", compute_type=cfg.compute_type,
                                                 intra_threads=cfg.intra_threads)
        self.sp = spm.SentencePieceProcessor(model_file=str(path / "sentencepiece.bpe.model"))

    def translate(self, text: str) -> str:
        if not text.strip():
            return ""
        guard = self.cfg.short_guard
        if guard and not any(q in text for q in ("\u055E", "?")):      # questions (՞ ?) mean something else
            fixed = SHORT_REPLIES.get(normalise(text))
            if fixed:
                return fixed
        res = self.translator.translate_batch([source_tokens(self.sp, text)], target_prefix=[[TGT_LANG]],
                                              beam_size=self.cfg.beam_size, return_scores=True)
        if (guard and len(normalise(text).split()) <= self.cfg.short_max_words
                and res[0].scores[0] < self.cfg.short_min_score):
            return UNCLEAR
        return output_text(self.sp, res[0].hypotheses[0])

    def warm_up(self):
        self.translate("Բարեւ ձեզ, ինչպե՞ս եք այսօր։")      # not a SHORT_REPLIES entry: really runs the model
