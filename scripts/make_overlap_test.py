"""Test-only: build a system-audio WAV with call clips placed at chosen times, to replay next to a mic WAV.

    python scripts/make_overlap_test.py <mic.wav> <out.wav> <clip.wav>@<seconds> ...

Used to check the 5 s rule when the other side starts talking while a mic phrase is longer than 5 s.
"""
import sys

import numpy as np
import soundfile as sf


def main():
    mic, out, *specs = sys.argv[1:]
    n = sf.info(mic).frames
    sr = sf.info(mic).samplerate
    audio = np.zeros(n, np.float32)
    for spec in specs:
        path, at = spec.rsplit("@", 1)
        clip, csr = sf.read(path, dtype="float32")
        assert csr == sr, f"{path}: {csr} Hz, mic {sr} Hz"
        i = int(float(at) * sr)
        audio[i:i + len(clip)] += clip[:max(0, n - i)]
        print(f"{path} ({len(clip) / sr:.2f} s) at {float(at):.2f} s")
    sf.write(out, audio, sr)


if __name__ == "__main__":
    main()
