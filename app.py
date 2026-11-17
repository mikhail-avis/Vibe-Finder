"""Vibe Finder: acoustic discovery from an upload or opt-in live Windows audio."""
from __future__ import annotations

import tempfile
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote_plus

import streamlit as st

from discovery import BENCHMARK_URL, discover_tracks, get_video_title, youtube_id
from engine import GROUPS, analyse, compare
from version import APP_VERSION
from jamendo import JamendoError, SEARCH_TAGS, find_candidates, valid_client_id, test_connection, resolve_client_id, DEFAULT_CLIENT_ID
from candidate_pipeline import scan_candidates
from catalogues import SOURCES, discover_across_sources
from adaptive_discovery import search_budgets, focus_styles
from history import read_history, save_search
from live_audio import CaptureError, capture_output, list_output_devices, test_output_device
from updater import (UpdateError, check_online, stage_online, stage_package, rollback_status, stage_rollback,
                     experimental_version, stage_experimental, experimental_rollback_status, stage_experimental_rollback)

ROOT = Path(__file__).resolve().parent

st.set_page_config(page_title="Vibe Finder • Find the vibe", page_icon="🎧", layout="wide")
st.title("🎧 Vibe Finder")
st.caption(f"Prototype {APP_VERSION} • Actual acoustic comparisons of eligible recordings. Upload a song or opt into Windows output capture.")

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
                st.success(f"Up to date (installed v{online['version']}; feed v{online.get('remote_version', online['version'])}).")
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
    previous = rollback_status(ROOT)
    if previous:
        st.divider()
        st.write(f"**Stable rollback available:** v{previous['installed']} → v{previous['restore']}")
        st.caption("Restores the application files from the last successful update. Your music and settings remain untouched.")
        if st.button("Prepare rollback to previous stable version", key="stage_stable_rollback"):
            try:
                restored = stage_rollback(ROOT)
                st.warning(f"Rollback to v{restored} is ready. Close Vibe Finder and restart with run-windows.bat to apply.")
            except (UpdateError, OSError) as exc:
                st.error(f"Could not prepare rollback: {exc}")
    elif (ROOT / "updates" / "pending_rollback.json").exists():
        st.warning("Stable rollback is pending. Restart with run-windows.bat.")
    else:
        st.caption("No verified stable rollback is available yet. A backup is created automatically when you next update.")

