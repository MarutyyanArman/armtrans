"""One-time setup (needs internet). After this the app runs fully offline from models/.

  python scripts/download_models.py --vad --asr --speakers
  python scripts/download_models.py --mt             # needs requirements-setup.txt (conversion only)

--vad       extracts the official Silero VAD ONNX file (MIT) from the silero-vad package on PyPI,
            without installing it (that package would pull in PyTorch).
--asr       downloads the ONNX export of NVIDIA FastConformer-Hybrid Large (hy), RNNT branch (CC-BY-4.0), the app default.
--asr-ctc   downloads the CTC-branch export of the same model (only needed for `--asr ctc` comparisons).
--speakers  downloads WeSpeaker ResNet34-LM ONNX (CC-BY-4.0), 26.5 MB, for system-audio speaker labels.
--mt        downloads facebook/nllb-200-distilled-1.3B (CC-BY-NC-4.0, ~5.5 GB) and converts it to
            CTranslate2 int8 (~1.3 GB). Needs `transformers` + CPU PyTorch at setup time only:
            pip install torch --index-url https://download.pytorch.org/whl/cpu
            pip install -r requirements-setup.txt
            Then checks that armtrans/translate.py tokenises and decodes like the official tokenizer.

Downloads use plain HTTP (HF_HUB_DISABLE_XET=1): on 2026-10-31 the default "Xet" transfer stalled on the laptop.
"""
import argparse
import glob
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from armtrans.config import MODELS_DIR, VAD_MODEL, AsrConfig, MtConfig, SpeakerConfig  # noqa: E402

SILERO_PKG = "silero-vad==6.2.3"          # version whose ONNX signature armtrans/vad.py was verified against
SILERO_MEMBER = "silero_vad/data/silero_vad.onnx"
ASR_REPO = "Vana-Labs/stt-fastconformer-armenian"          # RNNT branch, app default
ASR_REV = "ec92cef52199c3874681321b13853e8fe602d23f"        # revision tested on the laptop 2026-11-01
ASR_FILES = ["config.json", "vocab.txt", "encoder-model.onnx", "decoder_joint-model.onnx", "README.md"]
ASR_OUT = MODELS_DIR / AsrConfig().model_dir
ASR_CTC_REPO = "OpenVoiceOS/stt_hy_fastconformer_hybrid_large_pc_onnx"
ASR_CTC_REV = "f7db1fad9af6bd8a7a9e08fca02068c403b35468"
ASR_CTC_OUT = MODELS_DIR / "fastconformer-hy-ctc"
SPK_REPO = "Wespeaker/wespeaker-voxceleb-resnet34-LM"
SPK_FILE = "voxceleb_resnet34_LM.onnx"
SPK_OUT = MODELS_DIR / SpeakerConfig().model_dir
MT_REPO = "facebook/nllb-200-distilled-1.3B"
MT_OUT = MODELS_DIR / MtConfig().model_dir
MT_FILES = ["config.json", "generation_config.json", "pytorch_model.bin", "sentencepiece.bpe.model",
            "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"]
CHECK_SENTENCES = ["Բարև, ինչպե՞ս ես։", "Վաղը ժամը տասին կհանդիպենք գրասենյակում։", "Այո", "Ես 25 տարեկան եմ։",
                   "Էդ խանութի աշխատողի մտածելակերպով շատ վատ հաճախորդ է։"]


def get_vad():
    with tempfile.TemporaryDirectory() as tmp:
        print(f"Downloading {SILERO_PKG} wheel (no dependencies) to extract the ONNX file")
        subprocess.run([sys.executable, "-m", "pip", "download", "--no-deps", "-q", SILERO_PKG, "-d", tmp],
                       check=True)
        whl = glob.glob(str(Path(tmp, "*.whl")))[0]
        with zipfile.ZipFile(whl) as z:
            VAD_MODEL.write_bytes(z.read(SILERO_MEMBER))
    print(f"  -> {VAD_MODEL} ({VAD_MODEL.stat().st_size / 1e6:.1f} MB)")


def get_asr():
    from huggingface_hub import snapshot_download
    print(f"Downloading {ASR_REPO} @ {ASR_REV} -> {ASR_OUT}")
    snapshot_download(ASR_REPO, revision=ASR_REV, local_dir=str(ASR_OUT), allow_patterns=ASR_FILES)
    size = sum(f.stat().st_size for f in ASR_OUT.rglob("*") if f.is_file()) / 1e6
    print(f"  -> {size:.0f} MB")


