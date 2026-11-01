# PLAN.md — Live Armenian → English call translator

Status (2026-11-01): build steps 1–4 done and running on the laptop (mic + system audio, FastConformer RNNT ASR, NLLB-1.3B, speaker labels); step 5 in progress; step 6 not started. All live/replay runs so far: 0 lines over 5 s. Open: speaker over-splitting on real calls, Armenian ASR accuracy, NLLB invents words on 1-word inputs, real-call truth labels/references, the §8 tests (local-only, 60 min, battery, headphone mode, device path). Details: CONTEXT_HANDOFF.md, DECISIONS.md, TESTS.md.
**[Confirmed]** = stated by the owner. **[Proposal]** = suggestion, not yet agreed. **[To test]** = must be measured or verified before we rely on it.

## 1. Goal

A Python terminal app that runs locally on a Windows laptop. It listens to the microphone and system audio at the same time, transcribes Eastern Armenian, and translates it into English. One line per phrase:

```
[14:03:21] Speaker 2 | Բարև, ինչպե՞ս ես։ | Hi, how are you?
```

## 2. Confirmed facts

- Python terminal app. All inference runs on the laptop. No cloud inference.
- Laptop: MSI Modern 15 A11MU, i7-1195G7 (4 cores / 8 threads), Iris Xe iGPU, no NVIDIA GPU, 40 GB RAM, Windows 11, Python 3.12.4.
- Mic and system audio are captured at the same time. The user chooses both devices.
- Mic speech is labelled `Me`. System-audio speakers get `Speaker 1`, `Speaker 2`, …, stable for the whole session.
- Eastern Armenian in, English out. Typical call: 2–4 remote speakers, little overlap.
- Each line has time, speaker, Armenian, English.
- English appears **≤ 5 s after the phrase ends**, including speech detection and queue delays.
- Calls use headphones or laptop speakers. Echo stays out of scope (decision A): in speaker mode remote speech may also appear as `Me`. That rate is measured and reported. Pass/fail targets apply to headphone mode.

**Out of scope:** GUI, packaging, echo handling, speaker names, transcript saving, other input languages, cloud inference, hidden fallbacks. Test recordings and timing logs are dev tools only.

## 3. Definitions [Confirmed — values still tuned by measurement]

- **Phrase:** speech followed by a pause the speech detector accepts as an end (start with ~0.6 s), or cut at a max length (start with ~6 s; 5 s since 2026-10-31; since 2026-11-01 up to 8 s while the other stream is silent — CLAUDE.md).
- **Phrase end:** the moment the last speech sound of the phrase reaches the laptop's audio device: mic input, or system output for remote audio. Network delay before that is not ours to count.
- **5 s limit:** from phrase end to the complete line printed. It applies to **every** phrase, including the first one after startup.
- **Time field:** local clock time when the phrase started (HH:MM:SS).
- **Stable label:** a printed label is never changed. Speakers are never merged or relabelled afterwards.
- **Operating assumption:** during a session, only the call plays through system audio. Other sounds will be transcribed and may create new speakers.

## 4. Modules [Proposal]

| Module | Input | Output |
|---|---|---|
| `devices` | Windows device list | Chosen mic + loopback device. Wrong choice gives a clear error, never a silent default. |
| `capture` | Two devices | 16 kHz mono frames per stream, each stamped with one shared clock (`time.perf_counter`) when it arrives |
| `vad` | Frames per stream | Phrases: audio, start, end, source (`mic`/`system`) |
| `speakers` | System phrases | Phrase + `Speaker N` (mic phrases skip this and get `Me`) |
| `asr` | Phrase audio | Armenian text |
| `translate` | Armenian text | English text |
| `output` | Finished phrase | One printed line |
| `timing` | Stage timestamps | Per-phrase log. Phrases over 5 s are counted and shown in the log, never dropped or cut short to hide it. |

