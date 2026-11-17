"""Isolated, opt-in Vibe Finder acoustic comparison lab (not the stable app)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from experiments.audio_models import BackendUnavailable, CLAP, DSP, Essentia
from experiments.link_inputs import (
    CAPTURE_DURATIONS, capture_is_current, capture_link_audio, inspect_music_link,
)
from live_audio import CaptureError, list_output_devices, test_output_device

MAX_UPLOAD_BYTES = 24 * 1024 * 1024
AUDIO_TYPES = ["wav", "flac", "mp3", "ogg", "m4a"]


def backend_for(choice: str, checkpoint: str):
    if choice == "DSP (stable algorithm)":
        return DSP(seconds=30)
    if not checkpoint.strip():
        raise BackendUnavailable("A local model checkpoint path is required. No weights download automatically.")
    model_path = Path(checkpoint).expanduser()
    if choice == "CLAP (experimental)":
        return CLAP(model_path, seconds=30)
    if choice == "Essentia (experimental)":
        return Essentia(model_path, seconds=30)
    raise ValueError("Unknown matching backend")


def store_uploaded(upload, folder: Path, name: str) -> Path:
    contents = upload.getvalue()
    if not contents or len(contents) > MAX_UPLOAD_BYTES:
        raise ValueError("Each recording must be 1 byte to 24 MB")
    suffix = Path(upload.name).suffix.lower()
    if suffix not in {".wav", ".flac", ".mp3", ".ogg", ".m4a"}:
        raise ValueError("Unsupported audio file type")
    destination = folder / f"{name}{suffix}"
    destination.write_bytes(contents)
    return destination


def store_input(mode: str, uploaded, captured: dict | None, folder: Path, name: str) -> Path:
    """Always write temporary *audio bytes*, never a music-site URL as a song."""
    if mode == "Upload audio":
        if uploaded is None:
            raise ValueError(f"Upload the {name} song first.")
        return store_uploaded(uploaded, folder, name)
    if mode != "Music link":
        raise ValueError("Unknown reference input mode")
    if not captured or not captured.get("wav"):
        raise ValueError(f"Capture the {name} song playing on Windows first. A URL alone is not audio.")
    path = folder / f"{name}.wav"
    path.write_bytes(captured["wav"])
    return path


def _capture_device_panel(st):
    """Only Windows WASAPI playback outputs appear; never request a microphone."""
    st.subheader("Windows audio capture")
    if st.button("Refresh playback devices", key="lab_refresh_devices"):
        st.session_state.pop("lab_devices", None)
    if "lab_devices" not in st.session_state:
        try:
            st.session_state["lab_devices"] = list_output_devices()
        except CaptureError as exc:
            st.session_state["lab_devices"] = []
            st.warning(str(exc))
    devices = st.session_state["lab_devices"]
    if not devices:
        st.info("Connect your speakers or Bluetooth headphones and refresh. Link capture requires Windows.")
        return None
    by_index = {device["index"]: device for device in devices}
    key = "lab_device_index"
    if st.session_state.get(key) not in by_index:
        st.session_state[key] = next((d["index"] for d in devices if d["is_default"]), devices[0]["index"])
    chosen = st.selectbox(
        "Capture sound playing through", list(by_index), key=key,
        format_func=lambda idx: by_index[idx]["name"] + (" — Windows default" if by_index[idx]["is_default"] else ""),
    )
    if st.button("Quick sound check (1 second of audio)", key="lab_test_device"):
        try:
            with st.spinner("Opening the device and recording one second; startup may take longer..."):
                check = test_output_device(chosen)
            st.session_state["lab_last_device_test"] = {"index": chosen, "check": check}
        except CaptureError as exc:
            st.error(f"Cannot test selected output: {exc}")
    result = st.session_state.get("lab_last_device_test")
    if result and result["index"] == chosen:
        check = result["check"]
        if check["status"] == "sound_detected":
            st.success(check["headline"])
        elif check["status"] == "no_sound":
            st.error(check["headline"])
        else:
            st.warning(check["headline"])
        st.caption(f"{check['detail']} Test elapsed: {check['elapsed_seconds']:.2f}s, including Windows startup; this does not recognise the song.")
    st.caption("Playback audio only—not a microphone. Browser notifications or other sounds routed to this output can also be recorded.")
    return chosen


def _song_input(st, slot: str, title: str, device_index: int | None, duration: int) -> tuple[str, object, dict | None]:
    st.markdown(f"**{title}**")
    mode = st.radio(f"{title} input", ["Upload audio", "Music link"], key=f"lab_{slot}_mode", horizontal=True)
    if mode == "Upload audio":
        uploaded = st.file_uploader(f"{title} audio file", type=AUDIO_TYPES, key=f"lab_{slot}_file")
        return mode, uploaded, None
    url = st.text_input(f"Paste {title.lower()} link", key=f"lab_{slot}_link",
                        placeholder="https://www.youtube.com/watch?v=... or SoundCloud, Spotify, Jamendo...")
    valid = None
    if url.strip():
        try:
            valid = inspect_music_link(url)
            if valid["added_https"]:
                st.info("Added https:// automatically. Your music link is ready.")
            st.caption(f"Detected: {valid['provider']}. Open the link, play the song, then capture the sound from Windows.")
            st.link_button(f"Open {valid['provider']} song in browser", valid["url"])
        except ValueError as exc:
            st.warning(str(exc))
    if st.button(f"Capture {title.lower()} ({duration} seconds)", key=f"lab_{slot}_capture",
                 disabled=not valid or device_index is None):
        try:
            with st.spinner(f"Recording {duration}s of Windows playback; device startup may add extra time..."):
                capture = capture_link_audio(valid["url"], device_index, duration)
            st.session_state[f"lab_{slot}_capture_result"] = capture
        except (ValueError, CaptureError, OSError) as exc:
            st.error(f"Capture failed: {exc}")
    captured = st.session_state.get(f"lab_{slot}_capture_result")
    if not capture_is_current(captured, url):
        captured = None
    if captured:
        st.success(f"{title} captured: {captured['duration_seconds']} seconds of real audio. Total elapsed: {captured['elapsed_seconds']:.2f}s.")
        st.audio(captured["wav"], format="audio/wav")
        st.caption("Listen to the recording to confirm it contains the intended song before comparing.")
        if st.button(f"Discard {title.lower()} recording", key=f"lab_{slot}_discard"):
            st.session_state.pop(f"lab_{slot}_capture_result", None)
            captured = None
    return mode, None, captured


def main():
    import streamlit as st
    st.set_page_config(page_title="Vibe Finder • Experimental audio lab", page_icon="🧪", layout="wide")
    st.title("🧪 Vibe Finder — Experimental audio lab")
    st.warning("Preview only, separate from stable Vibe Finder (port 8502). CLAP/Essentia weights are not included. DSP runs with current dependencies.")
    st.write("Compare **actual audio recordings**. For either song, upload a file or paste a music link and capture its playback on Windows.")
    st.info("YouTube, YouTube Music, SoundCloud, Spotify, Bandcamp, Jamendo and other public HTTPS music links are supported as playback references. Links do NOT provide a waveform: play them through your browser and explicitly capture the output. The app does not rip or download streaming services.")
    selected = st.selectbox("Acoustic matching backend", ["DSP (stable algorithm)", "CLAP (experimental)", "Essentia (experimental)"])
    model_path = ""
    if selected != "DSP (stable algorithm)":
        st.info("Supply a verified local checkpoint and optional dependencies. CLAP may require extra dependencies, and Essentia is better suited to Linux/WSL. Never load untrusted model files.")
        model_path = st.text_input("Full local checkpoint path", placeholder="Path to a model you installed and verified")
    needs_output = (st.session_state.get("lab_reference_mode") == "Music link" or
                    st.session_state.get("lab_candidate_mode") == "Music link")
    device_index = None
    duration = 20
    if needs_output:
        device_index = _capture_device_panel(st)
        duration = st.select_slider("Recording duration for each link", options=list(CAPTURE_DURATIONS), value=20,
                                    key="lab_capture_duration")
        st.caption("A 20-second sample typically gives the matching engine more musical context than a one-second sound check.")
    left, right = st.columns(2)
    with left:
        ref_mode, reference, ref_capture = _song_input(st, "reference", "Reference song", device_index, duration)
    with right:
        cand_mode, candidate, cand_capture = _song_input(st, "candidate", "Comparison song", device_index, duration)
    ready_ref = reference is not None if ref_mode == "Upload audio" else ref_capture is not None
    ready_cand = candidate is not None if cand_mode == "Upload audio" else cand_capture is not None
    if st.button("Analyse and compare actual audio", type="primary", disabled=not ready_ref or not ready_cand):
        try:
            backend = backend_for(selected, model_path)
            with tempfile.TemporaryDirectory(prefix="vf-experiment-") as temp:
                folder = Path(temp)
                ref_path = store_input(ref_mode, reference, ref_capture, folder, "reference")
                candidate_path = store_input(cand_mode, candidate, cand_capture, folder, "comparison")
                with st.spinner("Extracting acoustic features from both recordings (up to 30 seconds each)..."):
                    ref_features = backend.extract(ref_path)
                    other_features = backend.extract(candidate_path)
                    score = float(backend.similarity(ref_features, other_features))
            if backend.name == "dsp":
                st.metric("Heuristic DSP similarity", f"{score:.1f}/100")
                st.caption("This score is an uncalibrated acoustic heuristic, not a probability or listening judgment.")
            else:
                st.metric(f"{backend.name.upper()} cosine similarity", f"{score:+.4f}")
                st.caption("Cosine similarity ranges from -1 to +1; it is not a calibrated match percentage.")
            st.success("Comparison completed using recorded audio waveforms. Temporary analysis files have been deleted.")
        except (BackendUnavailable, ValueError, OSError, RuntimeError, ImportError) as exc:
            st.error(f"Comparison unavailable: {exc}")
    st.divider()
    st.caption("Only capture audio you are permitted to analyse. The app doesn't fetch audio from YouTube, Spotify or other streaming services. Recorded playback stays in this browser session until discarded or the session ends; temporary analysis files are deleted. Stable Vibe Finder remains unchanged.")


if __name__ == "__main__":
    main()
