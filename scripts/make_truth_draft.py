"""Make a speaker-truth draft from a `run --record` folder, for scripts/score_speakers.py.

  python scripts/make_truth_draft.py recordings/rec-20261101-120000

Writes <folder>/truth_draft.json: one entry per system-audio phrase the app printed, with its times,
printed label and Armenian text. Fill "speaker" with who really spoke (any name or initial, same name for
the same person), delete entries that were not speech, then save it as truth.json and run:

  python scripts/score_speakers.py --truth <folder>/truth.json --hyp <folder>/hyp.jsonl

Starting from the app's own phrases means the truth uses its phrase boundaries; phrases the app missed
completely are not in the draft (add them by hand if you notice them while listening to system.wav).
"""
import argparse
import json
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    a = ap.parse_args()
    folder = Path(a.folder)
    hyp_path = folder / "hyp.jsonl"
    out = folder / "truth_draft.json"
    if not hyp_path.exists():
        sys.exit(f"Error: {hyp_path} not found (is this a `run --record` folder?)")
    if out.exists():
        sys.exit(f"Error: {out} already exists; not overwriting it.")
    hyp = [json.loads(l) for l in open(hyp_path, encoding="utf-8") if l.strip()]
    draft = [{"start_s": h["start_s"], "end_s": h["end_s"], "speaker": "",
              "printed": h["label"], "hy": h["hy"]}
             for h in hyp if h["source"] == "system" and h["hy"]]
    out.write_text(json.dumps(draft, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(draft)} system phrases -> {out}")
    print("Fill in \"speaker\" for each, delete non-speech entries, save as truth.json.")


if __name__ == "__main__":
    main()