- **Startup:** load and warm up every model before printing "Listening". The first real phrase must not pay the loading cost.
- **Workers:** one shared queue, oldest phrase first. Whether one or two ASR workers is faster on 4 cores is **[To test]** (only one worker is built; not measured yet).
- **Replay input (test only):** feed recorded mic and system WAV files at real-time speed through the same pipeline. This makes accuracy and latency tests repeatable without a live call.

## 5. Five-second budget

Worst case is not one phrase alone. It is a phrase that has to wait for another phrase already being processed: me and a remote speaker finishing together, or a duplicate in speaker mode. So:

`pause wait + processing of the phrase ahead + own processing ≤ 5 s`

With the proposed 0.6 s pause wait and 0.4 s margin, that leaves **≈ 2.0 s to process one max-length (6 s) phrase**: speaker embedding + ASR + translation. In other words, the whole chain must run at about **0.33× real time or faster** on this laptop, while the call app is also running.

- Long-run condition: total processing time per second of speech, across both streams, must stay well under 1. Otherwise the queue grows without limit.
- Speaker mode roughly doubles the load when remote people talk.
- These are targets derived from the proposed values. They are not measurements. Step 1 measures the real numbers.

**Measured (laptop, 2026-11-01; DECISIONS.md / TESTS.md L16):** phrase caps are now 5 s while the other stream talks and 8 s while it is silent (CLAUDE.md).
- Processing (ASR + speaker + translation): 5 s phrase ≈ 1.3–2.2 s, 8 s phrase ≈ 1.9–3.0 s; translation is most of it.
- Worst case (same speech on both streams, both cut at 5 s): max 4.30–4.46 s. A plain 8 s cap failed this test (6.02 s), hence the "solo" rule.
- Live call in speaker mode (2026-11-01 19:29, 58 lines): max 4.52 s, 0 over 5 s; load 0.43 s of processing per second of audio (above the 0.33× target, still within the limit). Speaker mode is the tight case.

## 6. Candidate models and libraries [To test]

Everything here must be verified: that it exists, its licence, Armenian support, a Windows + Python 3.12 install, and speed on this laptop. After the first download, every model must load from local files.

| Part | First to try | Alternatives | Key checks |
|---|---|---|---|
| Capture | `PyAudioWPatch` (WASAPI loopback) | `soundcard` | Mic + loopback open together. **Loopback may deliver no data while nothing plays**, so timestamps must not come from counting samples. Loopback is often 48 kHz stereo and needs resampling. Device buffer delay is measured. |
| Speech detection | Silero VAD | `webrtcvad` | Missed or late phrase ends on call audio; CPU per frame |
| ASR | NVIDIA FastConformer-Hybrid Large (hy), RNNT branch since 2026-11-01 (was CTC), ONNX via `onnx-asr` (Whisper removed 2026-10-31: wrong Armenian and slow on the laptop, see DECISIONS.md) | Community FastConformer without punctuation (needs NeMo or own ONNX export) | WER, real-time factor vs. the 0.33× target, behaviour on noise and short replies |
| Speakers | WeSpeaker ResNet34-LM (ONNX via onnx-asr) + simple online matching — built 2026-10-31 | `diart`, only if the simple version fails | Accuracy on short phrases, CPU time, labels over 30+ min on real calls. Status 2026-11-01: over-splits on real calls (more labels than people); short replies get weak matches; no truth labels yet |
| Translation | NLLB-200 1.3B (CTranslate2 int8) — chosen 2026-10-31 over OPUS-MT and NLLB-600M (DECISIONS.md) | NLLB-600M if latency margin is needed | chrF, my ratings, time per sentence, invented words on short inputs. Status 2026-11-01: chrF 89.0/80.1 on 8 sentences; 1–2 word lines: fixed replies + `(unclear)` on low score since 2026-11-01 (invented lines ≈ 18 → 6 of 58); owner ratings not done |

Dropped for now: full pyannote pipeline, SeamlessM4T, MADLAD-3B, and local LLMs. They are unlikely to fit 2 s of processing on 4 cores. Revisit only if a measurement shows a gap these could fill.

