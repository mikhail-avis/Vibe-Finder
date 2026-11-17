"""Vibe Finder: acoustic discovery from an upload or opt-in live Windows audio."""
from __future__ import annotations

import tempfile
import json
import os
from pathlib import Path
from urllib.parse import quote_plus

import streamlit as st

from discovery import BENCHMARK_URL, discover_tracks, get_video_title, youtube_id
from engine import GROUPS, analyse, compare
from version import APP_VERSION
from jamendo import JamendoError, find_candidates, valid_client_id, test_connection
from candidate_pipeline import scan_candidates
from live_audio import CaptureError, capture_output
from updater import UpdateError, check_online, stage_online, stage_package

ROOT = Path(__file__).resolve().parent

st.set_page_config(page_title="Vibe Finder • Find the vibe", page_icon="🎧", layout="wide")
st.title("🎧 Vibe Finder")
st.caption(f"Prototype {APP_VERSION} • Genuine audio analysis for authorised music. Choose a reference upload or opt-in Windows listening.")

with st.sidebar.expander(f"App updates (v{APP_VERSION})", expanded=False):
    st.write("Updates go into this same folder. Your music library and settings stay put.")
    pending_update = ROOT / "updates" / "pending_update.zip"
    if pending_update.exists():
        st.success("Update staged. Close Vibe Finder and start it again using run-windows.bat to apply it.")
    update_zip = st.file_uploader("Or select an official Vibe Finder update ZIP", type=["zip"], key="update_package")
    if update_zip is not None and st.button("Prepare this update", key="prepare_manual_update"):
        try:
            new = stage_package(ROOT, update_zip.getvalue())
            st.success(f"Version {new} is ready. Restart Vibe Finder using the usual launcher.")
        except (UpdateError, OSError, ValueError) as exc:
            st.error(f"Update rejected: {exc}")
    if st.button("Check for online updates"):
        try:
            online = check_online(ROOT)
            st.session_state["online_update"] = online
            if online is None:
                st.info("Automatic online releases are not configured yet. They'll be enabled once there's a trusted release website.")
            elif online["current"]:
                st.success(f"Up to date (v{online['version']}).")
        except Exception as exc:
            st.error(f"Could not check updates: {exc}")
    online = st.session_state.get("online_update")
    if online and not online.get("current"):
        st.write(f"A newer version is available: **v{online['version']}**")
        if st.button("Download and prepare update"):
            try:
                new = stage_online(ROOT, online)
                st.success(f"Version {new} is ready. Restart with run-windows.bat to install it.")
            except Exception as exc:
                st.error(f"Download or verification failed: {exc}")
    st.caption("Only apply ZIP files from a source you trust. The existing launcher is kept in place.")


SETTINGS_FILE = ROOT / "user-data" / "jamendo.json"


def saved_client_id() -> str:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return str(data.get("client_id", ""))
    except (OSError, ValueError):
        return ""


with st.sidebar.expander("Jamendo catalogue settings", expanded=True):
    st.write("Enter the Jamendo developer Client ID once. Saved only on this computer, not uploaded to GitHub.")
    client_id = st.text_input("Jamendo Client ID", value=os.environ.get("JAMENDO_CLIENT_ID") or saved_client_id(), max_chars=64)
    if st.button("Save Jamendo Client ID"):
        if not valid_client_id(client_id):
            st.error("Enter the application Client ID, not a password or secret.")
        else:
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps({"client_id": client_id.strip()}), encoding="utf-8")
            st.success("Client ID saved locally. It will be available next time.")
    if st.button("Test Jamendo connection (metadata only)"):
        if not valid_client_id(client_id):
            st.error("Enter a valid Jamendo Client ID first.")
        else:
            try:
                diagnostic = test_connection(client_id)
                st.success(f"Jamendo API responded. {diagnostic['returned']} tracks returned; "
                           f"{diagnostic['downloadable']} have eligible download links in this small sample.")
                st.caption("Connection confirmed, but this does not test audio downloads or sound matching. "
                           "Use Find Similar Music for the full test.")
            except JamendoError as exc:
                st.error(f"Jamendo connection test failed: {exc}")
    st.caption("Jamendo offers independent and Creative Commons music; this is not a search of every recording online.")

