"""CLI.

  python -m armtrans devices
  python -m armtrans run --mic 9 --system            # mic ("Me") + call audio (Speaker 1, 2, ...)
  python -m armtrans run --mic 9                     # mic only
  python -m armtrans run --system 10                 # call audio only, from a chosen loopback device
  python -m armtrans run --mic 9 --system --record recordings   # test only: also save time-aligned WAVs
  python -m armtrans replay --mic-wav me.wav --system-wav them.wav [--hyp-out hyp.jsonl]
"""
import os

os.environ["HF_HUB_OFFLINE"] = "1"          # local only: Hugging Face libraries may not touch the network
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .clock import Clock
from .config import LOGS_DIR, AppConfig
from .devices import DeviceError, list_input_devices, resolve_mic
from .pipeline import Pipeline
from .timing import TimingLog
from .vad import StreamVad, link_streams

DEFAULT_LOOPBACK = -1
# --asr: folder under models/ and onnx-asr type. Same NVIDIA hy model, two decoders (DECISIONS.md 2026-11-01).
ASR_CHOICES = {"rnnt": ("fastconformer-hy-rnnt", "nemo-conformer-rnnt"),
               "ctc": ("fastconformer-hy-ctc", "nemo-conformer-ctc")}


def _utf8_console():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def build_config(args) -> AppConfig:
    cfg = AppConfig()
    cfg.asr.cpu_threads = args.asr_threads
    cfg.asr.context_s = args.asr_context_s
    cfg.asr.model_dir, cfg.asr.kind = ASR_CHOICES[args.asr]
    cfg.mt.intra_threads = args.mt_threads
    cfg.mt.beam_size = args.mt_beam
    cfg.mt.short_guard = not args.no_mt_short_guard
    cfg.vad.min_silence_ms = args.min_silence_ms
    cfg.vad.max_phrase_s = args.max_phrase_s
    cfg.vad.max_phrase_solo_s = max(args.max_phrase_s, args.max_phrase_solo_s)
    cfg.vad.adaptive_pause = args.adaptive_pause
    cfg.vad.smart_cut = bool(args.smart_cut)
    cfg.vad.smart_cut_by = args.smart_cut or "vad"
    cfg.vad.smart_cut_window_s = args.smart_cut_window
    cfg.speakers.match = args.spk_match
    cfg.speakers.min_new_s = args.spk_min_new_s
    return cfg


def load_models(cfg: AppConfig, with_speakers: bool):
    from . import asr as asr_mod, speakers as spk_mod, translate as mt_mod, vad as vad_mod
    t0 = time.perf_counter()
    matcher = None
    try:
        vad_mod.SileroVAD()      # fail early if the VAD model is missing
        branch = "RNNT" if cfg.asr.kind == "nemo-conformer-rnnt" else "CTC"
        print(f"Loading ASR (NVIDIA FastConformer hy, {branch}, ONNX, {cfg.asr.cpu_threads} threads)...")
        asr = asr_mod.FastConformerASR(cfg.asr)
        print(f"Loading translation (NLLB-200 1.3B, int8, beam {cfg.mt.beam_size}, {cfg.mt.intra_threads} threads)...")
        mt = mt_mod.NllbMT(cfg.mt)
        if with_speakers:
            print("Loading speaker model (WeSpeaker ResNet34)...")
            embed = spk_mod.load_embedder(cfg.speakers)
            matcher = spk_mod.SpeakerMatcher(embed, cfg.speakers)
    except (asr_mod.ModelMissing, mt_mod.ModelMissing, vad_mod.ModelMissing, spk_mod.ModelMissing) as e:
        sys.exit(f"Error: {e}")
    print("Warming up models...")
    asr.warm_up()
    mt.warm_up()
    if matcher is not None:
        import numpy as np
        matcher.embed(np.zeros(16000, np.float32))     # warm-up only; does not create a speaker
    print(f"Models ready in {time.perf_counter() - t0:.1f}s")
    return asr, mt, matcher


def new_timing(cfg: AppConfig) -> TimingLog:
    LOGS_DIR.mkdir(exist_ok=True)
    path = LOGS_DIR / f"timing-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    return TimingLog(path, cfg.latency_limit_s)


