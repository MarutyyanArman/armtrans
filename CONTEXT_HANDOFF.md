# CONTEXT_HANDOFF.md — armtrans (live Armenian → English call translator)

Last updated **2026-11-01 21:45 (Asia/Yerevan)** for a fresh Claude session. Owner: Arman Marutyan.
Covers all sessions so far: 2026-10-31 build-up; 2026-11-01 speaker diagnosis, ASR comparison, RNNT switch, phrase-cut experiments, 8 s solo phrases, ASR context, full PLAN review.
**[measured]** = run on the owner's laptop (date + command in DECISIONS.md / TESTS.md). **[unverified]** / **[uncertain]** = not confirmed.
Source of truth: `CLAUDE.md` (rules), `PLAN.md`, `DECISIONS.md`, `TESTS.md` in the project root. There is **no TASKS.md**; tasks live in §7 here.

---

## 0. Where to continue (read this first)

- **Last owner request (2026-11-01 20:26):** "start the implementation from the 1st point" of the review's recommended order = **re-sync the docs** (this file, README, PLAN status/§5, requirements comments). Done at 20:40. Then (20:49) owner: "Skip 2, 3, 4, 5. Continue from the 6th point" → NLLB short-input guard done 21:00. 20:59 owner: "Skip point 7, I tested them myself" (no numbers given) → point 8 eval sets + FLEURS baseline done 21:35. 21:35 owner "yea" → point 9: approved leftovers deleted (~4.6 GB) + requirements pinned, done 21:45. **Open: owner to decide on the test-only folders (~6.6 GB, §3 Models); all 9 review points are now done or skipped by the owner.**
- **Owner feedback:** the 8 s solo-phrase version "gives better results; text appears within 5 s after the voice ends". Armenian ASR accuracy is still the main complaint.
- **Owner said "Don't change anything for now" (19:33)** about the ASR options list; then asked for the review (20:19) and point 1 (20:26). Ask before starting each next point.
- **WARNING — parallel sessions:** on 2026-11-01 another Claude session edited the same files at the same time (18:55–19:04: ASR context, `context_check.py`; 17:34: `bench_phrase_len.py`). Before editing, check file modification times (`find . -newermt ...`) and re-read files you change. Run one session at a time.

---

## 1. Project Overview

**Goal.** Local Python terminal app (Windows 11). It listens to the **microphone and system audio at the same time**, transcribes **Eastern Armenian**, translates to **English**, and prints one line per phrase:
```
[HH:MM:SS] <label> | <Armenian> | <English>
```
- Mic = `Me`. Call/system speakers = `Speaker 1, 2, …`, stable for the session; number of speakers found **automatically**.
- English must appear **≤ 5 s after the phrase ends** (VAD + queue + processing), for every phrase including the first.
- Typical call: 2–4 remote speakers, little overlap.

**Hard rules (CLAUDE.md):** local inference only; no hidden fallbacks (fail loudly); no invented results (every number has a date and a command); never drop or shorten a phrase for latency; labels never change after printing; short phrases get the closest speaker, never `Speaker ?` when a matcher is loaded; mic phrases heard during call speech that don't match the learned Me voice take the call speaker's label.

**Phrase rule (CLAUDE.md, changed 2026-11-01 by the owner):** ends on a 600 ms pause; forced cut at 5 s while the other stream is in speech; while every other stream is silent it may grow up to 8 s (`max_phrase_solo_s`), and is cut at once when the other stream starts speech.

**Out of scope:** GUI, packaging, echo handling (decision A: speaker mode duplicates are reported, not fixed), speaker names, transcript saving, other input languages. Recordings/timing logs are dev tools only (record only with consent).

**Hardware.** MSI Modern 15 A11MU, i7-1195G7 (4C/8T), Iris Xe, no NVIDIA GPU, 40 GB RAM, Windows 11, Python 3.12.4. Owner timezone Asia/Yerevan.

**Owner's working method:** start small end-to-end, measure, improve the weakest part only; one module per chat; run `pytest` before claiming anything works; choices in DECISIONS.md, results in TESTS.md.

