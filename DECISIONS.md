# DECISIONS.md

Each entry: date, decision, options considered, evidence, status.

## 2026-10-31 — Echo / speaker mode: option A
- Decision: echo handling stays out of scope. Speaker mode allowed; remote speech may also appear as `Me`.
- Options: A (allow, measure duplicates), B (add duplicate removal), C (require headphones).
- Evidence: owner's choice. Duplicate rate not yet measured.
- Status: confirmed.

## 2026-10-31 — Phrase and timing definitions
- Decision: pause ~0.6 s ends a phrase; max phrase ~6 s; time field = phrase start (HH:MM:SS); limit measured from when sound reaches the laptop audio device.
- Evidence: owner approved. Values are starting points, tuned by measurement.
- Status: confirmed.

## 2026-10-31 — Unclear short phrases print `Speaker ?`
- Decision: phrases under ~1.5 s that don't clearly match a known speaker print `Speaker ?` instead of guessing.
- Status: confirmed. Threshold values not yet tuned.

## 2026-10-31 — Prototype stack (provisional, NOT yet measured on target laptop)
- VAD: Silero VAD (ONNX). [updated below]
- ASR: `faster-whisper` small. [replaced below by FastConformer]
- Translation: OPUS-MT `Helsinki-NLP/opus-mt-hy-en` converted to CTranslate2.
- Mic capture: `sounddevice`. Loopback capture (step 3) will use `PyAudioWPatch`.
- Why provisional: chosen only to get an end-to-end prototype running. `scripts/benchmark.py` must be run on the laptop and its numbers recorded here before any of these are confirmed.

## 2026-10-31 — Silero VAD via faster-whisper's bundled ONNX file — SUPERSEDED (see next entries)

## 2026-10-31 — Whisper removed; ASR = NVIDIA FastConformer-Hybrid Large (hy), CTC, ONNX
- Trigger: owner ran faster-whisper `small` on the laptop. Observation (owner, not measured with the benchmark): Armenian transcription was incorrect and slow. No numbers recorded.
- Decision: remove Whisper completely. Use `OpenVoiceOS/stt_hy_fastconformer_hybrid_large_pc_onnx` (ONNX export of `nvidia/stt_hy_fastconformer_hybrid_large_pc`, CC-BY-4.0) run with `onnx-asr` on ONNX Runtime.
- Options considered: Whisper large-v3-turbo (slower on CPU); Armenian fine-tuned Whisper small/large-v2 (self-reported ~38% / ~40% WER on Common Voice); community FastConformer without punctuation (`mheryerznka/stt_arm_fastconformer_hybrid_large_no_pc`, self-reported lower WER, but needs NeMo and has no punctuation); Meta Omnilingual ASR (only Apple runtimes found).
- Evidence: NVIDIA model card reports CTC WER 11.19% (Common Voice test), 13.23% (FLEURS test); read speech, partly same corpora as training. ONNX export is CTC only.
- Downloaded revision: `f7db1fad9af6bd8a7a9e08fca02068c403b35468` (590 MB on disk; the repo now also contains `model.int8.onnx`, 132 MB — the model card I read earlier said there was no int8 file).
- Status: confirmed for speed. Accuracy still unmeasured (needs reference transcripts).

## 2026-10-31 — Whisper failure, measured from the owner's own timing logs (laptop)
- 3 live sessions with faster-whisper (default settings, `small` unless the owner changed it), 31 phrases of 0.5–6.2 s.
- ASR time was bimodal: 9 phrases took 2.0–3.6 s ("normal"), 22 took 23–61 s ("stuck", consistent with decoding loops). One 0.74 s phrase took 61 s and returned empty text.
- Because the queue is serial, stuck phrases delayed everything after them: worst latency 557 s.
- Even "normal" runs (2–3.6 s for 1.3–6.2 s of audio) miss the ~2.0 s budget for a 6 s phrase.

## 2026-10-31 — ASR runtime settings: fp32, 4 threads (measured on the laptop)
- Synthetic audio (speed of this CTC model depends on length, not content), min/max of 3 runs, after warm-up:

  | variant | threads | 1 s | 2 s | 4 s | 6 s |
  |---|---|---|---|---|---|
  | fp32 | 2 | 0.06–0.08 | 0.10–0.11 | 0.17–0.19 | 0.23–0.24 |
  | fp32 | 4 | 0.06–0.13 | 0.07–0.09 | 0.14–0.15 | **0.17–0.18** |
  | fp32 | 8 | 0.06–0.12 | 0.18–0.25 | 0.16–0.22 | 0.19–0.30 |
  | int8 | 2 | 0.12–0.15 | 0.24–0.25 | 0.44–0.44 | 0.66–0.76 |
  | int8 | 4 | 0.08–0.13 | 0.14–0.15 | 0.27–0.30 | 0.45–0.48 |
  | int8 | 8 | 0.14–0.22 | 0.20–0.65 | 0.36–0.47 | 0.43–0.46 |

- Decision: fp32 + 4 threads. int8 is ~2.5× slower on this CPU; 8 threads is no faster than 4 and more variable. Model load ~5.6 s.
- 6 s phrase in ~0.18 s = ~3% of real time, vs the 0.33 target (PLAN.md section 5).

## 2026-10-31 — Translation is now the weakest part (observation, not yet measured with chrF)
- Live mic session on the laptop: translation took 0.14–1.57 s per phrase (ASR 0.04–0.18 s), so it now dominates processing time.
- Several OPUS-MT outputs lost or changed the meaning of colloquial Armenian. Needs reference translations before deciding (candidates in PLAN.md: NLLB-200 600M / 1.3B).

## 2026-10-31 — Silero VAD: official ONNX file extracted from the `silero-vad` 6.2.3 wheel
- Reason: removing faster-whisper removed its bundled VAD file. The official file (MIT, 2.3 MB) is extracted by `scripts/download_models.py --vad` without installing the package (which would pull in PyTorch).
- Evidence: signature verified (input[B,576], state[2,B,128], sr int64 → output[B,1], stateN). On synthetic Armenian speech (espeak-ng) it found the same 3/3 phrases as the previous file, pause wait 0.62 s.
- Status: confirmed for the prototype.

## 2026-10-31 — OPUS-MT tokenisation with SentencePiece + "</s>"
- Reason: avoids needing `transformers` at runtime.
- Evidence: not yet. `scripts/download_models.py --mt` compares our tokens with the official MarianTokenizer on sample sentences and stops if they differ.
- Status: provisional until that check passes on the laptop.

