"""Isolated, opt-in Vibe Finder audio similarity preview. Not the stable app."""
from __future__ import annotations

import tempfile
from pathlib import Path

from experiments.audio_models import BackendUnavailable, CLAP, DSP, Essentia


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


def main():
    import streamlit as st
    st.set_page_config(page_title="Vibe Finder • Experimental audio lab", page_icon="🧪", layout="wide")
    st.title("🧪 Vibe Finder — Experimental audio lab")
    st.warning("Preview only. This runs separately from the stable Vibe Finder on port 8502. AI model weights are NOT included or downloaded. DSP works with the stable application's dependencies.")
    st.write("Compare **actual recordings**, not song names. Upload two audio files you have the right to analyse.")
    selected = st.selectbox("Acoustic matching backend", ["DSP (stable algorithm)", "CLAP (experimental)", "Essentia (experimental)"])
    model_path = ""
    if selected != "DSP (stable algorithm)":
        st.info("Optional: supply a locally verified model file. CLAP may require additional dependencies; Essentia TensorFlow is usually easier through Linux/WSL than native Windows. Never load untrusted PyTorch checkpoints.")
        model_path = st.text_input("Full local checkpoint path", placeholder="Path to a model you installed and verified")
    left, right = st.columns(2)
    with left:
        reference = st.file_uploader("Reference song", type=AUDIO_TYPES, key="experimental_reference")
    with right:
        candidate = st.file_uploader("Comparison song", type=AUDIO_TYPES, key="experimental_candidate")
    if st.button("Analyse and compare audio", type="primary", disabled=reference is None or candidate is None):
        try:
            backend = backend_for(selected, model_path)
            with tempfile.TemporaryDirectory(prefix="vf-experiment-") as temp:
                folder = Path(temp)
                ref_path = store_uploaded(reference, folder, "reference")
                candidate_path = store_uploaded(candidate, folder, "comparison")
                with st.spinner("Analysing both waveforms (up to 30 seconds per song)…"):
                    ref_features = backend.extract(ref_path)
                    other_features = backend.extract(candidate_path)
                    score = float(backend.similarity(ref_features, other_features))
            if backend.name == "dsp":
                st.metric("Heuristic DSP similarity", f"{score:.1f}/100")
                st.caption("This is an uncalibrated acoustic heuristic, NOT a probability that the songs are similar.")
            else:
                st.metric(f"{backend.name.upper()} cosine similarity", f"{score:+.4f}")
                st.caption("Cosine similarity ranges -1 to +1. It is NOT a percent or a calibrated listening judgment.")
            st.success("Comparison completed from both uploaded audio files. No audio files were retained.")
        except (BackendUnavailable, ValueError, OSError, RuntimeError, ImportError) as exc:
            st.error(f"Comparison unavailable: {exc}")
    st.divider()
    st.caption("For statistically meaningful accuracy comparisons, use the human-judged command-line benchmark in experiments/README.md. Stable Vibe Finder and its music catalogues remain unchanged.")


if __name__ == "__main__":
    main()
