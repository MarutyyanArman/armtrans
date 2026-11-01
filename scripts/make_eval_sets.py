"""Build the PLAN.md section 7 evaluation sets (test only). Needs internet once (FLEURS download).

    python scripts/make_eval_sets.py [--n 100] [--numbers 40] [--seed 0]

Creates (each folder: WAVs + refs.tsv "<file>\\t<Armenian>\\t<English>", read by scripts/benchmark.py):
  eval/fleurs_hy/clean/    first --n distinct sentences of FLEURS hy_am test (read speech, CC-BY-4.0),
                           English reference = FLEURS en_us raw transcription with the same sentence id
  eval/fleurs_hy/phone/    same audio, telephone band: 8 kHz resample + 300-3400 Hz band-pass (no codec)
  eval/fleurs_hy/noise10/  same audio + white/pink noise at 10 dB SNR (fixed seed)
  eval/fleurs_hy/numbers/  up to --numbers other sentences whose transcription contains digits (clean audio)
  eval/hard/noise_only/    no speech: stretches of real recordings where the app found no phrase (>= 4 s)
                           + synthetic white/pink noise and a 50 Hz hum. Expected result: no printed line.
Download cache: results/fleurs_cache (safe to delete).
"""
import argparse
import csv
import json
import re
import sys
import tarfile
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

ROOT = Path(__file__).resolve().parent.parent
SR = 16000
REPO = "google/fleurs"


def read_tsv(path):
    rows = []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            if len(r) >= 3:
                rows.append({"id": r[0], "file": r[1], "raw": r[2]})
    return rows


def phone(a):
    x = soxr.resample(a, SR, 8000)
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / 8000)
    spec[(f < 300) | (f > 3400)] = 0
    return soxr.resample(np.fft.irfft(spec, len(x)).astype(np.float32), 8000, SR).astype(np.float32)


def pink(n, rng):
    w = np.fft.rfft(rng.standard_normal(n))
    f = np.arange(len(w)); f[0] = 1
    return np.fft.irfft(w / np.sqrt(f), n)


def add_noise(a, snr_db, rng):
    noise = 0.5 * rng.standard_normal(len(a)) + 0.5 * pink(len(a), rng)
    p_sig, p_noise = np.mean(a ** 2), np.mean(noise ** 2)
    noise *= np.sqrt(p_sig / (p_noise * 10 ** (snr_db / 10)))
    return np.clip(a + noise, -1, 1).astype(np.float32)


def write_set(folder, items, transform=None):
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "refs.tsv", "w", encoding="utf-8") as refs:
        for name, audio, hy, en in items:
            sf.write(folder / name, transform(audio) if transform else audio, SR)
            refs.write(f"{name}\t{hy}\t{en}\n")
    print(f"{folder.relative_to(ROOT)}: {len(items)} clips")


def noise_only(out, rng):
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for rec in sorted((ROOT / "recordings").glob("rec-*")):
        hyp = rec / "hyp.jsonl"
        if not (rec / "meta.json").exists() or not hyp.exists():
            continue
        spans = [json.loads(l) for l in hyp.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not spans:       # nothing logged at all (e.g. rec-141437, cause unknown): gaps are not proven speech-free
            continue
        for src, wav in (("mic", "mic.wav"), ("system", "system.wav")):
            if not (rec / wav).exists() or sf.info(rec / wav).duration < 5:
                continue
            a, sr = sf.read(rec / wav, dtype="float32")
            busy = sorted((s["start_s"] - 1.0, s["end_s"] + 1.0) for s in spans if s["source"] == src)
            t, gaps = 0.0, []
            for b0, b1 in busy + [(len(a) / sr, len(a) / sr)]:
                if b0 - t >= 4.0:
                    gaps.append((t, min(b0, t + 8.0)))
                t = max(t, b1)
            for g0, g1 in gaps[:2]:
                seg = a[int(g0 * sr):int(g1 * sr)]
                if np.sqrt(np.mean(seg ** 2)) < 1e-4:      # digital silence (loopback idle) - not useful
                    continue
                sf.write(out / f"{rec.name}_{src}_{g0:.0f}s.wav", seg, sr)
                n += 1
    for name, sig in (("synthetic_white.wav", 0.01 * rng.standard_normal(SR * 6)),
                      ("synthetic_pink.wav", 0.02 * pink(SR * 6, rng)),
                      ("synthetic_hum50.wav", 0.05 * np.sin(2 * np.pi * 50 * np.arange(SR * 6) / SR))):
        sf.write(out / name, sig.astype(np.float32), SR)
        n += 1
    (out / "README.txt").write_text("No speech in any file. Expected: VAD finds no phrase, or ASR returns no text.\n",
                                    encoding="utf-8")
    print(f"{out.relative_to(ROOT)}: {n} clips")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--numbers", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    from huggingface_hub import hf_hub_download
    cache = ROOT / "results" / "fleurs_cache"
    get = lambda f: hf_hub_download(REPO, f, repo_type="dataset", cache_dir=str(cache))
    hy = read_tsv(get("data/hy_am/test.tsv"))
    en = {r["id"]: r["raw"] for r in read_tsv(get("data/en_us/test.tsv"))}
    print(f"FLEURS hy_am test: {len(hy)} rows; en_us test: {len(en)} sentences; first row: {hy[0]}")
    first = {}
    for r in hy:                                       # one recording per sentence id, file order
        first.setdefault(r["id"], r)
    ids = sorted(first, key=int)
    main_ids = ids[:a.n]
    num_ids = [i for i in ids[a.n:] if re.search(r"\d", first[i]["raw"])][:a.numbers]
    want = {first[i]["file"]: i for i in main_ids + num_ids}
    audio = {}
    with tarfile.open(get("data/hy_am/audio/test.tar.gz"), "r:gz") as tar:
        for m in tar:
            name = Path(m.name).name
            if name in want:
                x, sr = sf.read(tar.extractfile(m), dtype="float32")
                audio[want[name]] = soxr.resample(x, sr, SR).astype(np.float32) if sr != SR else x
    missing = [i for i in main_ids + num_ids if i not in audio]
    if missing:
        sys.exit(f"audio missing for ids {missing[:10]}")
    item = lambda i: (f"fleurs_{int(i):05d}.wav", audio[i], first[i]["raw"], en.get(i, ""))
    base = ROOT / "eval" / "fleurs_hy"
    rng = np.random.default_rng(a.seed)
    main_items = [item(i) for i in main_ids]
    write_set(base / "clean", main_items)
    write_set(base / "phone", main_items, phone)
    write_set(base / "noise10", main_items, lambda x: add_noise(x, 10, rng))
    write_set(base / "numbers", [item(i) for i in num_ids])
    print(f"English refs found for {sum(1 for i in main_ids + num_ids if en.get(i))}/{len(main_ids + num_ids)}")
    noise_only(ROOT / "eval" / "hard" / "noise_only", rng)


if __name__ == "__main__":
    main()
