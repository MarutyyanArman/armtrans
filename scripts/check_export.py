"""Compare an ONNX export (runtime venv, onnx-asr) with NeMo's own output saved by scripts/export_nemo.py.

  python scripts\\check_export.py --name arm-nopc

Prints NeMo vs ONNX text per clip for the RNNT and CTC folders and how many match exactly.
"""
import argparse
import json
import os
import sys
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import soundfile as sf  # noqa: E402

from armtrans.asr import FastConformerASR  # noqa: E402
from armtrans.config import AsrConfig  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    a = ap.parse_args()
    ref = json.loads((ROOT / "models" / f"fastconformer-{a.name}-nemo-ref.json").read_text(encoding="utf-8"))
    print(f"{ref['repo']} @ {ref['revision']}")
    for dec, kind in (("rnnt", "nemo-conformer-rnnt"), ("ctc", "nemo-conformer-ctc")):
        asr = FastConformerASR(AsrConfig(model_dir=f"fastconformer-{a.name}-{dec}", kind=kind))
        same = 0
        for clip, nemo_text in zip(ref["clips"], ref[dec]):
            audio, sr = sf.read(clip, dtype="float32")
            assert sr == 16000, clip
            onnx_text = asr.transcribe(audio)
            ok = onnx_text.strip() == nemo_text.strip()
            same += ok
            print(f"  {dec} {'SAME' if ok else 'DIFF'} {Path(clip).name}\n      nemo: {nemo_text}\n      onnx: {onnx_text}")
        print(f"{dec}: {same}/{len(ref['clips'])} identical to NeMo")


if __name__ == "__main__":
    main()