**Speaker matching rule [Confirmed]:**
- Each system phrase is compared with the known speakers.
- Only phrases of at least ~1.5 s (to tune) may create a new speaker or update a known speaker's voice profile.
- A shorter phrase gets its closest known speaker (changed 2026-10-31 at the owner's request: no `Speaker ?`; see DECISIONS.md).
- Mic phrases are `Me`; `Me` is learned from mic speech while the call is silent. A mic phrase heard while call speech plays that does not match `Me` and is closer to a call speaker gets that speaker's label.

## 7. Evaluation set (built in step 1)

1. **FLEURS `hy_am`** — read speech with English references. Use it for a first ASR/translation comparison. It is not call-like, so it is not enough on its own. **Built 2026-11-01** (`eval/fleurs_hy`, 100 test sentences + phone/noise/numbers variants): clean WER 13.6%, chrF 60.7.
2. **Own recordings** — start with ~10 min and grow to ~30 min. Real Eastern Armenian calls with 2–4 remote speakers and me, captured through the same mic + loopback path, with consent. Per phrase: start/end, speaker, reference Armenian. Add reference English for a subset.
3. **Hard cases** — short replies, numbers and names, loanwords, noise, silence, a speaker returning after a long gap, a speaker joining mid-call, me and a remote speaker finishing together. **Partly built 2026-11-01:** numbers, phone band, noise, no-speech (`eval/hard`); speaker cases need truth labels.

## 8. Tests (details and results live in TESTS.md)

| Requirement | Measure | Target |
|---|---|---|
| Local only | Full session with Wi-Fi off and Hugging Face offline mode on; check that no network connections open | Works, zero connections |
| Both sources | Mic and loopback run together for 60 min; dropouts, clock drift, behaviour when loopback is silent | No lost phrases |
| Armenian accuracy | WER / CER with fixed normalisation | Baseline first, then improve |
| Understandable English | chrF + my 1–3 rating (1 = misleading) | Set after baseline; **0 lines with invented content** |
| Stable speakers | % correct labels after mapping; spurious new speakers; label swaps | Set after baseline; 0 swaps |
| `Me` label | Headphone mode | 100% |
| 5 s limit (replay) | Two-stream replay at real-time speed with a call app open: median, p95, max, % over 5 s | 100% ≤ 5 s |
| 5 s limit (device path) | Script plays annotated clips through system output and logs when speech ends; app logs print time | 100% ≤ 5 s |
| 5 s limit (real call) | Real call, plugged in and on battery, 60 min; annotate phrase ends afterwards | 100% ≤ 5 s |
| Speaker mode | Duplicate rate and latency | Report only |

## 9. Build order

1. **Benchmark + small eval set.** Measure ASR / translation / embedding speed against the 0.33× target. Record results in DECISIONS.md.
2. **Smallest end-to-end:** mic → VAD → ASR → translation → printed `Me` line, with timing log and replay input from day one.
3. **Add loopback** as the second stream, with a temporary `Speaker ?` label. Re-check the 5 s limit with both streams.
4. **Add speaker matching.**
5. **Run all tests, fix the weakest result only, re-run.** Repeat.
6. Device-path test, real-call test, 60-min run.

## 10. How we work

- One module per focused chat. Bring PLAN.md, CLAUDE.md, and that module's TESTS.md section.
- No speed or accuracy claim without a measurement. Verify model and API claims by running them or from official docs.
- `TESTS.md`: checks, how to run, targets, latest results with dates. `DECISIONS.md`: choices, options, numbers, reasons. `CLAUDE.md`: stable rules (local only, no hidden fallbacks, no invented results, scope, output format, the 5 s definition).

## 11. Main risks

- 4-core CPU may not reach the 0.33× real-time target with a usable Armenian model. This is tested first.
- Laptop slowdown under long load or on battery.
- Short phrases → weak speaker matching.
- Speaker mode duplicates add load and can push other phrases past 5 s.
