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
from jamendo import JamendoError, SEARCH_TAGS, find_candidates, valid_client_id, test_connection
from candidate_pipeline import scan_candidates
from adaptive_discovery import search_budgets, focus_styles
from history import read_history, save_search
from live_audio import CaptureError, capture_output, list_output_devices, test_output_device
from updater import UpdateError, check_online, stage_online, stage_package

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
    adaptive_enabled = st.checkbox("Adaptive two-stage discovery (explore, then search promising styles)", value=True)
    st.caption("Speed: up to 4 downloads and 2 DSP analysis processes. The adaptive second pass reserves part of your track budget, not extra downloads. Locally cached fingerprints are reused.")

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
        first_budget, second_budget = search_budgets(sample_count, adaptive_enabled)
        with st.spinner("Pass 1: sampling permitted recordings across Jamendo styles…"):
            candidates, warnings = find_candidates(client_id, max_tracks=first_budget)
        if not candidates:
            st.warning("No eligible Jamendo candidates returned. Check the client ID or try again later.")
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
            with st.spinner(f"Pass 2: discovering fresh tracks in {', '.join(styles)}…"):
                second, issues = find_candidates(client_id, max_tracks=sample_count - len(candidates),
                                                 tags=styles,
                                                 exclude_ids={c['id'] for c in all_candidates})
            warnings.extend(issues)
            # A focused category may contain too few eligible tracks. Use
            # remaining styles as exploration instead of repeating candidates.
            remaining = sample_count - len(candidates) - len(second)
            if remaining > 0 and focus:
                fallback_tags = tuple(t for t in SEARCH_TAGS if t not in focus)
                if fallback_tags:
                    additional, issues = find_candidates(client_id, max_tracks=remaining,
                              tags=fallback_tags,
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
        st.success(f"Acoustically compared {len(scored)} Jamendo recordings from {summary['candidate_count']} sampled candidates. Reference BPM: {summary['reference_bpm']:.1f}.")
        if summary.get("focus"):
            st.caption(f"Second-pass discovery prioritised {', '.join(summary['focus'])} based on the first-pass audio scores; this does not guarantee better matches.")
        for rank, track in enumerate(scored[:10], 1):
            with st.container(border=True):
                left, right = st.columns([4, 1])
                with left:
                    st.write(f"**{rank}. {track['artist']} — {track['title']}**")
                    st.caption(f"Closest sonic characteristics: {', '.join(track['strengths'])} • estimated BPM: {track['bpm']:.1f} • discovered through {track.get('source_style', 'Jamendo')}")
                    with st.expander("Why did this track rank here?"):
                        st.caption("Acoustic feature similarity by category. These are heuristic scores, not listening-based accuracy ratings.")
                        for group in GROUPS:
                            value = float(track["components"].get(group, 0))
                            st.progress(min(1.0, max(0.0, value / 100)),
                                        text=f"{group}: {value:.1f}/100 • preference weight {summary['weights'][group]}/5")
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

with st.sidebar.expander("Recent discoveries (stored on this PC)"):
    past_searches = read_history(ROOT / "user-data" / "search-history.json")
    if not past_searches:
        st.caption("No recent searches. Only music metadata and scores are saved, never audio.")
    else:
        for item in past_searches[:5]:
            top = (item.get("matches") or [{}])[0]
            st.write(f"**{item.get('reference', 'Reference')}**")
            st.caption(f"{item.get('at_utc', '')[:10]} • {item.get('analysed', 0)} candidates • top match: {top.get('artist', 'None')} — {top.get('title', '')}")
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
