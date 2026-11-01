"""SETUP ONLY (needs NeMo + PyTorch, in a separate venv): export a NeMo hybrid FastConformer to onnx-asr folders.

  .venv-export\\Scripts\\python.exe scripts\\export_nemo.py --repo mheryerznka/stt_arm_fastconformer_hybrid_large_no_pc --name arm-nopc

Writes, under models/:
  fastconformer-<name>-rnnt/  encoder-model.onnx, decoder_joint-model.onnx, vocab.txt, config.json  (nemo-conformer-rnnt)
  fastconformer-<name>-ctc/   model.onnx, vocab.txt, config.json                                 (nemo-conformer-ctc)
  fastconformer-<name>-nemo-ref.json  NeMo's own RNNT and CTC text for the check clips, so the ONNX export
                                       can be compared with NeMo in the runtime venv (scripts/check_export.py).
Same steps as the published Vana-Labs export of the NVIDIA model (set_export_config decoder_type, export).
The app runtime never imports NeMo or PyTorch.
"""
import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
ROOT = Path(__file__).resolve().parent.parent


def write_vocab(model, folder: Path):
    vocab = [*model.tokenizer.vocab, "<blk>"]
    with (folder / "vocab.txt").open("w", encoding="utf-8") as f:
        for i, t in enumerate(vocab):
            f.write(f"{t} {i}\n")
    return len(vocab)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--name", required=True, help="short folder name, e.g. arm-nopc")
    ap.add_argument("--revision", help="pin a commit (default: current main, printed)")
    ap.add_argument("--check-clips", nargs="*", default=[], help="WAV files for the NeMo reference transcripts")
    a = ap.parse_args()

    from huggingface_hub import HfApi, hf_hub_download
    import nemo.collections.asr as nemo_asr

    info = HfApi().model_info(a.repo, revision=a.revision)
    nemo_file = next(s.rfilename for s in info.siblings if s.rfilename.endswith(".nemo"))
    path = hf_hub_download(a.repo, nemo_file, revision=info.sha)
    print(f"{a.repo} @ {info.sha}: {nemo_file}")

    m = nemo_asr.models.ASRModel.restore_from(path, map_location="cpu")
    m.eval()
    pre = m.cfg.preprocessor
    sub = int(m.cfg.encoder.get("subsampling_factor", 8))
    print(type(m).__name__, "| features", pre.get("features"), "| window", pre.get("window_size"),
          pre.get("window_stride"), "| normalize", pre.get("normalize"), "| subsampling", sub)
    if int(pre.get("features", 80)) != 80:
        raise SystemExit("Expected 80 mel features (onnx-asr nemo80 preprocessor); stopping.")

    models = ROOT / "models"
    out_r = models / f"fastconformer-{a.name}-rnnt"
    out_c = models / f"fastconformer-{a.name}-ctc"
    for d in (out_r, out_c):
        d.mkdir(parents=True, exist_ok=False)     # never overwrite an existing model folder

    m.set_export_config({"decoder_type": "rnnt"})
    m.export(str(out_r / "model.onnx"))           # NeMo writes encoder-model.onnx + decoder_joint-model.onnx
    n = write_vocab(m, out_r)
    json.dump({"model_type": "nemo-conformer-rnnt", "features_size": 80, "subsampling_factor": sub,
               "max_tokens_per_step": 10}, open(out_r / "config.json", "w"), indent=4)

    m.set_export_config({"decoder_type": "ctc"})
    m.export(str(out_c / "model.onnx"))
    write_vocab(m, out_c)
    json.dump({"model_type": "nemo-conformer-ctc", "features_size": 80, "subsampling_factor": sub},
              open(out_c / "config.json", "w"), indent=4)
    print("vocab", n, "| files:", sorted(p.name for p in out_r.iterdir()), sorted(p.name for p in out_c.iterdir()))

    ref = {"repo": a.repo, "revision": info.sha, "clips": a.check_clips, "rnnt": [], "ctc": []}
    if a.check_clips:
        for dec in ("rnnt", "ctc"):
            m.change_decoding_strategy(decoder_type=dec)
            hyp = m.transcribe(a.check_clips, batch_size=1, verbose=False)
            hyp = hyp[0] if isinstance(hyp, tuple) else hyp
            ref[dec] = [getattr(h, "text", h) for h in hyp]
    (models / f"fastconformer-{a.name}-nemo-ref.json").write_text(json.dumps(ref, ensure_ascii=False, indent=1),
                                                                    encoding="utf-8")
    print("done")


if __name__ == "__main__":
    main()