---

## 2. Current State (verified 2026-11-01 20:19–20:40)

### Works and verified on the laptop
| Area | Status |
|---|---|
| pytest | [measured] 2026-11-01 21:00: **75 passed, 1 skipped** (`$env:PYTHONIOENCODING="utf-8"` is required; without it 2 export_clips tests fail on cp1252) |
| Mic + loopback capture, VAD, ASR, MT, timing log, replay | all live/replay runs 0 over 5 s |
| ASR = NVIDIA FastConformer-Hybrid Large hy, **RNNT** branch (owner switched 11-01) | `--asr ctc` for A/B |
| **ASR context after a forced cut** (other session, 18:57, default ON, `--asr-context-s 1.5`) | offline A/B on 22 phrases: better 11, same 7, mixed 2, unclear 2, worse 0 (Claude's judgement, no refs) |
| **8 s solo phrases** (this session, 17:44, default ON, `--max-phrase-solo-s 8`) | stress 4.43 s, single 2.96 s, adversarial 2.44 s, real-call replay 2.82 s; plain 8 s cap failed (6.02 s) |
| Translation NLLB-200 1.3B CT2 int8, beam 4 | chrF 89.0 / 80.1 on 8 sentences; owner said keep as is |
| Speaker labels (WeSpeaker ResNet34-LM, online centroids, Me auto-learn, bleed rule) | works on synthetic voices; **over-splits on real calls** |
| Test tools | record/replay, tune_speakers, export_clips, benchmark, bench_lm, cut_report, context_check, bench_phrase_len, make_overlap_test, score_speakers, make_truth_draft |

### Live calls recorded tonight (owner, with the current defaults)
| Recording | Content | Result |
|---|---|---|
| rec-20261101-180039 | mic only (system.wav 0 s), 9 lines | max 3.34 s, longest 8.2 s |
| rec-20261101-182801 | mic + call (video?), 20 lines | max 3.20 s; **4 call labels**; 11 short replies (Հա, Վա, Դա…) put on Speaker 3 at similarity 0.10–0.54; 1.7 s "Զը։" created Speaker 4 |
| rec-20261101-183144 | mic only, 6 lines | max 2.28 s |
| rec-20261101-192931 | mic + call **on laptop speakers** (context ON), 58 lines | max **4.52 s**, 12 lines > 3 s, queue wait up to 3.06 s, load 0.43; **22 of 29 mic lines = call heard by the mic ("bleed")**, printed a second time; 27 forced 5 s cuts (solo rule never applies when the mic hears the call) |
| rec-20261101-201248 | mic 72.7 s, system empty, 5 lines | **no meta.json**: session closed without Ctrl+C (not finalised) |

### Not done / not verified
- Real-call speaker accuracy (no truth labels). Headphone mode never tested (PLAN: pass/fail targets apply to headphone mode).
- NLLB short-input guard added 21:00 (§4 #13); ≈ 6 of 58 short lines still invent content ("Oh, my God." etc.).
- PLAN §8 tests (L5, L7, 60-min, battery, device path): owner says he tested them himself (20:59); no results recorded. One vs two workers: not tested.
- FLEURS baseline built 21:35: clean WER 13.6% / chrF 60.7; phone 15.3%; noise 10 dB 18.3%; numbers 24.4% (format-inflated). No-speech set: 3 of 21 clips printed a line (1 = real call speech → set needs owner listening). Speaker hard cases not built.
- `eval/mt_8.tsv` English refs written by Claude, not checked by the owner.
- rec-141437 (empty hyp/system), 123825, 123842 not inspected.

---

## 3. Technical Context

### Pipeline
```
mic (sounddevice, WASAPI) ─┐                               ┌> mic: assign_mic → Me / bleed → Speaker N
                           ├> 16 kHz chunks ─> Silero VAD ─> Phrase ─> oldest-end-first queue ─> 1 worker:
loopback (PyAudioWPatch) ──┘   (arrival-clock stamps)        (streams linked)           ASR (+context) ─> speaker ─> NLLB ─> print
```
- Phrase: 600 ms pause, or forced cut at 5 s / 8 s solo (see §1). After a forced cut the next phrase starts immediately (`continuation`) and carries the last 1.5 s of the previous phrase as ASR context.
- VAD: threshold .5, neg .35, min_speech 250 ms, preroll 200 ms, tail 100 ms. Only phrases with ASR text reach the speaker matcher. Models loaded + warmed before "Listening".
- ASR model has a lock (worker thread + capture thread when `--smart-cut asr`).

### Paths and environment
- **Laptop (authoritative, the only copy now):** `C:\Users\MSI\Desktop\armtrans\armtrans`, venv `.venv`, models in `models\`. The old cloud workspace copy (`/home/claude/work/armtrans`) no longer exists.
- **How Claude works on it:** `mcp__remote-devices__device_bash` (Linux shell on the mounted folder `$HOME/mnt/armtrans/armtrans`; edit files with Python read-modify-write; no Windows Python there) + Desktop Commander `start_process` (PowerShell) to run the venv: `cd C:\Users\MSI\Desktop\armtrans\armtrans; $env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -m pytest -q`. Long replays: start one PowerShell command and poll with `read_process_output` / file timestamps.
- Logs written by PowerShell `*>` are UTF-16 (`iconv -f utf-16 -t utf-8` to read).
- Deleting files on the laptop is not permitted by default (ask; use `_to_delete/`).
- Transcript of this session: `/root/.claude/projects/-home-claude/2f3a9c20-0f6d-5997-9daa-9ee876d9ccca.jsonl` (cloud container, may be gone). Earlier: `d761462b-…`, `050b4e4d-…`.

### Code map
```
armtrans/
  config.py    VadConfig (max_phrase_s 5, max_phrase_solo_s 8, min_silence_ms 600, opt-in adaptive_pause/smart_cut/
               smart_cut_by vad|energy|asr/smart_cut_window_s), AsrConfig (rnnt default, context_s 1.5, context_tol_s 0.08,
               context_lead_s 0), MtConfig, SpeakerConfig (match .5, min_new_s 1.5), MODELS_DIR
  capture.py   Framer, MicCapture, SystemCapture (gap filling), ReplaySource, ChunkRecorder (time-aligned WAV tee)
  vad.py       SileroVAD, Segmenter (pause / max_len / smart_cut / word_cut; solo rule `_may_extend`; carries context),
               link_streams(vads), StreamVad
  asr.py       FastConformerASR: transcribe, transcribe_with_context (RNNT token timestamps), word_starts, lock
  translate.py NllbMT (ctranslate2 + SentencePiece); short-input guard: SHORT_REPLIES, normalise, UNCLEAR
  speakers.py  SpeakerMatcher.assign / assign_mic; reasons new|match|short_match|me|me_unenrolled|bleed
  pipeline.py  queue/worker, _call_spans (±0.5 s overlap), format_line; uses context when present
  timing.py    JSONL timing log (no text): cut_reason, audio_s, vad_wait_s, queue_wait_s, asr_s, spk_s, mt_s, latency_s,
               late, spk_reason/spk_sim, cut_prob, cut_asr_s
  phrase.py    Phrase (… cut_prob, cut_asr_s, context)
  __main__.py  run / replay / devices; HF offline env; --record (mic.wav system.wav hyp.jsonl meta.json phrases/ incl. *_ctx.wav)
scripts/  download_models, benchmark, bench_lm, export_nemo, check_export, export_clips, tune_speakers, make_truth_draft,
          score_speakers, compare_mt, cut_report, context_check, bench_phrase_len, make_overlap_test, mt_short_check, make_eval_sets, check_noise_only
tests/    test_capture_vad (incl. smart cut, solo rule, context), test_pipeline, test_metrics_cli, test_asr, test_speakers,
          test_models_glue, test_recording, test_tune_speakers, test_bench_lm, conftest
eval/     mt_8.tsv, tts_call/ (call.wav 53.9 s synthetic voices, truth.json), clips/ (empty),
          fleurs_hy/{clean,phone,noise10,numbers}/ (+refs.tsv), hard/noise_only/ (+ hard/_to_delete/)
```

### Models on the laptop (`models\`)
- **Used:** `fastconformer-hy-rnnt` (default), `fastconformer-hy-ctc` (`--asr ctc`), `nllb-1.3b-ct2` (CC-BY-NC-4.0, non-commercial), `wespeaker-resnet34`, `silero_vad.onnx`.
- **Deleted 2026-11-01 21:40 (owner OK):** whisper-large-v3-turbo / medium / small, nllb-600m-ct2, opus-mt-hy-en-ct2, `results\fleurs_cache`, `eval\hard\_to_delete`.
- **Test-only, kept until the owner decides (~6.6 GB):** `omnilingual-ctc-300m` 1.52 GB (dropped), `fastconformer-arm-nopc-{rnnt,ctc}` 0.87 GB (V2, worse), `lm\` 0.67 GB + `results\lm` 1.95 GB (LM test), `.venv-export` 1.58 GB (NeMo export env). Deleting disables `benchmark --asr omni*/v2*`, `bench_lm.py`, NeMo re-export.
- Deletion is enabled for this session only; a new session must ask again (device_request_delete_permission).

### Audio devices [measured]
Mic index **9** = Intel SST Microphone Array (WASAPI). Loopback **10** = "Speakers (Realtek(R) Audio) [Loopback]", 48 kHz stereo; delivers nothing if the call plays on another device.

### Recordings (`recordings\`)
| Folder | Content |
|---|---|
| rec-…115207 | first real call, Me + 2 remote, 7 call labels; `clips\` 01–16 for owner speaker labels |
| rec-…122635 | 6 mic clips, refs fully checked by owner |
| rec-…124830 | 6 mic + 14 call clips, refs partly corrected (flagged lines §7) |
| rec-…154627 | 15:47 continuous reading, mic only; used for cut/solo tests |
| rec-…180039 / 182801 / 183144 / 192931 / 201248 | tonight's live calls (§2) |
| smoke, 123825, 123842, 124015, 141437 | smoke / not inspected (124015 used by context_check) |

### CLI (laptop)
```powershell
cd C:\Users\MSI\Desktop\armtrans\armtrans; .\.venv\Scripts\Activate.ps1; $env:PYTHONIOENCODING="utf-8"
python -m pytest -q                                            # expect 75 passed, 1 skipped
python -m armtrans run --mic 9 --system --record recordings    # live, current defaults
python -m armtrans replay --mic-wav recordings\<rec>\mic.wav --system-wav recordings\<rec>\system.wav --hyp-out results\x.jsonl
python -m armtrans replay ... --max-phrase-solo-s 5 --asr-context-s 0     # old behaviour for A/B
python scripts\cut_report.py <wav> [--smart-cut vad|energy|asr]   # where cuts land (VAD only)
python scripts\context_check.py recordings\<rec> ...             # plain vs context ASR on saved phrase audio
python scripts\tune_speakers.py recordings\<rec> --match 0.3 0.4 0.5 --min-new 1.5 2.5
python scripts\export_clips.py recordings\<rec> --asr ; python scripts\benchmark.py --clips recordings\<rec>\asr_test --asr rnnt ctc --no-mt --show
```
Full option table: README.md.

---

## 4. Decisions (why) — details in DECISIONS.md

1. **Whisper removed:** wrong Armenian, 23–61 s per phrase.
2. **FastConformer fp32, 4 threads** (int8 ~2.5× slower).
3. **NLLB-1.3B, beam 4** (owner's choice). Hallucinates on 1-word input.
4. **Max phrase 6 → 5 s (10-31)**, then **8 s solo rule (11-01, owner: "if it doesn't affect the 5 s rule")**: plain 8 s cap broke the worst case (6.02 s); solo rule keeps it at 4.43 s.
5. **One worker, oldest-first queue.** No PyTorch at runtime.
6. **Speaker rework (10-31):** Me auto-learned, bleed rule, no `Speaker ?` with a matcher. Only phrases with text reach the matcher.
7. **`run --record` + exact phrase audio (11-01):** re-cut clips differed from live text by WER 41.8%.
8. **ASR comparison (11-01), 26 clips / 119 words:** checked call lines CTC 18 / RNNT 13 / CTC+LM 11 wrong words; mic CTC 6 / RNNT 4 / CTC+LM 4. LM adds plausible wrong real words. V2 YSU worse, Omnilingual 65–69% WER.
9. **RNNT adopted (owner, 11-01)**, +~0.05 s per phrase; `--asr ctc` kept, no automatic fallback.
10. **Speaker thresholds not changed until truth labels exist** (same voice 0.32–0.59, different voices up to ~0.4–0.5 on rec-115207).
11. **Smarter cuts (11-01), all opt-in, NOT adopted:** adaptive pause (no effect: the reader never pauses 120–600 ms), smart cut vad/energy, ASR word-boundary cut (`--smart-cut asr`, +0.14–0.17 s per cut). Across 2 recordings: current 9 cuts / 3 word splits vs asr 10 / 2–3: a tie. Clean word cuts still split sentences.
13. **NLLB short-input guard (11-01 21:00, owner: point 6):** fixed English for 18 common replies; `(unclear)` for ≤ 2-word lines with NLLB score < −0.9; `--no-mt-short-guard` = off. Invented short lines ≈ 18 → 6 of 58 (Claude's judgement, threshold tuned on the same data).
12. **ASR context after a forced cut (other session, 11-01, owner chose option 1):** adopted as default, 1.5 s; rule: a word is new if its first token starts ≥ join − 0 s or its last token ≥ join + 0.08 s; first letter capitalised. ASR time 0.06–0.33 s with context.

---

## 5. Problems and findings (open)

| Problem | Status |
|---|---|
| Speaker mode: the mic hears the call → most call lines printed twice (rec-192931: 22/29 mic lines), load doubled, solo rule never applies | by design (echo out of scope) — owner should decide headphones vs speakers |
| Delay margin tight in speaker mode (max 4.52 s live, 4.30–4.46 s stress) | 0 over so far; battery / call-app load untested |
| Real calls over-split speakers; short replies matched at 0.10–0.54 | diagnosed, not fixed; needs truth labels |
| Short mic tail after a cut relabelled as call speaker by the bleed rule | defect, fix awaiting OK (bleed only for mic phrases ≥ `min_new_s`) |
| First-ever call phrase creates Speaker 1 even if short (PLAN says only ≥ 1.5 s may create) | deviation, documented in code |
| ASR context vs test tools: export_clips/benchmark use only the phrase audio, not `*_ctx.wav` → offline text ≠ live text for continuation phrases | inconsistency, not fixed |
| ASR context: previous line keeps its broken last word, next line repeats it whole; a continuation whose words all fall in the context prints nothing (logged as empty ASR) | known limitation |
| ASR quality on call audio | RNNT + context; FLEURS read speech 13.6% WER vs ~24% on checked call lines → call conditions are the gap; options list given to owner (below) |
| NLLB 1-word hallucination | mostly fixed 21:00 (guard); confident inventions like "Oh, my God." remain |
| Same-stream speaker turn without a pause becomes one line with one label (8.2 s line on rec-124830) | VAD can't see it at any cap |

**ASR improvement options given to the owner (18:36), ordered cheap → big:** 0 test set (owner corrects refs.tsv) — prerequisite; 1 ASR context — **done by the other session**; 2 correction list for known errors/names; 3 CTC + small conversational LM with hotwords (plausible wrong words risk, RAM/KenLM issue); 4 audio input (headset, gain; noise suppression may hurt); 5 fine-tune FastConformer on conversational Armenian (GPU, hours of transcribed data, export pipeline exists); 6 other models (none fit); 7 local LLM correction (not recommended).

**Resolved along the way (history):** stale commits to laptop (old workspace sync), base64 corruption, benchmark imported removed OpusMT, tune_speakers no-truth mode, pyctcdecode "▁", ARPA check like-for-like, numpy float in timing JSON (energy cut, fixed 11-01), PYTHONIOENCODING needed for 2 tests.

---

## 6. Owner's stated priorities (2026-11-01)
- Speaker labels: improve recognition; wants explanation, parameters, options.
- Armenian ASR: "works 50/50"; improve via models, libraries, dictionaries. Main remaining complaint.
- Translation: keep as is for now.
- Chopped sentences: solved well enough by the 8 s solo rule (owner confirmed better results).

---

## 7. Next steps (review 2026-11-01 20:19, recommended order)

1. ~~Re-sync docs~~ — **done 20:40** (this file, README options/tests, PLAN status + §5 measured numbers + rows, requirements comments).
2. **Headphones vs laptop speakers decision** (owner) + one **headphone call** with `run --mic 9 --system --record recordings`: checks "Me = 100%", the solo rule in real use, no duplicates.
3. **Ground truth from the owner:** speaker labels for rec-182801 and rec-192931 (and rec-115207 clips 01–16); correct `asr_test\refs.tsv` for 2 calls (rec-124830 flagged lines: mic_02 `Մաբանություն`; sys_06 `սրրտաբանության`, `աշխատատում`; sys_07 `Հ է`; sys_08 `Բվել`, `վիդոդասերի`; sys_13 `Ար յութոթ ի չէը`; sys_03 `բոլորներով`; sys_02 `ողջ`); check `eval/mt_8.tsv`.
4. **Speakers:** tune_speakers grid on truth; limit short-reply labelling; short-mic-fragment fix; then multi-profile or a better embedding model if needed.
5. **Test tools for ASR context** (export_clips/benchmark use `*_ctx.wav`), then ASR options 2 → 4 → 3/5.
6. ~~NLLB 1-word invention fix~~ — **done 21:00** (guard). Owner skipped points 2–5 for now (20:49).
7. **PLAN §8 tests:** L5 Wi-Fi off, L7 replay with call app open, 60-min run plugged in + on battery, device-path test, one vs two workers.
8. ~~Hard-case eval set + FLEURS baseline~~ — **done 21:35** (DECISIONS 2026-11-01 "Evaluation sets"). Owner to listen to the 3 noise_only clips that printed text.
9. ~~Cleanup + pinning~~ — **done 21:45** (4.6 GB deleted, requirements pinned `==`). Open: test-only folders (~6.6 GB) — owner's decision.

Progress estimate (review): core app ~90%, delay requirement ~75%, quality goals ~30%, PLAN §7–8 evaluation ~30% → **overall ≈ 60–65%**.

---

## 8. Important context for the next Claude
- **Owner style:** direct, concrete numbers, honest downsides, targeted fixes over rewrites; short replies; runnable PowerShell commands; plain explanations.
- Every number in docs needs a date and a command. Only laptop results count.
- Don't change thresholds or the phrase rule without measurement and the owner's decision. Ask before changing documented rules (CLAUDE.md). The owner sometimes says "don't change anything" — then only analyse.
- Report honestly when an earlier claim was wrong (e.g. echo hypothesis disproved; `Արեւ` was a pause-ended phrase, not a cut tail; `Օժանդակ վայրերի։` was a cut tail).
- Not a git repo; no commits.
- Owner memory file `/areas/armenian-call-translator.md` exists (planning level, may lag).

## Instructions for the next session
1. Read this file → CLAUDE.md → PLAN.md → DECISIONS.md (2026-11-01 entries) → TESTS.md.
2. Check for parallel edits: file modification times vs this file's timestamp.
3. Baseline on the laptop: `python -m pytest -q` (75 passed, 1 skipped, with PYTHONIOENCODING=utf-8).
4. All 9 review points are done or skipped. Ask the owner what next (skipped: 2 headphone test, 3 truth labels, 4 speakers, 5 ASR tools/options, 7 owner-tested; open: test-only cleanup).
