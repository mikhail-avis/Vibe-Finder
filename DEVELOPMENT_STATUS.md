# Vibe Finder 0.5.0 - development status

Built from 0.4.2 updater-fix staging plus the verified 0.4.1 GitHub source.

## Implemented
- Adaptive broad/focused acoustic discovery bounded by user-selected candidate budget
- Fair round-robin Jamendo candidates and up to four concurrent metadata requests
- Rhythm short-lag attack recurrence and bass modulation descriptors; fingerprint cache version v2
- Explained per-category scores and weights
- On-PC metadata-only search history with clear button
- Playback peak meter for 3-second test
- GitHub Raw manifest cache-busting and diagnostic versions

## Test results
- 27 automated tests passing on Linux/Python 3.13; unit tests, mocked network, real synthetic WAV analysis, simulated 0.4.1-to-0.5 upgrade, mocked Windows WASAPI playback device selection
- Tests do not demonstrate quality improvement on real songs or compatibility with real Jamendo servers and real Windows audio hardware
- Version 0.5 is a prototype; do not represent heuristic match score as validated accuracy

## Outstanding
1. Actual Windows install/update and 3-second playback test (speakers and Bluetooth)
2. Live Jamendo metadata and downloads with user's local Client ID (never committed)
3. Compare search ranking against a human-rated real recording similarity set
4. Investigate refined segment-based matching/ML embeddings after evaluation

## Safeguards
- Preserve `user-data`, `library`, `update-source.json` and `run-windows.bat` when updating
- Publish archive and code before switching `releases/latest.json`
- Do not save raw copyrighted audio or user's Client ID to GitHub