with st.sidebar.expander("Experimental builds (separate installation)", expanded=False):
    installed_exp = experimental_version(ROOT)
    st.write("Experimental builds are installed in **experimental-build**, never over the stable Vibe Finder.")
    st.caption(f"Installed experimental version: {'none' if installed_exp == '0.0' else 'v' + installed_exp}")
    st.warning("Preview only: CLAP/Essentia model weights and optional dependencies are not bundled. Stable search remains unchanged.")
    if (ROOT / "updates" / "pending_experimental.zip").exists():
        st.info("Experimental build staged. Close Vibe Finder and restart with run-windows.bat to install separately.")
    if st.button("Check experimental releases", key="check_experimental"):
        try:
            trial = check_online(ROOT, channel="experimental")
            st.session_state["experimental_update"] = trial
            if trial is None:
                st.info("No verified experimental release published yet.")
            elif trial["current"]:
                st.success(f"Experimental installation is up to date at v{trial['version']}.")
        except (UpdateError, OSError, ValueError) as exc:
            st.error(f"Cannot check experimental releases: {exc}")
    trial = st.session_state.get("experimental_update")
    if trial and not trial.get("current"):
        st.write(f"Experimental release available: **v{trial['version']}**")
        if st.button("Download and prepare experimental build", key="download_experimental"):
            try:
                version = stage_online(ROOT, trial, channel="experimental")
                st.success(f"Experimental v{version} staged separately. Restart with run-windows.bat to install.")
            except (UpdateError, OSError, ValueError) as exc:
                st.error(f"Experimental download failed: {exc}")
    experiment_zip = st.file_uploader("Or choose a trusted experimental Vibe Finder ZIP", type=["zip"], key="experimental_upload")
    if experiment_zip is not None and st.button("Prepare experimental ZIP", key="manual_experimental"):
        try:
            version = stage_experimental(ROOT, experiment_zip.getvalue())
            st.success(f"Experimental v{version} staged. Restart with run-windows.bat.")
        except (UpdateError, OSError, ValueError) as exc:
            st.error(f"Experimental ZIP rejected: {exc}")
    entrypoint = ROOT / "experimental-build" / "experimental_landing.py"
    if entrypoint.is_file():
        st.write("**Experimental preview:** runs on a separate local Streamlit port (8502).")
        if st.button("Start experimental preview", key="start_experimental"):
            try:
                with socket.socket() as sock:
                    in_use = sock.connect_ex(("127.0.0.1", 8502)) == 0
                if in_use:
                    st.info("Port 8502 is already in use. Close that application before starting another preview.")
                else:
                    subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(entrypoint),
                                      "--server.port", "8502", "--server.address", "127.0.0.1",
                                      "--browser.gatherUsageStats", "false"],
                                     cwd=str(entrypoint.parent), stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                    st.success("Experimental preview starting. Open http://localhost:8502 in a few seconds.")
            except (OSError, ValueError) as exc:
                st.error(f"Could not start experimental preview: {exc}")
        st.link_button("Open experimental preview", "http://localhost:8502")
    exp_previous = experimental_rollback_status(ROOT)
    if exp_previous:
        st.caption(f"Previous experimental build available: v{exp_previous}")
        if st.button("Restore previous experimental build", key="rollback_experimental"):
            try:
                stage_experimental_rollback(ROOT)
                st.warning("Experimental rollback staged. Close both experimental and stable apps, then restart Vibe Finder.")
            except (UpdateError, OSError) as exc:
                st.error(f"Could not stage experimental rollback: {exc}")


SETTINGS_FILE = ROOT / "user-data" / "jamendo.json"
client_id, client_source = resolve_client_id(SETTINGS_FILE)

