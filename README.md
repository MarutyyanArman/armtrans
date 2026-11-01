# armtrans — live Eastern Armenian → English call translator (local, CPU-only)

A Python terminal app for Windows 11 that listens to your **microphone** and to **system audio** (the other side of a
call) at the same time, transcribes **Eastern Armenian**, translates it to **English**, and prints one line per phrase:

```
[14:03:21] Speaker 2 | Բարև, ինչպե՞ս ես։ | Hi, how are you?
```

- Everything runs **on the laptop**: no cloud APIs, Hugging Face offline mode at runtime, models load only from `models/`.
- **5-second rule:** the English line must appear at most 5 s after the phrase ends (speech detection and queueing included).
- Labels: `Me` for the microphone, `Speaker 1`, `Speaker 2`, … for call voices (found automatically, never changed after printing).
- Prototype status: built and tested on one laptop (i7-1195G7, 4 cores, no NVIDIA GPU, 40 GB RAM, Python 3.12.4).

## How it works

```
mic (sounddevice) ─┐                                       ┌─ mic phrase ─────────────── label "Me" ─┐
                   ├─ 16 kHz chunks ─ Silero VAD ─ phrases ┤                                         ├─ ASR ─ NLLB ─ print
loopback (WASAPI) ─┘   (shared clock)  + segmenter          └─ call phrase ─ WeSpeaker ─ "Speaker N" ┘
```

| Stage | Model / library | Module |
|---|---|---|
| Capture | `sounddevice` (mic) + `PyAudioWPatch` (WASAPI loopback), resampled with `soxr` | `armtrans/capture.py`, `devices.py` |
| Speech detection | Silero VAD (ONNX) + phrase segmenter | `armtrans/vad.py` |
| Armenian ASR | NVIDIA FastConformer-Hybrid Large `hy`, RNNT branch (ONNX via `onnx-asr`) | `armtrans/asr.py` |
| Speaker labels | WeSpeaker ResNet34-LM embeddings + online centroid matching | `armtrans/speakers.py` |
| Translation | NLLB-200 distilled 1.3B (CTranslate2 int8, beam 4) | `armtrans/translate.py` |
| Orchestration, timing | one worker, oldest-phrase-first queue; per-phrase timing log (no transcript text) | `armtrans/pipeline.py`, `timing.py` |

**Phrase rule.** A phrase ends after a 600 ms pause. It is force-cut at 5 s while the other stream is talking, and may
grow to 8 s while every other stream is silent (cut at once when another stream starts). After a forced cut, the next
phrase is recognised with 1.5 s of the previous audio as context. Phrases are never dropped or shortened to meet the
5 s limit; late lines are printed and counted.

**Short lines.** NLLB tends to invent content for 1–2 word inputs. Common replies (`Այո` → "Yes.", `Ոչ` → "No.", …) use
fixed English; other 1–2 word lines with a low NLLB score print `(unclear)` instead of a made-up sentence.

## Repository layout

```
armtrans/        the app (python -m armtrans)
scripts/         model download, benchmarks, evaluation and diagnostic tools (all test-only except download_models.py)
tests/           pytest unit tests (stand-in ONNX models are built on the fly; one VAD test needs the Silero file)
eval/            evaluation-set notes and small text references (audio is not in the repo)
PLAN.md          goal, definitions, budget, build order
DECISIONS.md     every design choice with the measurement behind it
TESTS.md         every check: command, target, latest dated result
CLAUDE.md        stable project rules (local only, no hidden fallbacks, no invented numbers, output format)
CONTEXT_HANDOFF.md  working notes for continuing development in a new AI-assisted session
```

Not in the repository (see `.gitignore`): model weights (`models/`, ~6 GB), call recordings, logs, results,
generated evaluation audio, virtual environments.

## Setup (Windows 11, Python 3.12)

```powershell
git clone <this repo> armtrans
cd armtrans
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python scripts\download_models.py --vad --asr --speakers     # Silero VAD, FastConformer RNNT, WeSpeaker
# Translation model: one-time conversion needs PyTorch + transformers (setup only, not used at runtime)
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-setup.txt
python scripts\download_models.py --mt                       # ~5.5 GB download, converts NLLB-1.3B to CTranslate2 int8
$env:PYTHONIOENCODING = "utf-8"
python -m pytest -q
```

Use Windows Terminal so Armenian renders, and set `$env:PYTHONIOENCODING = "utf-8"` in each new terminal.
Exact package versions are pinned in `requirements.txt` (the set that passes the tests on the target laptop).

## Usage

```powershell
python -m armtrans devices                 # list microphones (--mic) and loopback devices (--system)
python -m armtrans run --mic 9 --system    # mic = "Me", call audio = Speaker 1, 2, ...
python -m armtrans run --system            # call audio only
python -m armtrans run --mic 9             # mic only
python -m armtrans run --mic 9 --system --record recordings   # testing: also save time-aligned WAVs (get consent!)
python -m armtrans replay --mic-wav <mic.wav> --system-wav <system.wav> --hyp-out results\x.jsonl   # real-time replay
```

