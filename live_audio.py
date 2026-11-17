"""Opt-in Windows speaker-output capture; never opens a microphone.

A capture returns only an in-memory WAV; callers must not persist it. This
captures the default output mix, potentially including notification sounds.
Capturing audio from a service may be restricted by that service's terms.
"""
from __future__ import annotations

import io
import sys
import wave


class CaptureError(RuntimeError):
    pass


def capture_output(seconds: int = 20) -> bytes:
    if sys.platform != "win32":
        raise CaptureError("Listen Live currently requires Windows with WASAPI.")
    if not 5 <= seconds <= 30:
        raise CaptureError("Capture duration must be between 5 and 30 seconds.")
    try:
        import pyaudiowpatch as pa
    except ImportError as exc:
        raise CaptureError("Windows audio capture package is missing; reinstall the app dependencies.") from exc
    import numpy as np
    pieces = []
    try:
        with pa.PyAudio() as driver:
            device = driver.get_default_wasapi_loopback()
            rate = int(device["defaultSampleRate"])
            channels = max(1, min(2, int(device["maxInputChannels"])))
            chunk_frames = 2048
            with driver.open(format=pa.paInt16, channels=channels, rate=rate,
                             input=True, input_device_index=device["index"],
                             frames_per_buffer=chunk_frames) as stream:
                remaining = int(seconds * rate)
                while remaining:
                    count = min(chunk_frames, remaining)
                    pieces.append(stream.read(count, exception_on_overflow=False))
                    remaining -= count
        raw = b"".join(pieces)
        if not raw or np.max(np.abs(np.frombuffer(raw, dtype=np.int16).astype(np.int32))) < 50:
            raise CaptureError("No audible output was captured. Play the song before clicking Listen Live.")
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(raw)
        return output.getvalue()
    except CaptureError:
        raise
    except (OSError, LookupError) as exc:
        raise CaptureError("Could not open Windows speaker loopback. Check your active output device.") from exc