with st.sidebar.expander("Jamendo catalogue connection", expanded=False):
    st.success("Ready — Jamendo Client ID configured automatically.")
    st.caption(f"Using the {client_source}. No account setup or ID entry is required for music discovery.")
    if st.button("Test Jamendo connection (metadata only)"):
        try:
            diagnostic = test_connection(client_id)
            st.success(f"Jamendo API responded: {diagnostic['returned']} tracks found; "
                       f"{diagnostic['downloadable']} eligible download links in this sample.")
            st.caption("This tests the API connection, not acoustic matching.")
        except JamendoError as exc:
            st.error(f"Jamendo connection failed: {exc}")
            st.info("If the default ID is no longer accepted, you can set another ID under Advanced settings.")
    with st.expander("Advanced: override the Client ID"):
        st.caption("Only change this if you're using a separate Jamendo developer application. The included ID is public, not a secret.")
        replacement_id = st.text_input("Optional Jamendo Client ID override", value=client_id, max_chars=64)
        if st.button("Save Client ID override"):
            if not valid_client_id(replacement_id):
                st.error("Enter a valid public Jamendo application Client ID.")
            else:
                SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
                SETTINGS_FILE.write_text(json.dumps({"client_id": replacement_id.strip()}), encoding="utf-8")
                st.rerun()
        if st.button("Restore built-in Client ID"):
            # Only clear the optional override, not any other user files.
            SETTINGS_FILE.unlink(missing_ok=True)
            st.rerun()
    st.caption("Jamendo is a limited catalogue of independent music; it does not search every commercial recording.")

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
    st.markdown("**Windows playback device**")
    if st.button("Refresh audio devices", key="refresh_audio_devices"):
        st.session_state.pop("output_devices", None)
        st.session_state.pop("last_audio_check", None)
    if "output_devices" not in st.session_state:
        try:
            st.session_state["output_devices"] = list_output_devices()
            st.session_state.pop("selected_output_index", None)
        except CaptureError as exc:
            st.session_state["output_devices"] = []
            st.warning(str(exc))
    devices = st.session_state["output_devices"]
    selected_index = None
    if devices:
        by_index = {device["index"]: device for device in devices}
        indices = list(by_index)
        if st.session_state.get("selected_output_index") not in by_index:
            default = next((device for device in devices if device["is_default"]), devices[0])
            st.session_state["selected_output_index"] = default["index"]
        selected_index = st.selectbox(
            "Listen to audio playing through",
            indices,
            format_func=lambda idx: f"{by_index[idx]['name']}{' — Windows default' if by_index[idx]['is_default'] else ''}",
            key="selected_output_index",
        )
        st.caption("Only playback outputs are shown—not microphones. Select the speaker or Bluetooth headphones actually playing your song.")
        st.caption("Quick sound test checks whether audio reaches Vibe Finder. It does NOT identify the song or confirm a correct music match.")
        if st.button("Quick sound test (1-second audio sample)", key="test_audio_device"):
            try:
                with st.spinner("Opening the selected Windows output and collecting 1 second of audio…"):
                    check = test_output_device(selected_index)
                st.session_state["last_audio_check"] = {"device_index": selected_index, "result": check}
            except CaptureError as exc:
                st.session_state.pop("last_audio_check", None)
                st.error(f"Output test failed: {exc}")
        last_check = st.session_state.get("last_audio_check")
        if last_check and last_check["device_index"] == selected_index:
            check = last_check["result"]
            if check["status"] == "sound_detected":
                st.success(f"✅ {check['headline']}")
            elif check["status"] == "no_sound":
                st.error(f"⛔ {check['headline']}")
            else:
                st.warning(f"⚠️ {check['headline']}")
            st.write(f"**Selected output:** {check['device']}")
            st.write(check["detail"])
            st.info(f"**Next:** {check['next_step']}")
            st.caption(f"Audio recorded: {check['sample_seconds']} second • Audio-check elapsed: {check['elapsed_seconds']:.2f} seconds (includes Windows device startup). The app may take additional time to redraw.")
            with st.expander("Technical audio details"):
                st.write(f"Peak: {check['peak_percent']:.2f}% of digital full scale; average (RMS): {check['rms_dbfs']:.1f} dBFS")
                st.write(f"Device setup: {check['setup_seconds']} s • Recording: {check['capture_seconds']} s")
                st.caption("These values describe signal level only—not whether the correct music was captured or how well it matches other songs.")
    if st.button("Listen Live: capture 20 seconds of computer output", disabled=not (bool(video_id) and selected_index is not None)):
        try:
            with st.spinner(f"Listening on {by_index[selected_index]['name']} for 20 seconds…"):
                captured = capture_output(20, device_index=selected_index)
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
    sample_count = st.slider("Maximum Jamendo candidates to analyse", 4, 40, 16, step=2)
    selected_sources = tuple(st.multiselect(
        "Acoustic comparison catalogues", SOURCES, default=list(SOURCES),
        help="Only recordings with a permitted audio download enter the real waveform comparison."
    ))
    st.caption("Jamendo + ccMixter + Wikimedia Commons. All selected catalogues run through the same audio analyser; metadata-only entries are excluded.")
    adaptive_enabled = st.checkbox("Adaptive two-stage discovery (explore, then search promising styles)", value=True)
    st.caption("Speed: up to 4 downloads and 2 DSP analysis processes. The adaptive second pass reserves part of your track budget, not extra downloads. Locally cached fingerprints are reused.")