st.subheader("1. Choose one reference song")
input_mode = st.radio("Reference input", ["Upload an audio file", "Paste a YouTube link"], horizontal=True)
reference_upload = None
reference_url = ""
if input_mode == "Upload an audio file":
    reference_upload = st.file_uploader("Choose your reference song", type=["wav", "flac", "mp3", "ogg", "m4a"])
    st.caption("Upload one file you have permission to analyse. No comparison songs needed.")
else:
    reference_url = st.text_input("YouTube link", value=BENCHMARK_URL)
    video_id = youtube_id(reference_url)
    if video_id:
        st.link_button("Open the song on YouTube", f"https://www.youtube.com/watch?v={video_id}")
    st.info("A YouTube URL alone provides metadata, not the audio waveform. For an acoustic match, play audio you are authorised to capture on this Windows PC, then select Listen Live. The app does not rip YouTube audio.")
    if st.button("Listen Live: capture 20 seconds of computer output", disabled=not bool(video_id)):
        try:
            with st.spinner("Listening to your Windows output for 20 seconds…"):
                captured = capture_output(20)
                with tempfile.TemporaryDirectory() as td:
                    wav_path = Path(td) / "reference.wav"
                    wav_path.write_bytes(captured)
                    features = analyse(wav_path)
            st.session_state["captured_reference"] = {"url": reference_url, "features": features}
            st.success(f"Audio analysed. Estimated tempo: {features['bpm']:.1f} BPM. Ready to find similar music.")
        except (CaptureError, ValueError, OSError) as exc:
            st.error(f"Live audio capture failed: {exc}")
    captured = st.session_state.get("captured_reference")
    if captured and captured["url"] == reference_url:
        st.caption("Captured audio fingerprint ready. No microphone or permanent recording used.")

with st.expander("Advanced matching preferences"):
    cols = st.columns(5)
    starting = {"Rhythm": 5, "Bass": 5, "Timbre": 4, "Atmosphere": 4, "Energy": 3}
    weights = {name: col.slider(name, 0, 5, starting[name]) for name, col in zip(GROUPS, cols)}
    sample_count = st.slider("Number of Jamendo candidate tracks to analyse", 4, 30, 16, step=2)
    st.caption("Speed: up to 4 simultaneous downloads and 2 DSP analysis processes. Results update as tracks finish. Previously analysed tracks reuse local fingerprints.")