def get_asr_ctc():
    from huggingface_hub import snapshot_download
    print(f"Downloading {ASR_CTC_REPO} @ {ASR_CTC_REV} -> {ASR_CTC_OUT}")
    snapshot_download(ASR_CTC_REPO, revision=ASR_CTC_REV, local_dir=str(ASR_CTC_OUT))
    size = sum(f.stat().st_size for f in ASR_CTC_OUT.rglob("*") if f.is_file()) / 1e6
    print(f"  -> {size:.0f} MB")


def get_speakers():
    import onnxruntime as ort
    from huggingface_hub import HfApi, hf_hub_download
    rev = HfApi().model_info(SPK_REPO).sha
    print(f"Downloading {SPK_REPO}/{SPK_FILE} @ {rev} -> {SPK_OUT}")
    f = hf_hub_download(SPK_REPO, SPK_FILE, revision=rev, local_dir=str(SPK_OUT))
    s = ort.InferenceSession(f, providers=["CPUExecutionProvider"])
    ins = [(i.name, i.shape) for i in s.get_inputs()]
    outs = [(o.name, o.shape) for o in s.get_outputs()]
    print(f"  inputs {ins} outputs {outs}")
    if ins[0][0] != "feats" or outs[0][0] != "embs":
        sys.exit("Unexpected speaker model interface; armtrans/speakers.py expects feats -> embs.")


def convert_mt():
    from huggingface_hub import snapshot_download
    print(f"Downloading {MT_REPO} (~5.5 GB) ...")
    snap = snapshot_download(MT_REPO, allow_patterns=MT_FILES)
    print(f"Converting -> {MT_OUT} (int8)")
    subprocess.run(["ct2-transformers-converter", "--model", snap, "--output_dir", str(MT_OUT),
                    "--quantization", "int8", "--copy_files", "sentencepiece.bpe.model", "tokenizer.json",
                    "tokenizer_config.json", "special_tokens_map.json", "--force"], check=True)
    check_tokenizer_equivalence()


def check_tokenizer_equivalence():
    """Runtime tokenisation (armtrans/translate.py, SentencePiece only) must match transformers' NllbTokenizer."""
    from transformers import AutoTokenizer
    from armtrans.translate import NllbMT, output_text, source_tokens
    hf = AutoTokenizer.from_pretrained(str(MT_OUT), src_lang="hye_Armn")
    mt = NllbMT(MtConfig())
    ok = True
    for s in CHECK_SENTENCES:
        ours = source_tokens(mt.sp, s)
        theirs = hf.convert_ids_to_tokens(hf.encode(s))
        res = mt.translator.translate_batch([ours], target_prefix=[["eng_Latn"]], beam_size=4)
        toks = res[0].hypotheses[0]
        our_text = output_text(mt.sp, toks)
        hf_text = hf.decode(hf.convert_tokens_to_ids(toks[1:]), skip_special_tokens=True)
        same = ours == theirs and our_text == hf_text
        ok &= same
        print(f"  {'OK  ' if same else 'DIFF'} {s!r} -> {our_text!r}"
              + ("" if same else f"\n    ours={ours} / {our_text!r}\n    hf  ={theirs} / {hf_text!r}"))
    if not ok:
        sys.exit("Tokenizer mismatch: armtrans/translate.py must be changed before using this model.")
    print("Tokenizer check passed.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vad", action="store_true", help="extract Silero VAD ONNX")
    ap.add_argument("--asr", action="store_true", help="download NVIDIA FastConformer hy, RNNT branch (ONNX, app default)")
    ap.add_argument("--asr-ctc", action="store_true", help="download the CTC branch (only for --asr ctc comparisons)")
    ap.add_argument("--speakers", action="store_true", help="download WeSpeaker ResNet34 (ONNX)")
    ap.add_argument("--mt", action="store_true", help="download + convert NLLB-200 1.3B")
    ap.add_argument("--check-mt", action="store_true", help="only re-run the NLLB tokenizer check")
    a = ap.parse_args()
    if not (a.vad or a.asr or a.asr_ctc or a.speakers or a.mt or a.check_mt):
        ap.error("nothing to do: pass --vad, --asr, --speakers and/or --mt")
    MODELS_DIR.mkdir(exist_ok=True)
    steps = [(a.vad, get_vad), (a.asr, get_asr), (a.asr_ctc, get_asr_ctc), (a.speakers, get_speakers), (a.mt, convert_mt),
             (a.check_mt and not a.mt, check_tokenizer_equivalence)]
    for wanted, fn in steps:
        if wanted:
            try:
                fn()
            except SystemExit:
                raise
            except Exception as e:
                sys.exit(f"{fn.__name__} failed ({type(e).__name__}: {e}). Check internet access to huggingface.co.")


if __name__ == "__main__":
    main()
