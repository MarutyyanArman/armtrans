"""Cut the phrases of a `run --record` folder into WAV clips.

  python scripts/export_clips.py recordings/rec-20261101-115207          # call phrases -> clips/ (for speaker labelling)
  python scripts/export_clips.py recordings/rec-20261101-115207 --asr    # mic + call phrases -> asr_test/ (ASR test set)

Recordings made after 2026-11-01 12:30 store each phrase's exact model input in phrases/; those files are
copied as they are. Older recordings: cut from mic.wav/system.wav with 200 ms before / 100 ms after, which
is close but not identical to what the app heard (re-transcribing such cuts differed from the live text in
42% of words on rec-20261101-115207; the model is sensitive to tiny boundary changes).

--asr also writes asr_test/refs.tsv for scripts/benchmark.py: one line per clip,
  <clip.wav> TAB <Armenian> TAB <English (left empty)>
pre-filled with what the app printed live. Listen to each clip and correct the Armenian to exactly what
was said (the pre-fill only saves typing; an unchecked line counts as "the app was right").
Delete the line of a clip with no real speech. Clip names start with mic_ or sys_ so results can be
split by source. Existing files are never overwritten.
"""
import argparse
import json
import sys
from pathlib import Path

import soundfile as sf

SR = 16000


def cut(audio, h):
    a0, b0 = max(0, int((h["start_s"] - 0.2) * SR)), min(len(audio), int((h["end_s"] + 0.1) * SR))
    return audio[a0:b0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--asr", action="store_true", help="mic + call clips and a refs.tsv template in asr_test/")
    a = ap.parse_args()
    folder = Path(a.folder)
    hyp = [json.loads(l) for l in open(folder / "hyp.jsonl", encoding="utf-8") if l.strip()]
    sources = ["mic", "system"] if a.asr else ["system"]
    out = folder / ("asr_test" if a.asr else "clips")
    out.mkdir(exist_ok=True)
    refs_path = out / "refs.tsv"
    if a.asr and refs_path.exists():
        sys.exit(f"Error: {refs_path} already exists; not overwriting your corrections.")
    refs = []
    for src in sources:
        wav = folder / f"{src}.wav"
        if not wav.exists():
            print(f"(no {wav.name}, skipping {src})")
            continue
        audio, sr = sf.read(wav, dtype="float32")
        if sr != SR:
            sys.exit(f"Error: {wav} is {sr} Hz, expected {SR}")
        rows = sorted((h for h in hyp if h["source"] == src and h["hy"]), key=lambda h: h["end_s"])
        prefix = ("mic_" if src == "mic" else "sys_") if a.asr else ""
        exact = 0
        for i, h in enumerate(rows, 1):
            name = f"{prefix}{i:02d}_{h['start_s']:06.1f}s_{h['label'].replace(' ', '')}.wav"
            if not (out / name).exists():
                src_clip = folder / h["clip"] if h.get("clip") else None
                if src_clip is not None and src_clip.exists():
                    x, _ = sf.read(src_clip, dtype="float32")
                    sf.write(out / name, x, SR, subtype="FLOAT")
                    exact += 1
                else:
                    sf.write(out / name, cut(audio, h), SR, subtype="PCM_16")
            refs.append(f"{name}\t{h['hy']}\t")
            print(f"{name}   {h['hy']}   | {h['en'][:50]}")
        print(f"({src}: {exact} exact live clips, {len(rows) - exact} cut from {wav.name})")
    if a.asr:
        refs_path.write_text("\n".join(refs) + "\n", encoding="utf-8")
        print(f"\n{len(refs)} clips + {refs_path} (pre-filled with the live output: correct it by listening)")
    else:
        print(f"\n{len(refs)} clips in {out}")


if __name__ == "__main__":
    main()