def hyp_line(p, t0: float, clip: str = None) -> str:
    """One JSONL line for scripts/score_speakers.py; times are seconds from the start of the WAV files.
    clip: file (relative to the recording folder) holding the exact audio the models got for this phrase."""
    d = p.speaker
    row = {"source": p.source, "label": p.label, "start_s": round(p.start_t - t0, 3),
           "end_s": round(p.end_t - t0, 3), "hy": p.hy_text, "en": p.en_text,
           "spk_reason": d.reason if d else None,
           "spk_sim": round(d.similarity, 3) if d and d.similarity is not None else None}
    if clip:
        row["clip"] = clip
    return json.dumps(row, ensure_ascii=False) + "\n"


def cmd_devices(_args):
    try:
        devs = list_input_devices()
    except DeviceError as e:
        sys.exit(f"Error: {e}")
    print("Microphones (use the index with --mic):")
    for i, name, api, ch, sr in devs:
        print(f"  [{i:3d}] {name}  ({api}, {ch} ch, {sr:.0f} Hz)")
    print("\nSystem-audio loopback devices (use with --system; plain --system = the default one):")
    try:
        from .capture import list_loopback_devices
        for i, name, ch, sr, default in list_loopback_devices():
            print(f"  [{i:3d}] {name}  ({ch} ch, {sr:.0f} Hz){'  <- default' if default else ''}")
    except ImportError:
        print("  PyAudioWPatch is not installed: pip install -r requirements.txt")
    print("\nNote: mic and loopback indexes come from two different audio libraries; use each list for its own option.")


def cmd_run(args):
    from .capture import ChunkRecorder, MicCapture, SystemCapture
    if args.mic is None and args.system is None:
        cmd_devices(args)
        sys.exit("\nError: choose --mic <index> and/or --system [index]. There is no default.")
    dev = None
    if args.mic is not None:
        try:
            dev = resolve_mic(args.mic)
        except DeviceError as e:
            sys.exit(f"Error: {e}")
    cfg = build_config(args)
    rec_dir = None
    if args.record:
        rec_dir = Path(args.record) / f"rec-{datetime.now():%Y%m%d-%H%M%S}"
        try:
            rec_dir.mkdir(parents=True, exist_ok=False)
            (rec_dir / "phrases").mkdir()
        except OSError as e:
            sys.exit(f"Error creating recording folder {rec_dir}: {e}")
    asr, mt, matcher = load_models(cfg, with_speakers=args.system is not None)
    clock = Clock()
    timing = new_timing(cfg)
    recorders = {}
    hyp_f = open(rec_dir / "hyp.jsonl", "w", encoding="utf-8") if rec_dir else None
    t0 = clock.now()        # shared start of all recorded WAV files (set before any stream starts)

    def on_done(p):
        if hyp_f:
            clip = None
            if p.hy_text:       # exact model input, so ASR tests on recordings match live runs
                import soundfile as sf
                clip = f"phrases/{p.source}_{p.seq:03d}.wav"
                sf.write(rec_dir / clip, p.audio, 16000, subtype="FLOAT")
                if p.context is not None:   # the ASR also heard this (asr.context_s)
                    sf.write(rec_dir / clip.replace(".wav", "_ctx.wav"), p.context, 16000, subtype="FLOAT")
            hyp_f.write(hyp_line(p, t0, clip))
            hyp_f.flush()

    pipe = Pipeline(asr, mt, timing, clock, on_done=on_done, speakers=matcher)
    ws = asr.word_starts if cfg.vad.smart_cut and cfg.vad.smart_cut_by == "asr" else None

    def sink_for(name, vad):
        if rec_dir is None:
            return vad
        recorders[name] = ChunkRecorder(vad, rec_dir / f"{name}.wav", t0)
        return recorders[name]

    sources, vads = [], []
    if dev is not None:
        mic_vad = StreamVad("mic", cfg.vad, pipe.submit, timing.discard, now=clock.now, word_starts=ws,
                            context_s=cfg.asr.context_s)
        vads.append(mic_vad)
        sources.append(("mic", MicCapture(dev, sink_for("mic", mic_vad))))
    if args.system is not None:
        sys_vad = StreamVad("system", cfg.vad, pipe.submit, timing.discard, now=clock.now, word_starts=ws,
                            context_s=cfg.asr.context_s)
        vads.append(sys_vad)
        try:
            cap = SystemCapture(sink_for("system", sys_vad), None if args.system == DEFAULT_LOOPBACK else args.system)
        except (ValueError, OSError, ImportError) as e:
            sys.exit(f"Error opening system audio: {e}")
        sources.append(("system", cap))
    link_streams(vads)
    pipe.start()
    for _, s in sources:
        s.start()
    for name, s in sources:
        d = s.device
        print(f"Listening on {name}: [{d['index']}] {d['name']}")
    if rec_dir:
        print(f"RECORDING (test only) to {rec_dir} - record only calls where everyone agreed.")
    print("Ctrl+C to stop.\n")
    rec_error = None
    try:
        while pipe.error is None and rec_error is None:
            time.sleep(0.2)
            rec_error = next(((n, r.error) for n, r in recorders.items() if r.error), None)
    except KeyboardInterrupt:
        pass
    for _, s in sources:
        s.stop()
    pipe.drain_and_stop()
    timing.close()
    if rec_error:
        print(f"\n[ERROR] recording of {rec_error[0]} failed, app stopped: "
              f"{type(rec_error[1]).__name__}: {rec_error[1]}")
    if rec_dir:
        _finish_recording(rec_dir, recorders, hyp_f, args, sources, timing)
    print("\n" + timing.summary())
    for name, s in sources:
        if s.overflows:
            print(f"  WARNING: {name} buffer overflowed {s.overflows} times (audio was lost).")
        if getattr(s, "gap_fills", 0):
            print(f"  note: {name} sent no audio for a while {s.gap_fills} times; filled with silence.")
    print(f"  Timing log: {timing.path}")
    if pipe.error or rec_error:
        sys.exit(1)