All models are loaded and warmed up before "Listening" is printed. Ctrl+C prints a latency summary and label counts;
the timing log is written to `logs\`. Use headphones: on laptop speakers the mic also hears the call, so most call lines
print twice (echo handling is out of scope).

### Options

| Option | Default | Meaning |
|---|---|---|
| `--asr` | `rnnt` | ASR decoder; `ctc` = previous decoder, for A/B |
| `--asr-context-s` | `1.5` | after a forced cut, recognise with this much previous audio (0 = off) |
| `--max-phrase-s` | `5` | forced cut while the other stream is talking |
| `--max-phrase-solo-s` | `8` | longest phrase while the other stream is silent (5 = old behaviour) |
| `--min-silence-ms` | `600` | pause that ends a phrase |
| `--mt-beam` | `4` | translation beam size |
| `--no-mt-short-guard` | guard on | disable fixed short replies / `(unclear)` for 1–2 word lines |
| `--asr-threads` / `--mt-threads` | `4` / `4` | CPU threads |
| `--spk-match` / `--spk-min-new-s` | `0.5` / `1.5` | speaker matching thresholds (untuned) |
| `--adaptive-pause`, `--smart-cut [vad\|energy\|asr]`, `--smart-cut-window` | off | cut experiments, measured and not adopted (DECISIONS.md) |

## Testing

```powershell
$env:PYTHONIOENCODING = "utf-8"
python -m pytest -q                       # 2026-11-01 laptop: 75 passed, 1 skipped
python scripts\compare_mt.py --models nllb-1.3b --beam 4          # translation check on eval\mt_8.tsv
python scripts\make_eval_sets.py          # rebuild FLEURS hy_am + hard-case sets (needs internet once)
python scripts\benchmark.py --clips eval\fleurs_hy\clean --asr rnnt --show
python scripts\score_speakers.py --truth <truth.json> --hyp results\hyp.jsonl
```

Without `PYTHONIOENCODING=utf-8`, two `export_clips` tests fail on the Windows console encoding (cp1252).
Other tools (`tune_speakers.py`, `cut_report.py`, `context_check.py`, `mt_short_check.py`, `check_noise_only.py`,
`bench_lm.py`, …) are described in TESTS.md. `bench_lm.py` needs the optional `pip install pyctcdecode==0.5.0 --no-deps`.

## Measured results (laptop, 2026-11-01; commands and details in TESTS.md / DECISIONS.md)

| What | Result |
|---|---|
| Processing time per phrase (ASR + speaker + translation) | 5 s phrase ≈ 1.3–2.2 s; 8 s phrase ≈ 1.9–3.0 s |
| Worst-case delay (same speech on both streams, both cut at 5 s) | max 4.30–4.46 s, 0 lines over 5 s |
| Live call, laptop-speaker mode, 58 lines | max 4.52 s, 0 lines over 5 s |
| ASR on FLEURS hy_am (100 read sentences) | WER 13.6% (clean) |
| ASR on checked real-call lines | ≈ 24% word errors (13 of 54 hand-checked words; tiny sample) |
| Translation, 8 reference sentences (`compare_mt.py`) | chrF 89.0 (reference Armenian) / 80.1 (ASR Armenian) |
| Speaker labels, synthetic 10-phrase call | 10/10 correct, 0 swaps |

## Known limitations

- **Speaker over-splitting on real calls:** more labels than people; short replies are matched weakly.
- **Armenian accuracy on calls** is clearly worse than on read speech; the ASR is not fine-tuned on call audio.
- **Laptop-speaker mode** duplicates call lines as `Me` and is the tightest case for the 5 s limit.
- NLLB still produces occasional confident inventions on short or broken input.
- Numbers are written as words by the ASR, which inflates WER against digit references.
- Windows-only capture path (WASAPI loopback). Not yet run: 60-minute session, battery-power test, device-path latency test.

## Models and data licences

Model weights are downloaded by `scripts/download_models.py` and are not part of this repository; each keeps its own
licence — check the model cards before any reuse. Notably, **NLLB-200 is licensed CC-BY-NC 4.0 (non-commercial)**.
FLEURS evaluation data is CC-BY 4.0. Model sources: `facebook/nllb-200-distilled-1.3B`,
`Wespeaker/wespeaker-voxceleb-resnet34-LM`, the FastConformer `hy` ONNX repositories named in
`scripts/download_models.py`, and Silero VAD.

## Privacy

Only record calls with the consent of everyone on them. Recordings, transcripts and logs stay local and are excluded
from Git. The timing log never contains transcript text.