## 2026-10-31 — Empty ASR results are logged, not printed
- Reason: an empty line has nothing to translate. It is counted in the timing log and session summary, so it's visible.
- Status: confirmed for the prototype.

## 2026-10-31 — First accuracy baseline: 8 read sentences, live mic (laptop)
- Setup: owner read 8 prepared Eastern Armenian sentences into mic 9 (built-in Intel SST mic, WASAPI); other people were talking in the room.
- ASR: WER 9/40 = 22.5%, CER 26/195 = 13.3%. Two wrong words came from background speech that the VAD joined to sentence #3; without them WER 7/40 = 17.5%, CER 11/195 = 5.6%.
- Perfect: "Այսօր եղանակը շատ լավ է", "Այո, համաձայն եմ", and #3 apart from the joined background words.
- Error types: word split ("Բարև" → "Բա արեւեւ"), missing or extra letters ("ունենք" → "ունեք", "ուրբաթ" → "ուրբը", "Զանգիր" → "Զահանգիր"), a dropped last word ("եմ"), the question mark lost.
- Latency for these 8: 0.69–0.76 s (all ≤ 5 s).
- Translation (OPUS-MT), Claude's provisional 1–3 rating, owner to confirm:
  1 "How do you feel?" = 2 · 2 "Tomorrow you have 10 o'clock." = 1 (meeting lost) · 3 "And I'm now working on a new project." = 3 ·
  4 "I can send me a document." = 1 (question lost) · 5 "The weather is very good today." = 3 · 6 "We have to finish up to the fibs and the fibs." = 1 (invented content) ·
  7 "Yeah, I agree." = 3 · 8 "Tell me at night." = 1 (should be "Call me in the evening, please").