def _finish_recording(rec_dir, recorders, hyp_f, args, sources, timing):
    for r in recorders.values():
        r.close()
    hyp_f.close()
    meta = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "devices": {name: {"index": s.device["index"], "name": s.device["name"]} for name, s in sources},
        "settings": {"asr": args.asr, "spk_match": args.spk_match, "spk_min_new_s": args.spk_min_new_s,
                     "min_silence_ms": args.min_silence_ms, "max_phrase_s": args.max_phrase_s,
                     "max_phrase_solo_s": args.max_phrase_solo_s,
                     "asr_context_s": args.asr_context_s, "mt_short_guard": not args.no_mt_short_guard,
                     "adaptive_pause": args.adaptive_pause, "smart_cut": args.smart_cut,
                     "smart_cut_window": args.smart_cut_window},
        "timing_log": str(timing.path),
        "files": {name: {"file": f"{name}.wav", "duration_s": round(r.duration_s, 2),
                         "padded_s": round(r.padded_s, 3), "max_lead_s": round(r.max_lead_s, 3),
                         "max_lag_s": round(r.max_lag_s, 3),
                         "error": f"{type(r.error).__name__}: {r.error}" if r.error else None}
                  for name, r in recorders.items()},
    }
    (rec_dir / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nRecording saved: {rec_dir}")
    for name, r in recorders.items():
        if r.samples == 0:
            print(f"  {name}.wav  EMPTY - no audio arrived from this device (nothing played / device silent)")
            continue
        print(f"  {name}.wav  {r.duration_s:.1f}s  (silence added {r.padded_s:.2f}s, "
              f"max lead {r.max_lead_s * 1000:.0f} ms, max lag {r.max_lag_s * 1000:.0f} ms)")
    wavs = " ".join(f"--{n}-wav {rec_dir / (n + '.wav')}" for n in recorders)
    print(f"  Replay: python -m armtrans replay {wavs} --hyp-out {rec_dir / 'hyp_replay.jsonl'}")
    print(f"  Truth draft: python scripts\\make_truth_draft.py {rec_dir}")


def cmd_replay(args):
    from .capture import ReplaySource
    cfg = build_config(args)
    asr, mt, matcher = load_models(cfg, with_speakers=bool(args.system_wav) and not args.no_speakers)
    clock = Clock()
    timing = new_timing(cfg)
    hyp_f = open(args.hyp_out, "w", encoding="utf-8") if args.hyp_out else None
    sources = {}

    def on_done(p):
        if hyp_f:
            hyp_f.write(hyp_line(p, sources[p.source].started_at))
            hyp_f.flush()

    pipe = Pipeline(asr, mt, timing, clock, on_done=on_done, speakers=matcher)
    ws = asr.word_starts if cfg.vad.smart_cut and cfg.vad.smart_cut_by == "asr" else None
    vads = []
    for name, path in (("mic", args.mic_wav), ("system", args.system_wav)):
        if path:
            vad = StreamVad(name, cfg.vad, pipe.submit, timing.discard, now=clock.now, word_starts=ws,
                            context_s=cfg.asr.context_s)
            sources[name] = ReplaySource(path, vad, realtime=True)
            vads.append(vad)
    if not sources:
        sys.exit("Error: give --mic-wav and/or --system-wav.")
    link_streams(vads)
    pipe.start()
    threads = [threading.Thread(target=s.run, daemon=True) for s in sources.values()]
    print("Replaying in real time: " + ", ".join(f"{k} ({s.duration_s:.1f}s)" for k, s in sources.items()) + "\n")
    for t in threads:
        t.start()
    for t in threads:
        while t.is_alive() and pipe.error is None:
            t.join(timeout=0.2)
    pipe.drain_and_stop()
    timing.close()
    if hyp_f:
        hyp_f.close()
    print("\n" + timing.summary())
    print(f"  Timing log: {timing.path}")
    if pipe.error:
        sys.exit(1)


def main():
    _utf8_console()
    ap = argparse.ArgumentParser(prog="armtrans", description="Live Eastern Armenian -> English (local, prototype)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("devices", help="list microphones and system-audio loopback devices")

    def common(p):
        p.add_argument("--asr", choices=sorted(ASR_CHOICES), default="rnnt",
                       help="ASR decoder of the NVIDIA hy model: rnnt (default since 2026-11-01) or ctc (previous)")
        p.add_argument("--asr-threads", type=int, default=4)
        p.add_argument("--asr-context-s", type=float, default=1.5,
                       help="after a forced cut, recognise with this much of the previous audio (0 = off)")
        p.add_argument("--mt-threads", type=int, default=4)
        p.add_argument("--mt-beam", type=int, default=4)
        p.add_argument("--no-mt-short-guard", action="store_true",
                       help="translate 1-2 word lines as before (no fixed replies, no '(unclear)')")
        p.add_argument("--min-silence-ms", type=int, default=600)
        p.add_argument("--max-phrase-s", type=float, default=5.0)
        p.add_argument("--max-phrase-solo-s", type=float, default=8.0,
                       help="a phrase may grow up to this while the other stream is silent (5 = old behaviour)")
        p.add_argument("--adaptive-pause", action="store_true",
                       help="test: shorter pause ends a long phrase (600 ms; 250 ms after 3 s; 120 ms after 4 s)")
        p.add_argument("--smart-cut", nargs="?", const="vad", choices=["vad", "energy", "asr"],
                       help="test: at the max length, cut at the quietest point of the last 1.5 s and carry the rest "
                            "(vad = lowest speech probability, energy = quietest 10 ms, asr = before the last word start)")
        p.add_argument("--smart-cut-window", type=float, default=1.5, help="test: smart-cut search window, s")
        p.add_argument("--spk-match", type=float, default=0.5, help="similarity for a long phrase to join a speaker")
        p.add_argument("--spk-min-new-s", type=float, default=1.5, help="min phrase length to create/update a speaker")

    r = sub.add_parser("run", help="live: mic and/or system audio")
    r.add_argument("--mic", type=int, help="microphone index from `devices`")
    r.add_argument("--system", type=int, nargs="?", const=DEFAULT_LOOPBACK,
                   help="capture call audio with speaker labels; optional loopback index (default device if omitted)")
    r.add_argument("--record", metavar="DIR",
                   help="test only: also save mic.wav/system.wav (16 kHz, time-aligned), each phrase's exact audio "
                        "(phrases/), hyp.jsonl and meta.json "
                        "in a new folder under DIR, for replay and speaker scoring. Needs everyone's consent.")
    common(r)
    rp = sub.add_parser("replay", help="test only: replay WAV files in real time")
    rp.add_argument("--mic-wav")
    rp.add_argument("--system-wav")
    rp.add_argument("--no-speakers", action="store_true", help="label system audio 'Speaker ?' instead of matching")
    rp.add_argument("--hyp-out", help="write recognised/translated text and labels as JSONL for scoring")
    common(rp)
    args = ap.parse_args()
    {"devices": cmd_devices, "run": cmd_run, "replay": cmd_replay}[args.cmd](args)


if __name__ == "__main__":
    main()
