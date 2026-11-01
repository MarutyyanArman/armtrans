"""V3 test: CTC beam search with a word n-gram language model (pyctcdecode), vs plain greedy decoding.

  python scripts\\bench_lm.py --clips <dir> [<dir> ...] --asr ctc --arpa models\\lm\\wiki_pc_3g.arpa.gz
         --alpha 0.3 0.5 0.8 --beta 0.5 1.0 2.0 [--hotwords names.txt] [--show]

- Same clips/refs as scripts/benchmark.py (refs.tsv), same WER/CER normalisation.
- Per-frame log-probabilities come from the CTC model through onnx-asr (the same model the app uses), then
  are decoded twice: greedy (= what the app prints) and beam search + LM for every (alpha, beta).
- KenLM's Python module has no Windows wheel and this laptop has no C++ compiler, so the ARPA file is read by
  a small pure-Python stand-in (ArpaLM) that provides the five calls pyctcdecode makes. Same maths as KenLM
  (back-off n-gram, log10). Loading a big ARPA takes RAM and seconds; this is a test, not the app.
Test only. Nothing here is used by the app.
"""
import argparse
import gzip
import os
import sys
import time
import types
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np  # noqa: E402

from armtrans.asr import FastConformerASR  # noqa: E402
from armtrans.config import AsrConfig  # noqa: E402
from armtrans.metrics import cer, wer  # noqa: E402
from benchmark import ASR_MODELS, load_clip, load_refs  # noqa: E402


class State:
    __slots__ = ("ctx",)

    def __init__(self):
        self.ctx = ()


