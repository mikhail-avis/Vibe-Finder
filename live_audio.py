"""Opt-in Windows WASAPI playback capture, with selectable output devices.

Only output *loopback* devices are enumerated; microphones are never opened.
Audio is held in memory only. Other system sounds may be captured alongside music.
"""
from __future__ import annotations

import io
import sys
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


def _read_samples(seconds, device_index):
    if not 3 <= seconds <= 30:
        raise CaptureError("Capture duration must be between 3 and 30 seconds.")
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
                frames_left = int(seconds * rate)
                while frames_left:
                    frames = min(2048, frames_left)
                    chunks.append(stream.read(frames, exception_on_overflow=False))
                    frames_left -= frames
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


def test_output_device(device_index: int, seconds: int = 3):
    """Short nonpersistent signal check; no microphone access."""
    raw, rate, channels, device = _read_samples(seconds, device_index)
    peak, dbfs = _signal_levels(raw)
    return {"device": device["name"], "peak_percent": round(peak * 100, 2),
            "rms_dbfs": round(dbfs, 1), "audible": peak >= 50 / 32768,
            "sample_rate": rate, "channels": channels}


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
