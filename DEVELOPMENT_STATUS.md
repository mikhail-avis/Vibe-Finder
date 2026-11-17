# Vibe Finder 0.6.0 — development checkpoint

Built on the verified published GitHub v0.5.1 application, including automatic Jamendo ID setup work from the unpublished 0.5.2 staging release.

## Implemented
- Three selectable discovery catalogues: Jamendo, ccMixter and Wikimedia Commons
- Every candidate must expose licence metadata and a trustworthy, downloadable audio URL
- Bounded, redirect-validated, size-limited temporary audio downloads (no music API stream ripping)
- Existing per-track 50-second waveform DSP, weighted feature scoring, adaptive style search and fingerprint caching shared across all sources
- Fair budget splitting across selected catalogues and source/provider labels on match results
- Jamendo ID preference logic: local env override, saved override, built-in default (no user input needed)
- Earlier quick sound-level test, Windows device selection and self-update functionality preserved

## Verification
- Automated suite includes real synthetic WAV generation, mocked source APIs / downloaded audio, comparison scoring and fingerprint cache checks
- Test suite does not validate live API response shapes, remote providers' availability, Windows device performance or human-rated match accuracy
- Live provider validation is needed after installation on a Windows machine

## Safety
- No raw track archive, music files or local settings are intentionally published to GitHub
- Raw source audio is held temporarily and deleted; only derived fingerprints and local match history persist
- Online update ZIP is tested before being pushed; update feed must be changed only after validating GitHub's published ZIP checksum
- Personal folders `library`, `user-data`, `updates`, `.venv` and the launcher are protected by the updater