class ArpaLM:
    """Back-off n-gram model read from an ARPA file (optionally .gz). log10 probabilities, like KenLM."""

    def __init__(self, path: str):
        self.path = str(path).encode("utf-8")
        self.p = {}          # n-gram tuple -> (log10 prob, log10 backoff)
        opener = gzip.open if str(path).endswith(".gz") else open
        order = 0
        with opener(path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                if line.startswith("\\") and line.endswith("-grams:"):
                    order = int(line[1:line.index("-")])
                    continue
                if order == 0 or line.startswith("\\"):
                    continue
                parts = line.split("\t")
                bo = float(parts[2]) if len(parts) > 2 else 0.0
                self.p[tuple(parts[1].split(" "))] = (float(parts[0]), bo)
        self.order = max(len(k) for k in self.p)
        self.unk = self.p.get(("<unk>",), (-100.0, 0.0))[0]
        self.vocab = {k[0] for k in self.p if len(k) == 1}

    def __contains__(self, word):
        return word in self.vocab

    def BeginSentenceWrite(self, state):
        state.ctx = ("<s>",)

    def NullContextWrite(self, state):
        state.ctx = ()

    def BaseScore(self, prev, word, out):
        ctx = prev.ctx[-(self.order - 1):] if self.order > 1 else ()
        backoff = 0.0
        score = None
        for k in range(len(ctx), -1, -1):
            hit = self.p.get(ctx[len(ctx) - k:] + (word,))
            if hit is not None:
                score = backoff + hit[0]
                break
            if k > 0:
                b = self.p.get(ctx[len(ctx) - k:])
                if b is not None:
                    backoff += b[1]
        if score is None:
            score = backoff + self.unk
        out.ctx = (ctx + (word,))[-(self.order - 1):] if self.order > 1 else ()
        return score


def inner_ctc(asr: FastConformerASR):
    inner = asr.model.asr
    if not hasattr(inner, "_encode"):
        sys.exit("onnx-asr internals changed: no _encode on the model")
    return inner


def ctc_logprobs(inner, audio: np.ndarray) -> np.ndarray:
    w = audio.astype(np.float32)[None, :]
    lens = np.array([w.shape[1]], np.int64)
    feats, flens = inner._preprocessor(w, lens)
    logp, olens = inner._encode(feats, flens)
    return logp[0, : int(olens[0])]


def labels_for(inner):
    """pyctcdecode labels by token id. onnx-asr loads the vocab with the word-start mark "▁" already turned into a
    space; put it back, otherwise pyctcdecode does not see word boundaries (BPE mode) and the LM scores nonsense."""
    vocab = inner._vocab
    labels = []
    for i in range(len(vocab)):
        t = vocab[i]
        if i == inner._blank_idx:
            labels.append("")
        elif t == "<unk>":
            labels.append("⁇")
        else:
            labels.append("▁" + t[1:] if t.startswith(" ") else t)
    return labels


def greedy(logp, labels):
    ids = logp.argmax(-1)
    out, prev = [], -1
    for i in ids:
        if i != prev and labels[i] != "":
            out.append(labels[i])
        prev = i
    return "".join(out).replace("▁", " ").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", nargs="+", required=True)
    ap.add_argument("--asr", default="ctc", choices=[k for k, v in ASR_MODELS.items() if v[1] == "nemo-conformer-ctc"])
    ap.add_argument("--arpa", required=True)
    ap.add_argument("--alpha", type=float, nargs="+", default=[0.5])
    ap.add_argument("--beta", type=float, nargs="+", default=[1.0])
    ap.add_argument("--beam", type=int, default=32)
    ap.add_argument("--hotwords", help="text file, one word or name per line")
    ap.add_argument("--hotword-weight", type=float, default=10.0)
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    import pyctcdecode.language_model as pl
    from pyctcdecode.alphabet import Alphabet
    from pyctcdecode.decoder import BeamSearchDecoderCTC
    pl.kenlm = types.SimpleNamespace(State=State)       # pure-Python stand-in for kenlm (see docstring)

    clips, refs = {}, {}
    for d in map(Path, a.clips):
        tag = d.parent.name + "/" if len(a.clips) > 1 else ""
        clips.update({tag + f.name: load_clip(f) for f in sorted(d.glob("*.wav"))})
        refs.update({tag + k: v["hy"] for k, v in load_refs(d).items()})
    clips = {k: v for k, v in clips.items() if k in refs}
    folder, kind = ASR_MODELS[a.asr]
    asr = FastConformerASR(AsrConfig(model_dir=folder, kind=kind))
    inner = inner_ctc(asr)
    labels = labels_for(inner)
    logps = {k: ctc_logprobs(inner, v) for k, v in clips.items()}

    t0 = time.perf_counter()
    lm_model = ArpaLM(a.arpa)
    print(f"LM {a.arpa}: order {lm_model.order}, {len(lm_model.p):,} n-grams, {len(lm_model.vocab):,} words, "
          f"loaded in {time.perf_counter() - t0:.0f}s")
    hot = None
    if a.hotwords:
        hot = [w.strip() for w in Path(a.hotwords).read_text(encoding="utf-8").splitlines() if w.strip()]

    def score(hyps):
        w = [wer(refs[k], h) for k, h in hyps.items()]
        c = [cer(refs[k], h) for k, h in hyps.items()]
        return (sum(e for e, _ in w), sum(n for _, n in w), sum(e for e, _ in c), sum(n for _, n in c))

    g = {k: greedy(v, labels) for k, v in logps.items()}
    e = score(g)
    print(f"{a.asr} greedy (as app):        WER {e[0]}/{e[1]} = {e[0] / e[1]:.1%}  CER {e[2] / e[3]:.1%}")
    alphabet = Alphabet.build_alphabet(labels)
    best = None
    for alpha in a.alpha:
        for beta in a.beta:
            lm = pl.LanguageModel(lm_model, sorted(lm_model.vocab), alpha=alpha, beta=beta)
            dec = BeamSearchDecoderCTC(alphabet, lm)
            times = []
            hyps = {}
            for k, v in logps.items():
                t2 = time.perf_counter()
                hyps[k] = dec.decode(v, beam_width=a.beam, hotwords=hot, hotword_weight=a.hotword_weight).strip()
                times.append(time.perf_counter() - t2)
            e = score(hyps)
            print(f"{a.asr} + LM alpha={alpha:<4} beta={beta:<4}: WER {e[0]}/{e[1]} = {e[0] / e[1]:.1%}  "
                  f"CER {e[2] / e[3]:.1%}  decode median {np.median(times):.2f}s max {max(times):.2f}s")
            if best is None or e[0] < best[0][0]:
                best = (e, alpha, beta, hyps)
    if a.show and best:
        print(f"\nbest alpha={best[1]} beta={best[2]}: per clip (REF / greedy / LM)")
        for k in logps:
            if g[k] != best[3][k]:
                print(f"  {k}\n    ref:    {refs[k]}\n    greedy: {g[k]}\n    LM:     {best[3][k]}")


if __name__ == "__main__":
    main()
