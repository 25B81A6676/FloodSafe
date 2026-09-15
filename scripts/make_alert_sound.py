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

It also writes frontend/public/sounds/floodsafe-extreme-alert.wav, the
EXTREME tone: about 3 s of a rapidly alternating two-tone warble followed by a
rising sweep, with odd harmonics for a harsher, more penetrating timbre.

The frequencies are chosen to be DIFFERENT from official public-warning
signals. 853 Hz and 960 Hz - the pair used by broadcast and wireless emergency
alert systems - are deliberately avoided, so this tone cannot be mistaken for,
or impersonate, a real government alert. It is FloodSafe's own sound.
"""
from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend" / "public" / "sounds" / "floodsafe-alert.wav"
EXTREME_OUT = ROOT / "frontend" / "public" / "sounds" / "floodsafe-extreme-alert.wav"
# The EXTREME tone is mostly below 4 kHz, so 22.05 kHz halves the file size
# with no audible loss on a phone speaker.
EXTREME_RATE = 22_050

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


def extreme_samples() -> list[float]:
    """Alternating 1175/740 Hz warble (0.19 s each), then a rising sweep."""
    rate = EXTREME_RATE
    out: list[float] = []
    phase = 0.0

    def emit(freq_start: float, freq_end: float, seconds: float, fade: float = 0.006) -> None:
        nonlocal phase
        total = int(rate * seconds)
        fade_n = max(1, int(rate * fade))
        for i in range(total):
            frac = i / max(1, total - 1)
            freq = freq_start + (freq_end - freq_start) * frac
            # Continuous phase, so tone changes click-free without a gap.
            phase += 2 * math.pi * freq / rate
            env = min(1.0, i / fade_n, (total - i) / fade_n)
            # Odd harmonics give a square-ish, siren-like edge that cuts through
            # room noise better than a pure sine at the same peak level.
            raw = math.sin(phase) + 0.33 * math.sin(3 * phase) + 0.18 * math.sin(5 * phase)
            out.append(env * raw)

    for _ in range(6):                      # ~2.3 s alternating two-tone
        emit(1175.0, 1175.0, 0.19)
        emit(740.0, 740.0, 0.19)
    emit(700.0, 1480.0, 0.55)               # rising sweep
    emit(1480.0, 1480.0, 0.18)

    peak = max(abs(v) for v in out) or 1.0
    # Normalise close to full scale, then soft-limit so nothing hard-clips.
    return [math.tanh(1.6 * (v / peak)) / math.tanh(1.6) * 0.97 for v in out]


def write_wav(path: Path, samples: list[float], rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(
            b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples)
        )
    print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1024:.0f} KB, "
          f"{len(samples) / rate:.2f}s)")


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

    write_wav(EXTREME_OUT, extreme_samples(), EXTREME_RATE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