st.subheader("2. Find similar-sounding music")
if st.button("Find Similar Music", type="primary", use_container_width=True):
    st.session_state.pop("last_results", None)
    st.session_state.pop("last_summary", None)
    if not valid_client_id(client_id):
        st.error("Set your Jamendo Client ID in the sidebar first.")
        st.stop()
    if not any(weights.values()):
        st.error("Select at least one positive matching weight.")
        st.stop()
    try:
        if input_mode == "Upload an audio file":
            if reference_upload is None:
                st.warning("Upload a reference song first.")
                st.stop()
            with tempfile.TemporaryDirectory() as td:
                ext = Path(reference_upload.name).suffix.lower()
                path = Path(td) / ("reference" + ext)
                path.write_bytes(reference_upload.getvalue())
                with st.spinner("Analysing your reference audio…"):
                    reference = analyse(path, max_seconds=50)
        else:
            captured = st.session_state.get("captured_reference")
            if not captured or captured["url"] != reference_url:
                st.warning("Play the reference song and press Listen Live before searching. A YouTube link alone cannot be acoustically analysed.")
                st.stop()
            reference = captured["features"]
        with st.spinner("Looking for downloadable, authorised Jamendo candidate tracks…"):
            candidates, warnings = find_candidates(client_id, max_tracks=sample_count)
        if not candidates:
            st.warning("No eligible Jamendo candidates returned. Check the client ID or try again later.")
            if warnings:
                st.write(warnings)
            st.stop()
        scored = []
        skipped = []
        bar = st.progress(0, text="Starting parallel audio comparisons")
        live_status = st.empty()
        cache_path = ROOT / "user-data" / "jamendo-fingerprints.json"
        for event in scan_candidates(candidates, reference, weights, cache_path,
                                     download_workers=4, analysis_workers=2):
            if event["match"]:
                scored.append(event["match"])
            if event["error"]:
                skipped.append(event["error"])
            bar.progress(event["completed"] / event["total"],
                         text=f"Processed {event['completed']} / {event['total']} songs • {len(scored)} matches")
            if scored:
                leaders = sorted(scored, key=lambda item: item["score"], reverse=True)[:3]
                live_status.write("Best matches so far (provisional): " + " • ".join(
                    f"{song['artist']} — {song['title']} ({song['score']:.1f})" for song in leaders))
        bar.empty()
        live_status.empty()
        scored.sort(key=lambda item: item["score"], reverse=True)
        st.session_state["last_results"] = scored
        st.session_state["last_summary"] = {"candidate_count": len(candidates), "skipped": skipped, "warnings": warnings, "reference_bpm": reference["bpm"]}
    except Exception as exc:
        st.error(f"Discovery could not finish: {exc}")

if "last_results" in st.session_state:
    scored = st.session_state["last_results"]
    summary = st.session_state["last_summary"]
    if scored:
        st.success(f"Acoustically compared {len(scored)} Jamendo recordings from {summary['candidate_count']} sampled candidates. Reference BPM: {summary['reference_bpm']:.1f}.")
        for rank, track in enumerate(scored[:10], 1):
            with st.container(border=True):
                left, right = st.columns([4, 1])
                with left:
                    st.write(f"**{rank}. {track['artist']} — {track['title']}**")
                    st.caption(f"Closest sonic characteristics: {', '.join(track['strengths'])} • estimated BPM: {track['bpm']:.1f}")
                    st.link_button("Listen on Jamendo", track["listen_url"])
                    if track.get("license_url"):
                        st.link_button("Track licence", track["license_url"])
                with right:
                    st.metric("Heuristic match", f"{track['score']:.1f}/100")
    else:
        st.warning("Candidates were found but none could be decoded and analysed. This isn't evidence that similar songs don't exist.")
    if summary["skipped"] or summary["warnings"]:
        with st.expander("Unanalysed candidates / API issues"):
            st.write("\n".join((summary["warnings"] + summary["skipped"])[:35]))
    st.caption("Only the sampled, artist-downloadable Jamendo tracks were scored. Fingerprints are cached locally without saving MP3s. Scores are uncalibrated similarity indices, not probabilities, and cannot claim to rank all Jamendo music.")

with st.expander("Fallback: YouTube metadata-only discovery"):
    st.caption("This feature does NOT compare audio; it's here for when a reference recording cannot be analysed.")
    fallback_url = st.text_input("YouTube link for metadata search", value=reference_url or BENCHMARK_URL, key="fallback_url")
    if st.button("Find metadata-based leads"):
        if not youtube_id(fallback_url):
            st.error("Enter a valid YouTube link.")
        else:
            try:
                title = get_video_title(fallback_url)
                tracks, issues = discover_tracks(title, limit=10)
                st.write(f"Identified: **{title}**")
                for track in tracks:
                    st.write(f"{track['artist']} — {track['track']}")
                    st.link_button("Find on YouTube", track["youtube_search_url"])
                if issues:
                    st.write(issues)
            except (ValueError, LookupError) as exc:
                st.error(str(exc))

st.divider()
st.caption("No YouTube ripping. Listen Live captures opt-in system output, potentially including notification sounds, not room audio. Only analyse audio you're permitted to capture. Jamendo candidate audio is temporary and not redistributed. Jamendo artist download permissions and per-track licences are respected.")
