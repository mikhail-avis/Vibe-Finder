"""Vibe Finder: link-first discovery and optional local acoustic matching."""
from __future__ import annotations

import tempfile
from pathlib import Path
from urllib.parse import quote_plus

import streamlit as st

from discovery import BENCHMARK_URL, discover_tracks, get_video_title, youtube_id
from engine import GROUPS, analyse, compare, library_features
from version import APP_VERSION
from updater import UpdateError, check_online, stage_online, stage_package

ROOT = Path(__file__).resolve().parent

st.set_page_config(page_title="Vibe Finder • Find the vibe", page_icon="🎧", layout="wide")
st.title("🎧 Vibe Finder")
st.caption(f"Prototype {APP_VERSION} • Paste a YouTube link to discover music. No reference file required.")

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

st.subheader("Your reference song")
reference_url = st.text_input("Paste a YouTube link", value=BENCHMARK_URL)
if youtube_id(reference_url):
    st.link_button("Listen on YouTube", f"https://www.youtube.com/watch?v={youtube_id(reference_url)}")

with st.expander("Advanced: optional real-audio matching"):
    st.write("Link-only discovery searches catalogue metadata. To compare actual bass, rhythm, atmosphere and energy, supply an audio file you are authorised to analyse and a folder of candidate tracks.")
    reference_upload = st.file_uploader("Reference audio (optional)", type=["wav", "flac", "mp3", "ogg", "m4a"])
    folder = st.text_input("Folder of candidate audio files", value=str(ROOT / "library"))
    cols = st.columns(5)
    starting = {"Rhythm": 5, "Bass": 5, "Timbre": 4, "Atmosphere": 4, "Energy": 3}
    weights = {name: col.slider(name, 0, 5, starting[name]) for name, col in zip(GROUPS, cols)}

st.caption("**Link-only results are genre/metadata-based discovery leads, not verified sound-alikes.** Genuine acoustic comparisons still need authorised reference audio or a licensed/pre-analysed feature source.")

if st.button("Find music", type="primary", use_container_width=True):
    if not youtube_id(reference_url):
        st.error("Please enter a valid YouTube video link first.")
        st.stop()

    with st.spinner("Identifying the linked track…"):
        try:
            title = get_video_title(reference_url)
        except (ValueError, LookupError) as exc:
            st.error(str(exc))
            st.stop()
    st.write(f"**Identified:** {title}")

    if reference_upload is None:
        st.info("Link-only mode: looking for songs in a public music catalogue. These results have **not** been compared by their audio.")
        with st.spinner("Searching music metadata…"):
            tracks, issues = discover_tracks(title, limit=10)
        if tracks:
            st.subheader("Discoveries to try")
            for n, item in enumerate(tracks, 1):
                with st.container(border=True):
                    left, right = st.columns([3, 1])
                    with left:
                        st.write(f"**{n}. {item['artist']} — {item['track']}**")
                        if item["genre"]:
                            st.caption(f"Store genre: {item['genre']} • not an audio similarity score")
                    with right:
                        st.link_button("Find on YouTube", item["youtube_search_url"])
                        st.link_button("Store listing", item["store_url"])
            st.caption("These are catalogue search leads, not a ranked analysis of actual sound. YouTube buttons search by artist and title and may return several results.")
        else:
            st.warning("No catalogue discoveries came back for this track. This doesn't mean no similar songs exist. Try a later run or use Advanced audio matching.")
        if issues:
            with st.expander("Catalogue connection details"):
                st.write("\n".join(issues))
    else:
        if not Path(folder).expanduser().is_dir():
            st.error("The candidate library folder doesn't exist. Enter a valid folder in Advanced settings.")
            st.stop()
        if not any(weights.values()):
            st.error("Turn up at least one of the five weights.")
            st.stop()
        try:
            with tempfile.TemporaryDirectory() as scratch:
                ext = Path(reference_upload.name).suffix.lower()
                target = Path(scratch) / f"reference{ext}"
                target.write_bytes(reference_upload.getvalue())
                ref_features = analyse(target)
        except Exception as exc:
            st.error(f"Reference audio could not be analysed: {exc}")
            st.stop()

        bar = st.progress(0, text="Scanning library")
        def update(done, total):
            bar.progress(done / max(total, 1), text=f"Analysing files: {done}/{total}")
        with st.spinner("Comparing actual audio…"):
            indexed, errors = library_features(folder, progress=update)
        bar.empty()
        if errors:
            with st.expander(f"{len(errors)} file(s) couldn't be analysed"):
                st.write("\n".join(errors))
        if not indexed:
            st.warning("No supported audio files in the candidate library yet.")
        else:
            scored = [{**item, **compare(ref_features, item["features"], weights)} for item in indexed]
            scored.sort(key=lambda item: item["score"], reverse=True)
            st.success(f"Compared reference audio against {len(scored)} local tracks (estimated reference BPM: {ref_features['bpm']}).")
            st.subheader("Closest local audio matches")
            for rank, item in enumerate(scored[:10], 1):
                with st.container(border=True):
                    left, right = st.columns([3, 1])
                    with left:
                        st.write(f"**{rank}. {item['path'].stem}**")
                        st.caption(f"Closest features: {', '.join(item['strengths'])} • estimated BPM: {item['features']['bpm']}")
                        st.audio(str(item["path"]))
                    with right:
                        st.metric("Heuristic match index", f"{item['score']:.1f}/100")
                        st.link_button("Search YouTube", "https://www.youtube.com/results?search_query=" + quote_plus(item["path"].stem))
            st.caption("Audio match scores are heuristic indices, not probabilities or a validated semantic similarity measure.")

st.divider()
st.caption("YouTube links are used for track identification and listening—not audio ripping. The link-only catalogue lookup uses Apple's Search API for metadata and store links only; no Apple preview audio is downloaded or analysed. Advanced matching stays on your PC.")
