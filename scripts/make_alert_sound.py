"""Generate the FloodSafe alert tone.

    python scripts/make_alert_sound.py

Writes frontend/public/sounds/floodsafe-alert.wav

The tone is SYNTHESISED here from plain sine waves rather than taken from a
recording, so it is original work with no licensing question attached - which is
the whole reason this script exists instead of a downloaded siren file.

WAV rather than MP3 because every browser decodes it and it needs no encoder;
at ~1.4 s mono the file is small enough that the size saving would not matter.

Design: three rising tones (A5, C#6, F#6) then a longer final tone. Rising
pitch reads as escalation, and the pattern is short enough to play under a
notification without becoming annoying on repeat.
"""
from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend" / "public" / "sounds" / "floodsafe-alert.wav"

RATE = 44_100
AMPLITUDE = 0.42  # headroom, so the tone never clips on a phone speaker

# (frequency Hz, duration s, gap-after s)
SEQUENCE = [
    (880.00, 0.16, 0.07),   # A5
    (1108.73, 0.16, 0.07),  # C#6
    (1479.98, 0.16, 0.12),  # F#6
    (1108.73, 0.55, 0.00),  # settle on a longer tone
]


def tone(freq: float, seconds: float) -> list[float]:
    """One sine tone with short fades, so it starts and stops without a click."""
    total = int(RATE * seconds)
    fade = max(1, int(RATE * 0.012))
    out = []
    for i in range(total):
        envelope = 1.0
        if i < fade:
            envelope = i / fade
        elif i > total - fade:
            envelope = max(0.0, (total - i) / fade)
        # A touch of second harmonic gives the tone some body on small speakers.
        t = i / RATE
        sample = math.sin(2 * math.pi * freq * t) + 0.18 * math.sin(4 * math.pi * freq * t)
        out.append(AMPLITUDE * envelope * sample / 1.18)
    return out


def main() -> int:
    samples: list[float] = []
    for freq, duration, gap in SEQUENCE:
        samples.extend(tone(freq, duration))
        samples.extend([0.0] * int(RATE * gap))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(
            b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples)
        )

    seconds = len(samples) / RATE
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1024:.0f} KB, {seconds:.2f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
