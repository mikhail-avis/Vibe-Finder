"""Isolated, opt-in Vibe Finder acoustic comparison lab (not the stable app)."""
from __future__ import annotations

import tempfile
import hashlib
from pathlib import Path

from experiments.audio_models import BackendUnavailable, CLAP, DSP, Essentia
from experiments.discovery_lab import find_similar_audio
from catalogues import SOURCES
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
    st.set_page_config(page_title="Vibe Finder • Experimental music discovery", page_icon="🧪", layout="wide")
    st.title("🧪 Vibe Finder — Experimental music discovery")
    st.caption("One reference song → real waveform analysis → multiple licensed music catalogues → ranked similar-sounding songs")
    st.warning("Experimental preview on port 8502. AI checkpoints are optional and not included; the DSP matcher works immediately. Stable Vibe Finder remains separate.")
    activity = st.radio("What would you like to do?", ["Find similar music", "Advanced: Compare two songs"], horizontal=True)
    selected = st.selectbox("Acoustic matching engine", ["DSP (stable algorithm)", "CLAP (experimental)", "Essentia (experimental)"])
    model_path = ""
    if selected != "DSP (stable algorithm)":
        st.info("Supply a trusted local model checkpoint; this is optional and the AI models are not automatically downloaded. Essentia generally needs Linux/WSL.")
        model_path = st.text_input("Verified local model checkpoint path", placeholder="C:/path/to/your/verified/model")
    st.info("For YouTube, YouTube Music, SoundCloud, Spotify or other public music links, play the track in your browser and explicitly capture audio from your selected Windows playback output. A link alone doesn't provide audio.")
    needs_output = (st.session_state.get("lab_reference_mode") == "Music link" or
                    (activity.startswith("Advanced") and st.session_state.get("lab_candidate_mode") == "Music link"))
    device_index = _capture_device_panel(st) if needs_output else None
    duration = 20
    if needs_output:
        duration = st.select_slider("Recording duration", options=list(CAPTURE_DURATIONS), value=20, key="lab_capture_duration")
    if activity == "Find similar music":
        st.subheader("1. Give Vibe Finder one song")
        ref_mode, reference, ref_capture = _song_input(st, "reference", "Reference song", device_index, duration)
        ready_ref = reference is not None if ref_mode == "Upload audio" else ref_capture is not None
        st.subheader("2. Choose where to search")
        chosen_sources = st.multiselect("Music catalogues", list(SOURCES), default=list(SOURCES),
                                        help="Each candidate must have downloadable, licence-eligible audio to be scored.")
        sample_count = st.slider("Candidate recordings to analyse", min_value=4, max_value=24, value=12, step=4)
        st.caption("This compares a sampled selection of actual recordings. It does not search every song on every platform, and streaming-site links are not candidate catalogues.")
        # A prior search must never be presented as results for a changed song.
        raw_reference = (reference.getvalue() if ref_mode == "Upload audio" and reference is not None
                         else (ref_capture or {}).get("wav", b""))
        identity = (hashlib.sha256(raw_reference).hexdigest() if raw_reference else "",
                    selected, model_path, tuple(chosen_sources), sample_count)
        if st.button("Find similar-sounding songs", type="primary", disabled=not ready_ref or not chosen_sources):
            try:
                matcher = backend_for(selected, model_path)
                with tempfile.TemporaryDirectory(prefix="vf-discovery-ref-") as td:
                    reference_path = store_input(ref_mode, reference, ref_capture, Path(td), "reference")
                    bar = st.progress(0, text="Finding usable candidate audio...")
                    def update(done, total, matches):
                        bar.progress(done / max(1, total), text=f"Audio analysed: {done}/{total} • {matches} usable matches")
                    with st.spinner("Discovering catalogue recordings and comparing their real audio..."):
                        result = find_similar_audio(reference_path, matcher, sources=tuple(chosen_sources),
                                                    sample_count=sample_count, on_progress=update)
                    bar.empty()
                st.session_state["lab_discovery_result"] = result
                st.session_state["lab_discovery_identity"] = identity
            except (BackendUnavailable, ValueError, OSError, RuntimeError, ImportError) as exc:
                st.error(f"Music discovery couldn't finish: {exc}")
        result = (st.session_state.get("lab_discovery_result")
                  if st.session_state.get("lab_discovery_identity") == identity else None)
        if result:
            st.subheader("3. Similar-sounding music")
            st.write(f"Compared real audio from **{result['analysed']} of {result['sampled']}** sampled tracks across {', '.join(result['sources'])}.")
            if not result["ranked"]:
                st.warning("No candidate recordings could be decoded and compared; this does not mean there are no similar songs.")
            for i, song in enumerate(result["ranked"], 1):
                with st.container(border=True):
                    st.markdown(f"**{i}. {song['artist']} — {song['title']}**")
                    st.caption(f"{song['source']} • actual acoustic analysis")
                    if result['backend'] == 'dsp':
                        st.write(f"DSP heuristic similarity: **{song['score']:.1f}/100**")
                    else:
                        st.write(f"{result['backend'].upper()} cosine similarity: **{song['score']:+.4f}**")
                    if song['listen_url'].startswith('https://'):
                        st.link_button("Listen to track", song['listen_url'])
                    if song['license_url'].startswith('https://'):
                        st.link_button("Licence details", song['license_url'])
            if result['warnings'] or result['skipped']:
                with st.expander("Catalogue warnings and tracks that couldn't be analysed"):
                    st.write("\n".join((result['warnings'] + result['skipped'])[:30]))
            st.caption("Rankings are not calibrated probabilities. Candidate discovery is a bounded sample, not an exhaustive music search. No scores come from track names or metadata.")
    else:
        st.subheader("Developer-only comparison — two known recordings")
        st.caption("Use this to test whether a new AI model recognizes the similarity of two tracks. It is not required for normal discovery.")
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
                with tempfile.TemporaryDirectory(prefix="vf-experiment-") as td:
                    folder = Path(td)
                    ref_path = store_input(ref_mode, reference, ref_capture, folder, "reference")
                    candidate_path = store_input(cand_mode, candidate, cand_capture, folder, "comparison")
                    with st.spinner("Extracting features from both recordings..."):
                        score = float(backend.similarity(backend.extract(ref_path), backend.extract(candidate_path)))
                if backend.name == "dsp":
                    st.metric("Heuristic DSP similarity", f"{score:.1f}/100")
                else:
                    st.metric(f"{backend.name.upper()} cosine similarity", f"{score:+.4f}")
                st.caption("Uncalibrated acoustic similarity, not a probability. No temporary analysis audio retained.")
            except (BackendUnavailable, ValueError, OSError, RuntimeError, ImportError) as exc:
                st.error(f"Comparison unavailable: {exc}")
    st.divider()
    st.caption("Only analyse audio you have permission to use. Link capture requires explicit Windows output recording and does not scrape or download streaming-service audio.")


if __name__ == "__main__":
    main()
