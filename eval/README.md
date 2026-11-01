# Evaluation set (PLAN.md section 7)

- Any folder of WAV clips + `refs.tsv` works with `scripts/benchmark.py`: one line per clip `file.wav<TAB>Armenian reference<TAB>English reference` (English may be empty).
- In the GitHub repo no audio is included: rebuild the sets below with `python scripts/make_eval_sets.py`; `tts_call/` keeps only `truth.json` / `voices.txt` (the synthetic `call.wav` stays local).
- Long recordings for replay tests: keep mic and system as separate WAV files from the same call, with consent of everyone recorded.

Start with ~10 minutes: FLEURS `hy_am` test clips (read speech, with English) plus your own call clips, including the hard cases in PLAN.md.

## Built sets (2026-11-01, `python scripts/make_eval_sets.py`; results DECISIONS.md / TESTS.md L19–L20)

- `fleurs_hy/clean` — 100 FLEURS hy_am test sentences (read speech, CC-BY-4.0) + `refs.tsv` with English from FLEURS en_us.
- `fleurs_hy/phone` — same, telephone band (8 kHz, 300–3400 Hz; no codec). `fleurs_hy/noise10` — same + noise at 10 dB SNR.
- `fleurs_hy/numbers` — 40 sentences with digits (the ASR writes numbers as words, so WER is inflated).
- `hard/noise_only` — no logged speech + synthetic noise; expected: nothing printed (`scripts/check_noise_only.py`).
  Not verified by listening yet: one clip proved to contain call speech.
- Not built yet: own-call hard cases (short replies, names, a speaker returning / joining, both finishing together) — need truth labels.
