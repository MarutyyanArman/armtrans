# CLAUDE.md — project rules (stable)

Read PLAN.md first. These rules do not change between chats.

## What this is
Local Python terminal app: mic + system audio → Eastern Armenian ASR → English translation.
Target: Windows 11, Python 3.12, i7-1195G7 (4 cores), 40 GB RAM, no NVIDIA GPU.

## Current stack
Silero VAD (ONNX) → NVIDIA FastConformer hy, RNNT branch (ONNX, `onnx-asr`; CTC branch selectable with `--asr ctc`; after a forced cut the next phrase is recognised with 1.5 s of the previous audio as context) → NLLB-200 1.3B (CTranslate2 int8). System audio: PyAudioWPatch loopback + WeSpeaker ResNet34 speaker labels. No PyTorch, Whisper or transformers at runtime.

## Hard rules
- **Local inference only.** No cloud APIs. Models are downloaded once with `scripts/download_models.py`; the app runs with Hugging Face offline mode on and only loads from `models/`.
- **No hidden fallbacks.** If a device, model or step fails, stop or print a clear error. Never silently switch device, model, language or skip a stage.
- **No invented results.** No speed, accuracy or quality number goes into any doc unless it was measured, with the date and the command that produced it.
- **Never drop or shorten a phrase to meet the 5 s limit.** Late phrases are printed and counted in the timing log.
- **Labels never change after printing.** `Me` for mic. `Speaker N` for system audio (number of speakers found automatically; short phrases get the closest speaker, never `Speaker ?`). Mic phrases heard while call speech plays and not matching the learned `Me` voice get the call speaker's label.

## Output format
`[HH:MM:SS] <label> | <Armenian> | <English>` — time = local clock time when the phrase started. English is `(unclear)` for a 1–2 word line NLLB scores below `mt.short_min_score`; common one-word replies get fixed English (2026-11-01, DECISIONS.md).

## Definitions
- Phrase: speech ending in a pause of `vad.min_silence_ms` (600 ms) or cut at `vad.max_phrase_s` (5 s since 2026-10-31). Since 2026-11-01 (owner's decision) a phrase may grow up to `vad.max_phrase_solo_s` (8 s) while every other stream is silent; it is cut at once when another stream starts speech (`--max-phrase-solo-s 5` = old behaviour).
- Phrase end: last speech sound reaches the laptop audio device.
- 5 s limit: phrase end → line printed. Applies to every phrase, including the first.

## Out of scope
GUI, packaging, echo handling, speaker names, transcript saving, other input languages.

## How to work
- One module per chat. Change only that module and its tests.
- Run `python -m pytest -q` before saying a change works.
- Record checks and results in TESTS.md, choices in DECISIONS.md.
- Verify library APIs by running them or reading official docs, not from memory.