st.subheader("2. Find similar-sounding music")
if st.button("Find Similar Music", type="primary", use_container_width=True):
    st.session_state.pop("last_results", None)
    st.session_state.pop("last_summary", None)
    if not valid_client_id(client_id):
        st.error("The configured Jamendo Client ID is invalid. Check the advanced connection settings.")
        st.stop()
    if not selected_sources:
        st.error("Select at least one music catalogue in Advanced matching preferences.")
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
        first_budget, second_budget = search_budgets(sample_count, adaptive_enabled)
        with st.spinner("Pass 1: sampling permitted recordings across Jamendo styles…"):
            candidates, warnings = discover_across_sources(client_id, max_tracks=first_budget,
                                                          sources=selected_sources)
        if not candidates:
            st.warning("No downloadable, licensed audio candidates were found in the selected catalogues. Try another source or check the provider warnings.")
            if warnings:
                st.write(warnings)
            st.stop()

        scored = []
        skipped = []
        all_candidates = list(candidates)
        focus = ()
        bar = st.progress(0, text="Analysing broad discovery sample")
        live_status = st.empty()
        cache_path = ROOT / "user-data" / "jamendo-fingerprints.json"

        def run_batch(batch: list[dict], stage_label: str) -> None:
            for event in scan_candidates(batch, reference, weights, cache_path,
                                         download_workers=4, analysis_workers=2):
                if event["match"]:
                    scored.append(event["match"])
                if event["error"]:
                    skipped.append(event["error"])
                # The total is a fixed user-selected budget, not just the current batch.
                completed = len(scored) + len(skipped)
                bar.progress(min(1.0, completed / sample_count),
                             text=f"{stage_label}: {completed}/{sample_count} sampled • {len(scored)} analysed")
                if scored:
                    leaders = sorted(scored, key=lambda item: item["score"], reverse=True)[:3]
                    live_status.write("Best acoustic matches so far (provisional): " + " • ".join(
                        f"{song['artist']} — {song['title']} ({song['score']:.1f})" for song in leaders))

        run_batch(candidates, "Pass 1 • broad scan")
        if second_budget:
            focus = focus_styles(scored, SEARCH_TAGS)
            # If the first pass yielded no usable audio, broaden again instead
            # of assuming any genre is relevant to the reference.
            styles = focus or SEARCH_TAGS
            with st.spinner(f"Pass 2: discovering fresh downloadable audio in {', '.join(styles)}…"):
                second, issues = discover_across_sources(client_id,
                    max_tracks=sample_count - len(candidates), sources=selected_sources,
                    tags=styles, exclude_ids={c['id'] for c in all_candidates})
            warnings.extend(issues)
            # A focused category may contain too few eligible tracks. Use
            # remaining styles as exploration instead of repeating candidates.
            remaining = sample_count - len(candidates) - len(second)
            if remaining > 0 and focus:
                fallback_tags = tuple(t for t in SEARCH_TAGS if t not in focus)
                if fallback_tags:
                    additional, issues = discover_across_sources(client_id, max_tracks=remaining,
                              sources=selected_sources, tags=fallback_tags,
                              exclude_ids={c['id'] for c in [*all_candidates, *second]})
                    second.extend(additional)
                    warnings.extend(issues)
            if second:
                run_batch(second, "Pass 2 • focused scan")
                all_candidates.extend(second)

        bar.empty()
        live_status.empty()
        scored.sort(key=lambda item: item["score"], reverse=True)
        st.session_state["last_results"] = scored
        st.session_state["last_summary"] = {
            "candidate_count": len(all_candidates), "skipped": skipped, "warnings": warnings,
            "reference_bpm": reference["bpm"], "focus": focus,
            "adaptive": bool(second_budget), "weights": dict(weights),
            "catalogues": selected_sources,
        }
        reference_label = reference_upload.name if input_mode == "Upload an audio file" else f"Windows capture for {video_id}"
        try:
            save_search(ROOT / "user-data" / "search-history.json",
                        reference_label=reference_label, matches=scored,
                        examined=len(all_candidates), weights=weights, adaptive=bool(second_budget))
        except OSError:
            st.caption("Search completed, but its local history couldn't be saved.")
    except Exception as exc:
        st.error(f"Discovery could not finish: {exc}")

