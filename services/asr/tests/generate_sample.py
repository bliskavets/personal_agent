"""Standalone script to regenerate tests/sample.wav (16 kHz mono, 2 s sine wave)."""

import struct
import math
import os

SAMPLE_RATE = 16000
DURATION_S = 2
FREQUENCY = 440   # Hz (concert A)
AMPLITUDE = 0.5


def write_wav(path: str, samples: list, sample_rate: int) -> None:
    """Write a minimal PCM WAV file from a list of int16 sample values."""
    n_samples = len(samples)
    data_size = n_samples * 2  # int16 -> 2 bytes each
    with open(path, "wb") as f:
        # RIFF header
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_size))
        f.write(b"WAVE")
        # fmt sub-chunk
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))
        f.write(struct.pack("<H", 1))           # PCM
        f.write(struct.pack("<H", 1))           # mono
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", sample_rate * 2))  # byte rate
        f.write(struct.pack("<H", 2))           # block align
        f.write(struct.pack("<H", 16))          # bits per sample
        # data sub-chunk
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        for s in samples:
            f.write(struct.pack("<h", s))


if __name__ == "__main__":
    n = SAMPLE_RATE * DURATION_S
    samples = [
        int(AMPLITUDE * 32767 * math.sin(2 * math.pi * FREQUENCY * i / SAMPLE_RATE))
        for i in range(n)
    ]
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample.wav")
    write_wav(out, samples, SAMPLE_RATE)
    print(f"Written {out}  ({n} samples, {DURATION_S}s @ {SAMPLE_RATE} Hz)")
