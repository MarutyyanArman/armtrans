"""WER / CER with a fixed Armenian normalisation, and chrF (sacrebleu)."""
import re
import unicodedata

# Armenian question ՞ (U+055E), exclamation ՜ (U+055C), emphasis ՛ (U+055B) and apostrophe ՚ (U+055A)
# sit INSIDE a word (ինչպե՞ս), so they are deleted, not turned into spaces.
_INWORD_RE = re.compile("[՞՜՛՚]")
# Between-word marks: full stop ։ (U+0589), comma ՝ (U+055D), hyphen ֊ (U+058A), « », one-dot leader ․ (U+2024).
_PUNCT = "։՝֊«»․" + ".,?!:;\"'()[]{}-–—…"
_PUNCT_RE = re.compile("[" + re.escape(_PUNCT) + "]")


def normalize_hy(text: str) -> str:
    t = unicodedata.normalize("NFC", text)
    t = t.replace("և", "եւ")      # ligature և -> եւ (spelling varies between writers/models)
    t = t.lower()
    t = _INWORD_RE.sub("", t)
    t = _PUNCT_RE.sub(" ", t)
    return " ".join(t.split())


def edit_distance(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def wer(ref: str, hyp: str) -> tuple[int, int]:
    """Returns (errors, reference words) so results can be summed over a corpus."""
    r, h = normalize_hy(ref).split(), normalize_hy(hyp).split()
    return edit_distance(r, h), len(r)


def cer(ref: str, hyp: str) -> tuple[int, int]:
    r, h = normalize_hy(ref).replace(" ", ""), normalize_hy(hyp).replace(" ", "")
    return edit_distance(list(r), list(h)), len(r)


def chrf(refs: list[str], hyps: list[str]) -> float:
    from sacrebleu.metrics import CHRF
    return CHRF().corpus_score(hyps, [refs]).score