if "last_results" in st.session_state:
    scored = st.session_state["last_results"]
    summary = st.session_state["last_summary"]
    if scored:
        from collections import Counter
        source_counts = Counter(track.get("source", "Jamendo") for track in scored)
        source_summary = ", ".join(f"{name}: {num}" for name, num in source_counts.items())
        st.success(f"Real audio analysed: {len(scored)} recordings from {summary['candidate_count']} eligible candidates. Sources: {source_summary}. Reference BPM: {summary['reference_bpm']:.1f}.")
        if summary.get("focus"):
            st.caption(f"Second-pass discovery prioritised {', '.join(summary['focus'])} based on the first-pass audio scores; this does not guarantee better matches.")
        for rank, track in enumerate(scored[:10], 1):
            with st.container(border=True):
                left, right = st.columns([4, 1])
                with left:
                    st.write(f"**{rank}. {track['artist']} — {track['title']}**")
                    st.caption(f"**Catalogue:** {track.get('source', 'Jamendo')} • Strongest feature similarities: {', '.join(track['strengths'])} • estimated BPM: {track['bpm']:.1f} • search tag: {track.get('source_style', 'unspecified')}")
                    with st.expander("Why did this track rank here?"):
                        st.caption("Acoustic feature similarity by category. These are heuristic scores, not listening-based accuracy ratings.")
                        for group in GROUPS:
                            value = float(track["components"].get(group, 0))
                            st.progress(min(1.0, max(0.0, value / 100)),
                                        text=f"{group}: {value:.1f}/100 • preference weight {summary['weights'][group]}/5")
                    st.link_button("Listen on " + track.get("source", "Jamendo"), track["listen_url"])
                    if track.get("license_url"):
                        st.link_button("Track licence", track["license_url"])
                with right:
                    st.metric("Heuristic match", f"{track['score']:.1f}/100")
    else:
        st.warning("Candidates were found but none could be decoded and analysed. This isn't evidence that similar songs don't exist.")
    if summary["skipped"] or summary["warnings"]:
        with st.expander("Unanalysed candidates / API issues"):
            st.write("\n".join((summary["warnings"] + summary["skipped"])[:35]))
    st.caption("Only recordings from selected sources that were actually downloaded, decoded and acoustically compared receive scores. Unavailable or unlicensed audio is skipped. Fingerprints are cached, raw tracks are discarded. Scores are uncalibrated similarity indices, not probabilities or catalogue-wide rankings.")

with st.sidebar.expander("Recent discoveries (stored on this PC)"):
    past_searches = read_history(ROOT / "user-data" / "search-history.json")
    if not past_searches:
        st.caption("No recent searches. Only music metadata and scores are saved, never audio.")
    else:
        for item in past_searches[:5]:
            top = (item.get("matches") or [{}])[0]
            st.write(f"**{item.get('reference', 'Reference')}**")
            st.caption(f"{item.get('at_utc', '')[:10]} • {item.get('analysed', 0)} candidates examined")
            if top.get("artist"):
                st.write(f"Top audio match: **{top['artist']} — {top.get('title', '')}**")
                st.caption(f"Source: {top.get('source', 'Jamendo')} • similarity: {float(top.get('score', 0)):.1f}/100 (heuristic, not probability)")
                if top.get("listen_url"):
                    st.link_button("Listen to top match", top["listen_url"])
            else:
                st.caption("No playable, analysed matches in this search")
            vid = item.get("reference", "").removeprefix("Windows capture for ")
            if vid != item.get("reference", "") and len(vid) == 11:
                st.link_button("Open reference song", "https://www.youtube.com/watch?v=" + vid)
        if st.button("Clear local search history", key="clear_history"):
            (ROOT / "user-data" / "search-history.json").unlink(missing_ok=True)
            st.rerun()

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