- Conclusion: translation is now the weakest part. Part of its errors start in ASR (#2, #4, #6), but OPUS-MT also turns small ASR errors into wrong meaning or invented words.
- Caveat: 40 words from one speaker is far too small for a reliable number; it is a first baseline only.

## 2026-10-31 — Translation comparison: OPUS-MT vs NLLB-200 600M / 1.3B (laptop, CTranslate2 int8, 4 threads)
- Data: `eval/mt_8.tsv` = the 8 read sentences, as said (hy_ref) and as recognised live (hy_asr). English references written by Claude, owner to check. Script: `scripts/compare_mt.py`; results `results/compare-mt-20261031-222305.json`.
- Models converted from `facebook/nllb-200-distilled-600M` and `facebook/nllb-200-distilled-1.3B` (snapshot 7be3e246…) → `models/nllb-600m-ct2` (594 MB), `models/nllb-1.3b-ct2` (1318 MB). NLLB licence is CC-BY-NC-4.0 (non-commercial; fine for personal use — verify before any other use).
- Tokenizer check: transformers 5.22.1 warns about a tokenizer regex; tokens were identical to NLLB's own SentencePiece model on 18/18 test strings, so the warning does not affect this input.

  | model | beam | chrF exact text | chrF ASR text | worst time, short sentence | long 5–6 s phrase, mean / max |
  |---|---|---|---|---|---|
  | OPUS-MT | 2 | 62.5 | 47.2 | 0.08 s | — |
  | OPUS-MT | 4 | 62.6 | 46.8 | 0.09 s | — |
  | NLLB-600M | 2 | 81.3 | 60.5 | 0.50 s | — |
  | NLLB-600M | 4 | 82.8 | 61.0 | 0.62 s | 0.97 / 1.09 s |
  | NLLB-1.3B | 2 | 79.0 | 81.4 | 1.02 s | 1.64 / 1.95 s |
  | NLLB-1.3B | 4 | 89.0 | 80.1 | 1.23 s | 1.63 / 1.81 s |

- Meaning: NLLB-1.3B translated #4, #6, #8 correctly even from the flawed ASR text ("Can you send me the document?", "We have to be done by Friday.", "Call me in the evening, please."). Its remaining errors on ASR text come from ASR errors (#1 "Բա արեւեւ" → "It's sunny like you"; #3 background words → "As you know,"). NLLB-600M still failed #1, #4, #6 on ASR text.
- Latency arithmetic (not yet measured with two streams): worst case two 6 s phrases ending together = 0.62 + 2 × (0.18 ASR + MT). NLLB-600M beam 4: ≈ 3.2 s. NLLB-1.3B: ≈ 4.9 s — under 5 s but with almost no margin.
- Caveat: 8 short sentences, one speaker, Claude-written references. chrF differences of a few points are noise at this size.
- Status: OPUS-MT is clearly worse. Choice between NLLB-600M and NLLB-1.3B pending owner decision and a two-stream latency test.

## 2026-10-31 — Translation: NLLB-200 1.3B (owner's choice), beam 4; max phrase 5 s
- OPUS-MT removed from the app (kept only inside scripts/compare_mt.py for reproducibility).
- Runtime tokenisation uses SentencePiece directly (no transformers at runtime). `download_models.py --check-mt` on the laptop: tokens AND decoded output identical to the official tokenizer on 5/5 sentences.
- Max phrase lowered 6 s → 5 s to keep margin (worst-case estimate with 6 s phrases was ≈ 4.9 s).
- Known issue (laptop, 2026-10-31): NLLB adds words to one-word inputs — "Այո" → "Yes, you did.", "Օքեյ" → "Okay, here we go." This breaks "0 invented content" for very short phrases. Not fixed yet.

## 2026-10-31 — System audio + speaker labels (step 3 + 4)
- Owner confirmed: speakers to label are the people in the call (system audio); mic stays "Me".
- Loopback: PyAudioWPatch 0.2.12.9 (WASAPI loopback). Laptop default loopback = index 10 "Speakers (Realtek(R) Audio) [Loopback]", 2 ch, 48 kHz. `--system` with no index uses the default.
- Gap filling: if loopback sends nothing for 0.1 s, silence is pushed for the missing time (stamped with the clock) so phrases still end.
- Voice fingerprints: WeSpeaker ResNet34-LM ONNX (Wespeaker/wespeaker-voxceleb-resnet34-LM @ f0c48c29…, 26.5 MB, CC-BY-4.0), feats[B,T,80] → embs[B,256], run via onnx-asr 0.12.0's speaker-embedding loader. No PyTorch.
- Matching rule as approved (PLAN.md): long (≥ 1.5 s) phrases create/update speakers at similarity < / ≥ 0.5; short phrases need ≥ 0.6, else "Speaker ?". Only phrases with recognised text reach the matcher.
- Test (laptop): synthetic 3-speaker call (Windows voices David, Zira, Irina; 10 phrases incl. two < 1.5 s), `eval/tts_call/`.
  - Replay and live loopback both gave labels 1,2,3,1,?,3,1,2,1,3 = correct for 9/9 numbered phrases, 0 spurious speakers, 0 swaps; the short "Sure." → "Speaker ?" (sim 0.47), short "Okay." → Speaker 1 (sim 0.68–0.70).
  - Similarities: same voice 0.85–0.91; different voices up to 0.47 (Zira vs Irina, both female). The 0.5 threshold is close to that; thresholds are NOT tuned — needs real call recordings. Synthetic voices are easier than real people.
  - Speaker step time 0.02–0.12 s per phrase.
- Latency (laptop, NLLB-1.3B):
  - system only, live loopback: 10 phrases, 1.19–2.44 s, median 2.15 s.
  - mic + system together (call played through laptop speakers, room conversation on the mic = speaker mode): 24 phrases, max 3.39 s, 0 over 5 s, max queue wait 1.08 s. The mic labelled the echoed call and room speech as "Me" (accepted, option A).

## 2026-10-31 - Speaker labels reworked (mic = Me, call speakers found automatically)
- "Speaker ?" removed; short call phrase gets the closest known speaker. Me voice learned from mic phrases with no call speech overlapping; mic phrases overlapping call speech that are not Me and closer to a call speaker get that speaker's label.
- Laptop replay of call.wav: purity 10/10, 0 spurious, 0 swaps (synthetic voices). Bleed rule unit-tested only.

## 2026-11-01 — Test-only recording of live sessions (`run --record DIR`)
- Why: speaker thresholds cannot be tuned without real call audio and truth. Real live session `logs/timing-20261031-230652.jsonl` (after the speaker rework) gave 8 call-speaker labels for 29 phrases; new speakers were created at best similarity 0.08–0.45 and short phrases were given Speaker 1 at 0.17–0.28. How many people really spoke is unknown, so this may be over-splitting or real; it cannot be scored.
- What: `run ... --record DIR` writes `DIR/rec-<time>/mic.wav`, `system.wav` (16 kHz mono PCM16, exactly the audio the VAD saw), `hyp.jsonl` (labels, times, text, spk_reason, spk_sim — same format as `replay --hyp-out`) and `meta.json` (devices, settings, timing-log path, per-file padding/lead/lag).
- Alignment: all files share one start time t0; each file starts with silence up to its first chunk; arrival jitter is not padded (audio written back to back, so no drift is added); a jump > 0.25 s is filled with silence. Replay of two recorded streams reproduces live phrase positions within ±0.04 s (unit test with stand-in VAD). Real-device drift between mic and loopback over a long call is **not measured**; meta.json reports max lead/lag to check it.
- A recording write error stops the app with a clear message (no silent loss of the test data); the live pipeline keeps getting the audio until it stops.
- `scripts/make_truth_draft.py <folder>`: truth draft from the printed system phrases for the owner to fill in (speaker names), then `score_speakers.py`.
- Recordings are a dev tool only (transcript saving stays out of scope for the app). Record only with everyone's consent.
- Smoke test 2026-11-01 (laptop, `run --system --record recordings\smoke`, nothing playing): folder, hyp.jsonl and meta.json written; loopback delivered no audio, so system.wav was empty and the summary now says so. Writing real audio from live devices is so far covered only by unit tests.

## 2026-11-01 — Real call over-split (8 labels for 2 speakers); offline tuner
- Owner (2026-11-01): the 23:06 session (`logs/timing-20261031-230652.jsonl`) had 2 remote speakers on different hardware, plus Me. App printed Speaker 1–8 → 6 spurious.
- What the log shows (no audio, so not provable): Speaker 1 stable at start and end (matches 0.51–0.78). Speakers 2–8 were all created in the middle of the call, at best similarity 0.08–0.45, during a stretch where the owner talked a lot and several mic phrases overlapped call speech. Short call phrases there got Speaker 1 at similarity 0.17–0.28 (i.e. clearly not Speaker 1).
- Why a threshold change alone cannot fix it (from the logged numbers): the 7 new speakers were created at best similarities 0.15, 0.36, 0.42, 0.10, 0.37, 0.45, 0.08. Only one of them is the real second speaker; to stop the other six, the match threshold would have to be below 0.08, which would also merge different people (synthetic test: different voices reach 0.52).
- Hypotheses, untested: (a) my own voice returning through the remote side (their speakers → their mic) and appearing in system audio as "new" voices; (b) the second speaker's channel (different hardware/codec, short phrases) gives unstable fingerprints; (c) overlapping speech. `scripts/tune_speakers.py` checks (a) directly (similarity of each call phrase to my learned voice) and (b) via same-vs-different-speaker similarity ranges.
- `scripts/tune_speakers.py <record folder>`: cuts phrases from system.wav, computes fingerprints once with the real model, reports separability and echo check, re-runs the app's SpeakerMatcher for a grid of `--match`/`--min-new` and scores each with score_speakers.score. Sanity check on the synthetic call reproduces the live result (TESTS.md L12).
- Next: one real recorded call with this setup, truth filled in, then decide the fix by measurement (threshold/min length, echo rule, or per-speaker multi-profile matching).

## 2026-11-01 — First recorded real call (`recordings/rec-20261101-115207`): Me + 2 remote speakers, 7 call labels printed
- Owner: Me + 2 remote speakers. App printed Speaker 1–7 (16 call phrases, 3 min). Timing: 32 phrases, max latency 2.63 s, 0 over 5 s. Recording alignment: mic/system max lead/lag 16/11 ms over 185 s (meta.json).
- Not echo of my voice: similarity of call phrases to learned Me is 0.06–0.26 (one phrase 0.40) (`scripts/tune_speakers.py`, 2026-11-01).
- Fingerprints of what looks like one voice are weakly alike: the six phrases printed as Speaker 2 are 0.32–0.59 to each other (median ≈ 0.49), so the 0.5 threshold splits them. Phrases 128–134 s (printed Speaker 5/6/7) are 0.27–0.42 to each other.
- One call phrase (107 s, 1.6 s, "That's right") has similarity ≈ 0 (−0.03…0.20) to every other phrase and to Me: non-speech or a very different sound; it created Speaker 4.
- No truth file yet (owner gave only the speaker count), so correctness is unmeasured. Label counts per setting, no truth (same recording): match 0.5/min_new 1.5 → 8 labels (as printed 7); 0.4/1.5 → 6; 0.3/1.5 → 3 (one label for 13 phrases, one for the 107 s outlier, one for 128–134 s); 0.3/2.5 → 2 (one label for everything up to 123 s, the second only for the last 2 phrases). A count of 2 does not show that phrases are assigned to the right person.
- Separate defect, seen in the same session: a mic phrase cut at the 5 s limit left a 0.1 s tail (103.8–103.9 s, "What is it?") that overlapped call speech, was not close to Me (0.64 vs a tail too short to fingerprint) and printed as "Speaker 1" (reason `bleed`, sim 0.207). Fix candidate: no bleed relabel for mic phrases shorter than `min_new_s`. Not changed yet.

## 2026-11-01 — Step 1 tooling: real-call ASR test set
- `scripts/export_clips.py <rec> --asr` writes mic_/sys_ clips + `asr_test/refs.tsv` (pre-filled with the live text, owner corrects by listening); `scripts/benchmark.py --clips <dir...> --no-mt` scores WER/CER overall and per source. benchmark.py fixed (still loaded the removed OPUS-MT; now NLLB).
- Finding (laptop, 2026-11-01, `results/asr_dryrun`, copy of rec-20261101-115207): re-transcribing clips cut from the recording with the app's margins (200 ms / 100 ms) differed from the live text by WER 41.8%, CER 21.2% (98 words; mic 35.8% / 18.3%, call 48.9% / 24.7%). 16 of 32 phrases identical, the rest changed words on unclear speech. So the model's output on call-like speech flips with boundary shifts of tens of ms — another sign of low-confidence words; not a bug in the app.
- Consequence: `run --record` now also saves each phrase's exact model input (`phrases/<source>_<seq>.wav`, referenced in hyp.jsonl as "clip"); export uses those when present, so ASR scores on new recordings measure exactly what the app heard. Older recordings fall back to cutting (approximate).

## 2026-11-01 — ASR comparison on real call clips (step 2 of the ASR plan)
- Test set: 26 phrase clips, 119 reference words: `recordings/rec-20261101-122635/asr_test` (6 mic clips, fully checked by owner) + `recordings/rec-20261101-124830/asr_test` (6 mic + 14 call clips; owner corrected 9 call lines, the rest still equal the CTC live output → scores are biased TOWARD the current CTC model). Exact live clips (phrases/), laptop, 4 threads, `python scripts\benchmark.py --clips ... --asr ctc rnnt v2ctc v2rnnt omni omni8 omnihy --no-mt --show` (results/benchmark-20261101-132721.json and earlier runs today).
- Candidates:
  - ctc = current app: NVIDIA stt_hy_fastconformer_hybrid_large_pc, CTC branch (OpenVoiceOS ONNX).
  - rnnt (V1) = same NVIDIA model, RNNT branch: Vana-Labs/stt-fastconformer-armenian @ ec92cef5 (ONNX, CC-BY-4.0), onnx-asr `nemo-conformer-rnnt`, no conversion needed.
  - v2ctc / v2rnnt (V2) = mheryerznka/stt_arm_fastconformer_hybrid_large_no_pc @ 222f6451 (Apache-2.0, YSU; +145 h YouTube pseudo-labels; no punctuation/capitals). Exported here with `scripts/export_nemo.py` (NeMo 3.0.0 in a separate `.venv-export`, PyTorch only there); `scripts/check_export.py`: ONNX = NeMo on 5/5 clips for RNNT and 5/5 for CTC.
  - omni / omni8 / omnihy = Meta Omnilingual ASR CTC 300M v2, OpenVoiceOS ONNX @ bc55e1de (Apache-2.0), greedy CTC in scripts/benchmark.py; omnihy = output limited to Armenian letters.
- Results (WER = wrong words / reference words; CER characters):

  | model | all 26 clips (CTC-biased) | 12:26 mic, fully checked (18 words) | 12:48 call lines owner corrected (54 words) | slowest clip |
  |---|---|---|---|---|
  | ctc (current) | WER 18.5% CER 6.2% | 6/18, CER 13.9% | 16/54, CER 8.8% | 0.14–0.19 s |
  | rnnt (V1) | 22.7% / 8.3% | **4/18**, 13.9% | **15/54**, 8.2% | 0.21–0.22 s |
  | v2ctc | 39.5% / 11.4% | 9/18, 19.0% | 21/54, 9.8% | 0.13 s |
  | v2rnnt | 35.3% / 10.4% | 10/18, 19.0% | 16/54, **7.8%** | 0.20 s |
  | omni (fp32) | 68.9% / 28.9% | — | — | 1.21 s |
  | omni8 (int8) | 68.1% / 30.1% | — | — | 3.35 s (misses 0.33 RTF) |
  | omnihy | 65.5% / 25.2% | — | — | 1.94 s |

- Observations: RNNT fixed long/rare words the CTC branch broke (ֆակուլտետից, հաղորդչական, միայն վերցնելով … բայը, լսում, Խի ես, վիդեոդասերի) and was never worse than CTC on checked lines. V2 got some call words/names right (Կարմենն, բայերից, հետաձգելի … բաժանմունքում) but splits/merges words (խիես, ուրախեմ) and is worse on the owner's mic. Omnilingual 300M answered half the phrases in another script (Georgian, Arabic, Latin, Chinese); limiting it to Armenian letters made short phrases come out empty. Omnilingual dropped.
- Caveat: 119 words, two short sessions, references partly unchecked. Differences of a few words are within noise. Not yet a basis to change the app; RNNT is the leading candidate.

## 2026-11-01 — V3: CTC beam search + Armenian word 3-gram LM (test only)
- LM text: Armenian Wikipedia (wikimedia/wikipedia 20231101.hy @ b04c8d1c), 3,518,225 lines, 76 M words, NFC, և→եւ; Armenian-script lines only. Two LMs built with KenLM lmplz (workspace, built from source), order 3, `--prune 0 1 1`:
  - `models/lm/wiki_pc_3g.arpa.gz` (case + punctuation kept, for the NVIDIA model): 4.40 M unigrams, 5.15 M bigrams, 4.33 M trigrams; md5 917e1a48ee4f8ef084c090023d676dc5.
  - `models/lm/wiki_nopc_3g.arpa.gz` (normalize_hy: lower case, no punctuation, for V2): 2.28 M / 5.11 M / 4.99 M; md5 a6c4dbbf7f07272c27aa425d6c0b7fec.
- Decoder: pyctcdecode 0.5.0 (installed with --no-deps; works with numpy 2.5.4), beam 32. KenLM's Python module has no Windows wheel and the laptop has no C++ compiler, so `scripts/bench_lm.py` reads the ARPA with a pure-Python back-off reader (ArpaLM). Checked against KenLM `query` on 20 Wikipedia sentences: max difference 0.0001 log10 (rounding). Uses ~8–10 GB RAM and minutes to load: fine for a test, NOT usable in the app as is.
- Caveat: alpha/beta are chosen on the same 26 clips they are scored on, so the best LM row is optimistic.
- Results (laptop, 2026-11-01, `python scripts\bench_lm.py --clips <both asr_test> --asr ctc|v2ctc --arpa models\lm\wiki_*_3g.arpa.gz --alpha 0.3 0.5 0.8 --beta 0.5 1.5 3.0 --show`, outputs in results\lm\bench_ctc.txt, bench_v2ctc.txt). Decoding takes ≈0.01 s per phrase; loading the ARPA in Python ≈25–30 s and ~8–10 GB RAM; pyctcdecode's word trie build adds minutes.
  - All 26 clips (CTC-biased refs): ctc greedy 22/119 (18.5%, CER 6.2%) → best ctc+LM 24/119 (alpha 0.3, beta 1.5; CER 8.4%). v2ctc greedy 47/119 (39.5%) → best v2ctc+LM 40/119 (alpha 0.3, beta 0.5; CER 12.2%, worse than greedy 11.4%).
  - Only lines a person checked (12:48 call lines the owner corrected; sys_06 reference additionally cleaned of two obvious CTC typos the owner left, սրրտ- and աշխատատում — Claude's assumption), 54 words: ctc 18 (CER 9.6%), **ctc+LM 11 (9.3%)**, rnnt 13 (**7.6%**), v2rnnt 15 (7.1%), v2ctc+LM 16 (9.8%). Owner's mic 12:26 (fully checked, 18 words): ctc 6, ctc+LM 4, rnnt 4, v2ctc 9, v2rnnt 10.
- What the LM does: fixes near-miss spellings of real words (ֆակուլտետից, հետբուհական, հաղորդչական, բայերից, միայն վերցնելով … բայը, լսում, Ողջույն, սրտաբանության, աշխատում). It also replaces sounds with confident, real, WRONG words, pulled toward written Wikipedia Armenian: Կմենն→Կենն, Լտելու→Ետ, եմ→է, պատրաստվում→պատրաստվում է, Այսօր→Այսօր է, Հու→Չու (V2: Հու→ինչու, Խոսակցություն→խորհրդակցություն, լսում ե՞ս→լսում է). A garbled non-word is visibly wrong; a plausible real word is not, and NLLB will translate it confidently. This conflicts with the "no invented content" rule and must be weighed, not just the WER.
- Status: no change to the app. Evidence (tiny, partly self-cleaned references) points to RNNT (V1) as the safest improvement: never worse than CTC on checked lines, no new runtime dependency, +~0.05 s per phrase. CTC+LM has the fewest wrong words on checked call lines but needs either KenLM compiled for Windows (needs MSVC Build Tools) or a much smaller LM, and it introduces plausible wrong words. RNNT + LM is not available with onnx-asr/pyctcdecode.

## 2026-11-01 — App switched to the RNNT branch (owner's decision)
- `AsrConfig` default: `fastconformer-hy-rnnt` / `nemo-conformer-rnnt` (Vana-Labs/stt-fastconformer-armenian @ ec92cef52199c3874681321b13853e8fe602d23f, fp32). Same NVIDIA acoustic model as before; only the decoder changes.
- Why: on the lines a person checked it made fewer errors than CTC (call 13 vs 18 of 54 words, mic 4 vs 6 of 18) and was never worse; +~0.05 s per phrase (slowest clip 0.22 s vs 0.19 s); no new dependency. Evidence is small (see the comparison entry above).
- `--asr ctc` keeps the previous decoder for A/B tests. No automatic fallback: a missing model stops the app with the download command (`download_models.py --asr` / `--asr-ctc`).
- recordings' meta.json now stores the `asr` choice.
- To check after the switch: live latency with both streams (`run --mic 9 --system`), and the synthetic replay (call.wav).


## 2026-11-01 — Smarter phrase cuts: built as opt-in test options, NOT adopted (owner decides)
- Problem: in continuous speech the 5 s cap (`max_len`) cuts mid-word/mid-sentence (15:47 test: 4 of 8 phrases). Raising the cap only moves the cut.
- Built in `armtrans/vad.py` (Segmenter), all **off by default**: the app and the CLAUDE.md phrase rule are unchanged.
  - `--adaptive-pause`: pause needed to end a phrase 600 ms; 250 ms after 3 s; 120 ms after 4 s (`VadConfig.pause_steps`, untuned).
  - `--smart-cut [vad|energy]` + `--smart-cut-window S` (default 1.5): at 5 s, cut at the quietest point of the last S seconds — lowest Silero chunk (`vad`) or quietest 10 ms loudness frame (`energy`) — and carry the audio after it into the next phrase. Nothing dropped; carried audio counts toward the next 5 s. New cut reason `smart_cut`; `Phrase.cut_prob` = VAD probability at a forced cut.
  - Tool: `scripts/cut_report.py <wav> [--smart-cut ...] [--window S]` (VAD only, no ASR).
- Measured 2026-11-01 16:23–16:36, laptop, one recording (`recordings\rec-20261101-154627\mic.wav`, one speaker reading continuously), `python -m armtrans replay --mic-wav <wav> [opts] --hyp-out results\cut_*_1101.jsonl`; word splits judged by Claude from the Armenian text (no reference transcript):

  | Variant | forced cuts | cuts splitting a word | lines < 1.5 s | max latency |
  |---|---|---|---|---|
  | current (`max_len`) | 4 | 2 (քերական\|ությունից, Բաժ\|անորդագրվեք) | 2 | 3.87 s |
  | smart vad 1.5 s | 5 | 1 (դարձ\|նել) | 1 | 4.02 s |
  | smart vad 1.5 s + adaptive pause | identical to the row above (no 250–600 ms pauses in this speech) | | | 4.05 s |
  | smart energy 1.0 s | 4 | 2 (օգտա\|կար, դարձ\|նել) | 1 | 2.58 s |
  | smart energy 1.5 s | 5 | 3 (կոնկր\|ետ, վիդեոդ\|ասերի, դարձ\|նել) | 1 | 2.88 s |
- Findings: the speaker almost never pauses — Silero probability stays 0.4–1.0 between words (`cut_report.py`: only 1 of 4–5 forced cuts fell below 0.35 in any variant). Neither VAD nor loudness reliably finds word gaps here. `vad 1.5` was best on this sample (1 split of 5, and "տեսքով։ | Ուզում եմ…" now breaks at a sentence end) but costs up to +1.26 s VAD wait. Latency spread between runs (max 2.6–4.1 s) is mostly run-to-run machine load, not the variant [uncertain: laptop load was not controlled]. Sample is one recording, 4–5 cuts: not enough to adopt anything.
- Bug found and fixed during the test: energy cut times were numpy floats → timing log JSON failed (`Object of type bool is not JSON serializable`); app stopped loudly as designed. Now plain floats; test added.
- Next option (not built, needs owner OK because ASR would run inside segmentation): **cut at a word boundary from ASR timestamps.** Verified 2026-11-01: onnx-asr 0.12.0 `load_model(...).with_timestamps()` on the RNNT model returns tokens with start times (80 ms steps; a leading space marks a word start), e.g. mic_007: " դարձ" 4.16 s, "նել" 4.48 s, " առանց" 4.80 s. At the cap: recognise the buffer (~0.2 s for 5 s audio, measured earlier), cut before the last word start, carry the rest. Cost: one extra ASR call per forced cut, the carried part is recognised twice.

## 2026-11-01 — ASR word-boundary cut (`--smart-cut asr`): built as opt-in, NOT adopted (no clear win)
- Owner OK'd building it (16:45). Off by default; app and CLAUDE.md phrase rule unchanged.
- How: at the 5 s cap the Segmenter calls `FastConformerASR.word_starts(buffer)` (RNNT `with_timestamps()`, tokens starting with a space = word starts), takes the last word start inside the 1.5 s window, and cuts at the quietest 10 ms within −120…+40 ms of it (cut reason `word_cut`). Audio after the cut is carried into the next phrase. If no word starts in the window, it cuts at the quietest 10 ms instead and logs `smart_cut` (visible, not hidden). The extra call runs in the capture thread; the ASR model now has a lock (worker + capture thread). Timing log gains `cut_prob` and `cut_asr_s` for forced cuts.
- Cost [measured 2026-11-01, laptop]: extra ASR call 0.14–0.17 s per forced cut (`scripts\cut_report.py ... --smart-cut asr`, and timing logs below).
- Text check, laptop 2026-11-01 16:47–16:55, `python -m armtrans replay --mic-wav <mic> [--system-wav <sys>] [--smart-cut asr] --hyp-out results\cut*_*.jsonl`. Splits judged by Claude from the Armenian text (no reference transcripts, so uncertain):

  | Recording | Variant | forced cuts | clear word splits | max latency | call labels |
  |---|---|---|---|---|---|
  | rec-154627 mic (one reader) | current | 4 | 2 (քերական\|ությունից, Բաժ\|անորդագրվեք) | 3.87 s | — |
  | rec-154627 mic | asr | 5 | 1–2 (դարձ\|նել: ASR put a word start inside the word; վախկին\|… unclear) | 2.42 s | — |
  | rec-124830 mic + call | current | 5 | 1 (…չենք \| Բոխում = փոխում) | 2.35 s | 2 |
  | rec-124830 mic + call | asr | 5 (4 word_cut, 1 fallback) | 1 (պատրաստ…\|Բոլոր…, at a speaker change) | 2.64 s | 3 |
- Good cases: "…ոչինչ \| Չենք փոխում…" (old split փոխում), "…բժշկական \| Կենտրոնում սպաս" (old left a 0.3 s "Հ։"), "…սկսենք \| Քերականությունից". Bad: the last word at the buffer edge is cut off, so the ASR sometimes invents a word start inside it.
- Result: across 2 recordings, current 9 cuts / 3 clear splits vs asr 10 cuts / 2–3 splits — a tie within the noise of this tiny, unreferenced sample. Even clean word cuts still split sentences, so the English of each half is still incomplete (e.g. "…ավելի կոնկրետ օժանդակ։ | Բայերից։"). The extra speaker label in the asr run comes from different phrase boundaries feeding the matcher (speaker over-splitting is a known separate problem).
- Not adopted. To decide properly we need reference transcripts for long continuous speech (refs.tsv style) and more recordings.

## 2026-11-01 — Longer phrases while the other side is silent (`max_phrase_solo_s` = 8 s) — ADOPTED as default (owner: "if it doesn't affect the 5 s rule")
- Plain raise of the cap breaks the 5 s rule. Stress test = the same continuous speech (`rec-20261101-154627\mic.wav`) replayed on BOTH streams, so two long phrases end together (laptop 2026-11-01 17:39–17:43, `python -m armtrans replay --mic-wav <w> --system-wav <w> --max-phrase-s N --hyp-out results\stress<N>_154627.jsonl`): cap 5 s → max 4.46 s, 0 over; **cap 8 s → max 6.02 s, 1 over 5 s** (second phrase waits 2.96 s for the first; MT of an 8–10 s line takes 2.5–2.9 s).
- Rule adopted instead (`armtrans/vad.py`, `link_streams`): past 5 s a phrase keeps growing only while every other stream is not in speech, up to 8 s (hard limit). When another stream starts speech, a phrase already past 5 s is cut on its next chunk; if both talk, both cut at 5 s as before. A 600 ms pause still ends any phrase. `--max-phrase-solo-s 5` restores the old behaviour. CLAUDE.md phrase definition updated.
- Why 8 s and not 10: one stream alone at cap 10 s gave max 3.23 s (10.2 s line, MT 2.88 s); with the other side able to start right after a long cut, a longer line leaves less margin. 8 s: single-stream max 2.96–3.05 s.
- Measured with the new default, laptop 2026-11-01 17:45–17:53 (`replay ... --hyp-out results\solo_*.jsonl`, timing logs 174556/174727/174859/175031):

  | Test | lines | longest line | max latency | over 5 s |
  |---|---|---|---|---|
  | stress: same speech on both streams | 16 | 5.22 s (both cut at 5) | 4.43 s (old 4.46) | 0 |
  | your reading alone (rec-154627 mic) | 6 | 8.22 s | 2.96 s | 0 |
  | adversarial: reading + call clips starting while a mic line is past 5 s (`scripts\make_overlap_test.py`, clips at 49.6/56.2/70.3 s) | 10 | 6.62 s | 2.44 s | 0 |
  | real call rec-124830 (mic + call) | 21 (was 24) | 8.22 s | 2.82 s | 0 |
- Translation gets more complete when one person talks long, e.g. "Կկիրառում … դարձնել առանց օժանդակ բայերի։" → "…without auxiliary verbs." as one line (was split "առանց։ | Օժանդակ պահերի։").
- Downsides seen: (1) on the call, two remote people speaking back-to-back with no pause on the same stream (41.8–49.9 s) became ONE 8.2 s line with one label (Speaker 2); with the 5 s cut the turn happened to fall near a cut. Same-stream speaker turns without a pause are not detected by VAD at any cap. (2) The stress margin (4.43 s) is unchanged, i.e. still tight when both sides talk at once. (3) Lines can start up to 8 s after the speech began (the rule measures from the phrase end, so this is allowed, but the reader waits longer for the first words).
- Remaining check: owner's live test (`run --mic 9 --system --record recordings`).

## 2026-11-01 — ASR context after a forced cut (`asr.context_s` = 1.5 s) — ADOPTED as default (owner chose option 1 of the ASR list)
- What: a phrase that continues a forced cut (same stream, no pause between) is recognised together with the last 1.5 s of the previous phrase (`Phrase.context`, set by the Segmenter). `FastConformerASR.transcribe_with_context` uses RNNT token timestamps (`with_timestamps()`) and prints words from the first one that reaches into the new audio: a word is new if its first token starts ≥ join − `context_lead_s` (0) or its last token starts ≥ join + `context_tol_s` (0.08 s). A word cut in half by the previous line is printed whole here. First letter capitalised (the model writes it mid-sentence). `--asr-context-s 0` = off. Phrases after a pause are unchanged. `run --record` also saves the context as `phrases/<src>_<seq>_ctx.wav`.
- Checked first: `''.join(tokens)` equals `recognize()` text on 28/28 recorded phrases (laptop 2026-11-01).
- Offline A/B on the exact saved phrase audio, every phrase that followed a forced cut in all finished recordings (22 phrases: rec-124015, 124830, 154627, 180039, 182801, 183144), laptop 2026-11-01, `python scripts\context_check.py <recs> --lead 0 0.12 0.24` → `results\context_check_lead_1101.txt`. Judged by Claude from the text (no references):
  - First rule (last token only) lost a one-token first word twice ("Շատ շնորհակալ եմ" → "շնորհակալ եմ", "Իմ գիտելիքներով" → "գիտելիքներով"). Fixed by the first-token condition; lead 0.24 s adds duplicated words from the previous line (եմ, եւ, պետք), 0.12 = 0.00 → lead 0.
  - Final rule: **better 11** (e.g. Բախում→փոխում, վայրերից→բայերից, Տեւեք→հետեւեք, Կալիքին→ալիքին, Վանգերեն→անգլերեն, "Հ։"→քերականությունից, "Էակ է այդպես փորձի"→"այդպես փորձենք", բաժանորդագր է→բաժանորդագրվեք), **same 7, mixed 2** (one word worse each: վիդեոդասերի→վդեասերի, վիդեո→վդեո), **unclear 2, worse 0**.
  - ASR time with context 0.06–0.33 s per phrase (plain 0.04–0.28 s).
- 5 s rule with context on, real-time replays (laptop 2026-11-01 18:58–19:04, `replay ... --hyp-out results\ctx_*.jsonl`): rec-183144 mic 6 lines max 2.28 s; rec-124830 mic + call 21 lines max 2.53 s; stress (same speech on both streams) 16 lines max 4.30 s (4.43 s without context); 0 over 5 s.
- Limits: only lines after a forced cut benefit (with the 8 s solo rule these are fewer); words in the middle of a line are unchanged; the previous line keeps its broken last word (the next line repeats it whole).

## 2026-11-01 — NLLB invents content on 1–2 word inputs: short-input guard — ADOPTED as default (owner: review point 6)
- Data: every distinct ASR line of ≤ 2 words the app printed in all recordings/replays (58) + 15 common correct replies as a control. Tool: `python scripts\mt_short_check.py [--variants ...]` (laptop 2026-11-01 20:55–21:05) → `results\mt_short_1101.txt`, `results\mt_short_app_1101.txt`. Judged by Claude (no references; owner may disagree).
- Kinds of failure: ASR fragments that are not words become fluent sentences (`Հ։`/`Ե։`/`Քթեր։` → "What is it?", `Հովհ.` → "How can we do this?"); real short words get added content (`Ոչ։` → "No, I did not.", `Իհարկե։` → "Of course you do.", `Օքեյ։` → "Okay, here we go.", `Բարեւ։` → "How are you?").
- Decoding changes tried and rejected: removing the final `։` (worse: `Մաբանություն` → "What is the Bible's view?"); output length cap = source + 2 (no effect); `length_penalty 0` (mixed: `Այո։` → "I did.").
- Signal that works: NLLB's average log-prob of its best hypothesis. Short lines scoring ≤ −0.95: 11, of which 9 invented / 2 acceptable (`Արջ` "bear", `Զը`). No control reply scores below −0.79.
- Adopted (`armtrans/translate.py`, `MtConfig.short_guard`, `--no-mt-short-guard` = old behaviour):
  1. `SHORT_REPLIES`: 18 common replies (Այո, Ոչ, Չէ, Հա, Լավ, Օքեյ, Իհարկե, Բարեւ (ձեզ), Ողջույն, Ցտեսություն, Հասկացա, Շնորհակալություն / շնորհակալ եմ, Ճիշտ է, Մի րոպե, Վա, Հաջողություն) get fixed English when the whole line is that reply (punctuation/case/և normalised; questions with ՞ or ? excluded).
  2. Otherwise a line of ≤ 2 words with score < −0.9 prints `(unclear)` as its English. The Armenian line is still printed (nothing dropped).
- Result on the 58 + 15 lines (same data the threshold was chosen on → optimistic): 22 of 58 ASR lines changed — 13 → `(unclear)` (10 had invented content; 3 lost something: `Արջ` "It's a bear." was correct, `Զը` "The Z.", `Փրզեն Քնդթինյութենս` "Presence Quintinothens" hinted at "Present Continuous"); 9 → fixed replies (all same or better: "Good luck with that." → "Good luck.", "Okay, here we go." → "Okay."). Lines with invented content ≈ 18 → ≈ 6 (left: `Հ հո`/`Աո`/`Հու` → "Oh, my God.", `Հառա՞` → "Is it cold?", `Եսեի լավն`, `Ելանք` → "Let's get out of here."). Control 15: 11 changed, all to plainer correct English, none worse.
- Cost: none measurable (scores come with the same beam search; the reply table skips the model). Warm-up now translates a full sentence (a reply would skip the model).
- Not fixed: inventions with a confident score ("Oh, my God."); any line > 2 words. The threshold needs re-checking on new recordings.

## 2026-11-01 — Evaluation sets (PLAN §7) built; first FLEURS baseline (owner: review point 8)
- Built by `python scripts\make_eval_sets.py` (laptop 2026-11-01 21:00–21:07; downloads FLEURS once, cache `results\fleurs_cache` ~0.5 GB, deletable):
  - `eval\fleurs_hy\clean` — first 100 distinct sentences of FLEURS hy_am **test** (read speech, CC-BY-4.0; 1219 s audio, 2025 words), English reference = FLEURS en_us text with the same sentence id (140/140 found).
  - `phone` — same audio band-limited (8 kHz, 300–3400 Hz; no codec, so only an approximation of call audio). `noise10` — + white/pink noise at 10 dB SNR (seed 0). `numbers` — 40 other test sentences containing digits.
  - `eval\hard\noise_only` — 18 stretches of real recordings with no logged phrase (≥ 4 s) + 3 synthetic (white, pink, 50 Hz hum). rec-141437 excluded (empty hyp, cause unknown; clip moved to `eval\hard\_to_delete`).
- Speaker hard cases (returning speaker, joining mid-call, finishing together) not built: they need speaker truth (review point 4, skipped by the owner).
- Baseline, app defaults (RNNT, NLLB-1.3B beam 4 + short guard), `python scripts\benchmark.py --clips eval\fleurs_hy\<set> --asr rnnt --show` → `results\fleurs_<set>_1101.txt`, `results\benchmark-20261101-211153/211737/212255/212539.json`:

  | Set | WER | CER | chrF (EN) | words |
  |---|---|---|---|---|
  | clean | **13.6%** | 6.4% | 60.7 | 2025 |
  | phone | 15.3% | 6.8% | 60.0 | 2025 |
  | noise10 | 18.3% | 8.1% | 57.7 | 2025 |
  | numbers | 24.4% | 15.1% | 57.2 | 887 |
- Reading: on read speech the ASR is far better than on our calls (real-call checked lines ~13/54 wrong words ≈ 24%, DECISIONS "ASR comparison"), so the gap is the call conditions (spontaneous speech, room mic, call audio), not Armenian in general. Phone band costs +1.7 WER points, 10 dB noise +4.7. chrF here = ASR+MT end to end vs FLEURS English (not comparable to the 89.0/80.1 on mt_8, which used our own 8 references).
- Numbers: the ASR writes numbers as words (1537 → "հազար հինգ հարյուր երեսունյոթ", correct) while FLEURS writes digits, so the 24.4% overstates errors; real number errors also occur ("150 եւ 200" → "հարյուր հիսուն եւ երկու օր", "403" → "չորսա օր երեքը", "1988" → "հազար ին հարութունութ").
- Speed: FLEURS utterances are 5–29 s, longer than app phrases (≤ 8 s); the slowest clips (13–29 s) took 7–11 s, almost all translation. RTF "MISSES 0.33" lines in these runs come from those long clips and do not apply to the app.
- No-speech check (`python scripts\check_noise_only.py` → `results\noise_only_1101.txt`, app path VAD → ASR → MT): 21 files / 148 s → 18 nothing; 3 printed: `rec-124830_mic_25s` = real call speech heard by the mic ("Կլինիկական սրտաբանություն"; not a false alarm — "no phrase logged" did not mean "no speech"), `rec-115207_mic_12s` "Հեն գրի։" → "Write it down." and `rec-154627_mic_38s` "Հ։" → "(unclear)" (0.6–0.7 s, unknown; owner should listen). All 3 synthetic noises: nothing. So the set is not yet a verified no-speech set.

## 2026-11-01 — Cleanup and pinned requirements (owner: review point 9, "yea")
- Deleted on the laptop (owner's OK, 21:35; ~4.6 GB): `models\whisper-large-v3-turbo`, `whisper-medium`, `whisper-small`, `nllb-600m-ct2`, `opus-mt-hy-en-ct2`, `results\fleurs_cache` (re-download: `scripts\make_eval_sets.py`), `eval\hard\_to_delete`. Results from those models stay in this file (10-31 entries). `scripts\compare_mt.py` now lists only models present and says clearly when one was removed; re-check 2026-11-01 21:41 `compare_mt.py --models nllb-1.3b --beam 4`: chrF 89.0 / 80.1 (same as 10-31).
- Kept, awaiting a separate owner decision (~6.6 GB, test-only, finished experiments): `models\omnilingual-ctc-300m` 1.52 GB, `models\fastconformer-arm-nopc-{ctc,rnnt}` 0.87 GB, `models\lm` 0.67 GB + `results\lm` 1.95 GB, `.venv-export` 1.58 GB. Deleting them disables `benchmark.py --asr omni*/v2*`, `bench_lm.py`, and NeMo re-export (re-creatable).
- `requirements.txt` pinned (`==`) to the laptop versions that pass the tests; `pip install -r requirements.txt --dry-run`: all already satisfied. `requirements-setup.txt`: transformers==5.22.1 (torch 2.15.0+cpu).
