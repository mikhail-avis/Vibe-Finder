"""Opt-in Windows WASAPI playback capture, with selectable output devices.

Only output *loopback* devices are enumerated; microphones are never opened.
Audio is held in memory only. Other system sounds may be captured alongside music.
"""
from __future__ import annotations

import io
import sys
import time
import wave


class CaptureError(RuntimeError):
    """An unavailable playback device or failed audio capture."""


def _load_pyaudio():
    if sys.platform != "win32":
        raise CaptureError("Listen Live requires Windows and WASAPI playback devices.")
    try:
        import pyaudiowpatch as pa
    except ImportError as exc:
        raise CaptureError("PyAudioWPatch is missing. Restart using run-windows.bat to install dependencies.") from exc
    return pa


def _default_loopback_index(driver, pa):
    """Return the WASAPI default output's *loopback* index, or None."""
    try:
        info = driver.get_default_wasapi_loopback()
        return int(info["index"])
    except (OSError, LookupError, KeyError, ValueError):
        return None


def _loopback_devices(driver, pa):
    default_index = _default_loopback_index(driver, pa)
    outputs = []
    for item in driver.get_loopback_device_info_generator():
        # PaWPatch marks the virtual recording analogue of an output device.
        if not item.get("isLoopbackDevice", False):
            continue
        try:
            index = int(item["index"])
            channels = int(item["maxInputChannels"])
            rate = int(item["defaultSampleRate"])
        except (ValueError, TypeError, KeyError):
            continue
        if channels < 1 or rate < 8000:
            continue
        outputs.append({
            "index": index,
            "name": str(item.get("name", f"Output {index}")),
            "channels": channels,
            "sample_rate": rate,
            "is_default": index == default_index,
        })
    return sorted(outputs, key=lambda d: (not d["is_default"], d["name"].lower(), d["index"]))


def list_output_devices():
    """Enumerate connected WASAPI *playback* loopbacks, never microphones."""
    pa = _load_pyaudio()
    try:
        with pa.PyAudio() as driver:
            outputs = _loopback_devices(driver, pa)
    except (OSError, LookupError) as exc:
        raise CaptureError("Couldn't list Windows playback devices. Check Windows audio services and reconnect your headphones.") from exc
    if not outputs:
        raise CaptureError("No WASAPI playback outputs were detected. Connect or enable a speaker/headset and press Refresh devices.")
    return outputs


def _read_samples(seconds, device_index, timings=None):
    if not 1 <= seconds <= 30:
        raise CaptureError("Capture duration must be between 1 and 30 seconds.")
    started = time.perf_counter()
    pa = _load_pyaudio()
    try:
        with pa.PyAudio() as driver:
            devices = _loopback_devices(driver, pa)
            if not devices:
                raise CaptureError("No playback outputs were found. Check Windows Sound settings and press Refresh devices.")
            if device_index is None:
                device = next((d for d in devices if d["is_default"]), None)
                if device is None:
                    raise CaptureError("No default playback output is available. Select a device from the dropdown.")
            else:
                device = next((d for d in devices if d["index"] == device_index), None)
                if device is None:
                    raise CaptureError("That playback device disconnected or changed. Press Refresh devices and select it again.")
            rate = device["sample_rate"]
            # Some multi-channel output devices have more than 2 channels. Preserve
            # the native channel count, avoiding an unsupported channel override.
            channels = device["channels"]
            chunks = []
            with driver.open(
                format=pa.paInt16, channels=channels, rate=rate,
                input=True, input_device_index=device["index"],
                frames_per_buffer=2048,
            ) as stream:
                capture_started = time.perf_counter()
                frames_left = int(seconds * rate)
                while frames_left:
                    frames = min(2048, frames_left)
                    chunks.append(stream.read(frames, exception_on_overflow=False))
                    frames_left -= frames
            capture_ended = time.perf_counter()
            if timings is not None:
                timings.update({
                    "setup_s": round(capture_started - started, 2),
                    "capture_s": round(capture_ended - capture_started, 2),
                    "sample_s": float(seconds),
                })
            return b"".join(chunks), rate, channels, device
    except CaptureError:
        raise
    except (OSError, LookupError, ValueError) as exc:
        raise CaptureError(
            "Unable to capture that playback output. It may have disconnected or changed format. "
            "Reconnect it, press Refresh devices, then test again."
        ) from exc


def _signal_levels(raw):
    import numpy as np
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if not samples.size:
        return 0.0, float("-inf")
    peak = float(np.max(np.abs(samples))) / 32768.0
    rms = float(np.sqrt(np.mean(samples * samples))) / 32768.0
    rms_dbfs = float(20 * np.log10(rms)) if rms else float("-inf")
    return peak, rms_dbfs


def describe_signal(peak: float, rms_dbfs: float, clipped_fraction: float = 0.0) -> dict:
    """Explain a measured output signal; no claim to recognise a song.

    These are diagnostic thresholds, not calibrated music-quality scores.
    """
    if peak < 50 / 32768 or rms_dbfs < -60:
        return {"status": "no_sound", "headline": "No sound detected",
                "detail": "Vibe Finder did not receive a useful signal from this output.",
                "next_step": "Play music, confirm its Windows Volume Mixer output, and try another device if needed."}
    if rms_dbfs < -35:
        return {"status": "too_quiet", "headline": "Sound detected, but very quiet",
                "detail": "Audio is reaching Vibe Finder, but the average level is low.",
                "next_step": "Check the music app's volume and output routing, then test again."}
    if clipped_fraction > .01 or rms_dbfs > -6:
        return {"status": "possibly_distorted", "headline": "Sound detected — possibly too loud",
                "detail": "The sample may be overloaded or distorted.",
                "next_step": "Lower the playback volume slightly, then test again."}
    return {"status": "sound_detected", "headline": "Sound detected on this output",
            "detail": "The digital audio level looks usable for a reference capture.",
            "next_step": "Play just the intended song, then use Listen Live for the full reference capture."}


def test_output_device(device_index: int, seconds: int = 1):
    """Quick, nonpersistent output signal check; no microphone or song recognition."""
    started = time.perf_counter()
    timings = {}
    raw, rate, channels, device = _read_samples(seconds, device_index, timings=timings)
    peak, dbfs = _signal_levels(raw)
    # A peak alone can be misleading (a single notification or click counts).
    # RMS and sample saturation provide context without claiming song quality.
    import numpy as np
    samples = np.frombuffer(raw, dtype=np.int16)
    clipped_fraction = float(np.mean(np.abs(samples.astype(np.int32)) >= 32700)) if samples.size else 0.0
    diagnosis = describe_signal(peak, dbfs, clipped_fraction)
    elapsed = time.perf_counter() - started
    return {"device": device["name"], "peak_percent": round(peak * 100, 2),
            "rms_dbfs": round(dbfs, 1), "audible": diagnosis["status"] != "no_sound",
            "sample_rate": rate, "channels": channels,
            "sample_seconds": seconds, "elapsed_seconds": round(elapsed, 2),
            "setup_seconds": timings.get("setup_s"),
            "capture_seconds": timings.get("capture_s"), **diagnosis}


def capture_output(seconds: int = 20, *, device_index: int | None = None) -> bytes:
    """Capture selected (or default) Windows playback mix into an in-memory WAV."""
    raw, rate, channels, device = _read_samples(seconds, device_index)
    peak, _ = _signal_levels(raw)
    if peak < 50 / 32768:
        raise CaptureError(
            f"No audible sound detected on '{device['name']}'. "
            "Play the song, check Windows Volume Mixer routes it to this output, "
            "then try Test selected device."
        )
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(raw)
    return output.getvalue()
