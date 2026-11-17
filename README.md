# Vibe Finder 0.5.1 — acoustic music discovery (development build)

Vibe Finder analyses real audio to suggest similar-sounding tracks from a sampled set of eligible Jamendo music. Scores are heuristic acoustic similarity indices, not probabilities or proof of shared musical style.

## What's new in 0.5

- **Adaptive discovery:** the selected track budget is split into a broad Jamendo scan and an optional second pass targeting styles that performed best against the reference in the first *actual audio* scan. A fallback searches other styles if focused results are unavailable; tracks never get counted twice. The search is NOT catalogue-wide or machine-learned recommendation.
- **Fair sampling:** collection across genres is round-robin instead of always filling from the first tags; up to four Jamendo metadata requests overlap.
- **Acoustic matching:** added short-lag attack recurrence and low-frequency pulse variation to existing rhythm/bass features. Cached fingerprints use a new version key and are recalculated on demand.
- **Explainable rankings:** expand a match to see each of the five acoustic group scores and the weights selected for the search. All similarity scores remain heuristics and require real-music validation.
- **Local search history:** recent reference labels, weights, and ranked match metadata are stored in `user-data/search-history.json`, with a Clear button. Raw audio and Jamendo Client IDs are never written to this history.
- **Playback diagnostics:** signal peak meter after the three-second device check (not a continuously updating live graph).
- **Updater reliability:** online version checks include a unique cache-busting query and no-cache headers, plus installed/remote version diagnostics.

When published on GitHub, install using **App updates > Check for online updates > Download and prepare update**, then close and relaunch `run-windows.bat`.

## Choose one reference song

- **Upload audio:** Provide a WAV, FLAC, MP3, OGG or M4A file that you have permission to analyse. The app extracts a temporary audio fingerprint, then searches for eligible Jamendo candidate tracks and ranks them by actual audio features.
- **Paste a YouTube link:** A YouTube URL identifies a song, but does not provide downloadable audio through the standard YouTube API. On Windows, **Listen Live** can, with your permission and subject to the audio service's terms, capture a 20-second sample of the computer's digital speaker output. It does not use the microphone, but other computer sounds may be present. A URL alone cannot trigger acoustic matching.
- **Metadata search fallback:** A separate tool finds title/genre-based leads. They are **not** audio comparisons.

## Windows Listen Live: choosing speakers or Bluetooth headphones

1. In Windows, connect your Bluetooth headphones/speaker, USB headset or speakers and start playing music through them.
2. In Vibe Finder, choose **Paste a YouTube link** as the reference input. Under **Windows playback device**, select **Refresh audio devices** to discover available WASAPI playback outputs.
3. Under **Listen to audio playing through**, pick the output that Windows is actually sending the music to. The Windows default output is labelled. Bluetooth stereo and Bluetooth hands-free devices may be shown separately; prefer the **stereo playback** output where music is playing.
4. Click **Quick sound test (1-second audio sample)** while the song is playing. If the test detects a signal, press **Listen Live: capture 20 seconds**. This listens to the selected output only; it doesn't activate a microphone.
5. If silent, open **Windows Settings > System > Sound > Volume mixer** and check which output your music app uses. Switch the app's output if needed, reconnect Bluetooth, then **Refresh audio devices** and retry. Devices not available in Windows WASAPI cannot be captured by this method.

The signal test reports peak percentage and RMS dBFS. It checks whether a digital signal was recorded, not whether the music is the intended song. Notifications and other computer sounds sent to the same output can also be included.

## Jamendo catalogue

Create an app at https://devportal.jamendo.com/ and enter its public **Client ID** in Vibe Finder's sidebar. The ID is saved in `user-data/jamendo.json` on your own computer, not in source control. Do not enter your Jamendo password or secret. In **Jamendo catalogue settings**, select **Test Jamendo connection (metadata only)** to confirm the API accepts your ID; this does not download music or prove sound comparison works.

Jamendo's API is used to discover candidates across several styles. Only tracks marked `audiodownload_allowed` with a trusted Jamendo download URL are sampled. Candidate audio is stored temporarily, analysed, and deleted; only compact feature fingerprints are cached locally. Licence and service permissions still apply. **This is not a search of all music on Jamendo, YouTube or Spotify.**

## Performance model

Vibe Finder can download up to **four** eligible candidate tracks concurrently and analyse up to **two** tracks in separate processes, subject to queue back-pressure so downloads cannot create an unlimited pile of temporary files. Provisional best matches appear as completed tracks arrive. Cache hits bypass network downloads and analysis. Actual speed depends on track length, connection, CPU and catalogue response times.

The offline benchmark uses generated WAV tracks, not Jamendo downloads. Run `python scripts/bench_pipeline.py --tracks 16 --seconds 15 --latency-ms 250` to compare one worker against 4 download / 2 analysis workers on your machine.

## Windows setup and updates

- Supported development targets: Python 3.11 or 3.12 on Windows; first-time installs require a working Python installation added to PATH.
- For a full first-time installation, extract the full app distribution to a persistent folder such as `C:\AI\Vibe-Finder`, then double-click `run-windows.bat` and open http://localhost:8501.
- If you're on Vibe Finder 0.3, **do not extract a partial release ZIP over your installation**. Instead, when an official newer version is available, open **App updates**, upload the release ZIP, click **Prepare this update**, close the app, and restart with the existing `run-windows.bat` launcher.
- The updater safeguards personal folders and settings, checks per-file hashes and stages updates before restart. Only use releases from a trusted source.

## Current limitations and verification

- Jamendo's accessible sample may not include niche tracks or provide good stylistic matches. No candidate is ranked without actual audio analysis, but rankings are not yet calibrated against human similarity judgements.
- The model does not automatically acquire a YouTube waveform; opt-in live capture is a separate Windows capability.
- The 0.5 development build has passed local unit tests, including mocked Windows output selection/capture tests, and synthetic-audio benchmarks. Actual Windows output capture and Jamendo end-to-end playback are not verifiable in this Linux environment. A **real Jamendo API and download test**, plus Windows WASAPI capture, still require validation on a Windows machine. The development environment cannot reach Jamendo live.
- This README describes a development build; the GitHub update feed publishes version 0.5.1 after the archive is uploaded.

Run tests with `python -m unittest discover -s tests -v`.

## Updater cache fix in 0.5.0

GitHub Raw can temporarily serve a cached `releases/latest.json` after a new version has been published, causing older Vibe Finder apps to incorrectly report **Up to date**. Version 0.5.0 appends a unique cache-busting query to every manifest fetch and sets no-cache request headers. The updater reports both the installed version and the version supplied by the feed. Version 0.4.1 or earlier may require one manual official update ZIP to acquire this fix; it does not rewrite protected local settings or the launcher.

## Quick audio diagnostics (0.5.1)

The former three-second **Test selected device** now records **one second** of audio.
Windows WASAPI stream initialization, processing, and Streamlit rerendering add
wall-clock time. The app now reports how long the audio check itself took,
including opening the device, and distinguishes this from recording duration.

The output uses **No sound detected**, **Sound detected, but very quiet**,
**Sound detected on this output**, or **Sound detected — possibly too loud**.
Those are approximate signal-level diagnostics, *not* song recognition or proof
that the intended music is captured. Technical readings are behind an expander.
For a proper reference fingerprint, keep only the intended track playing before
using the separate 20-second Listen Live capture.
