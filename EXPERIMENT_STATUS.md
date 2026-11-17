# Experimental Audio Lab v0.7.1 — Music Link Capture

- Separate opt-in experimental installer, stable app remains v0.6.1.
- Reference and comparison independently accept uploaded WAV/MP3/FLAC/OGG/M4A
  or a public HTTPS music-page link.
- YouTube / YouTube Music / SoundCloud / Spotify / Bandcamp / Jamendo / Wikimedia
  and generic HTTPS music pages open in browser. No audio download or ripping.
- User explicitly captures 10, 20 or 30 seconds from a selected Windows WASAPI
  playback output (including supported Bluetooth); no microphone.
- One-second audio-level check and recorded-WAV playback preview.
- Both recordings flow to the real DSP/optional CLAP/Essentia analyser.
- In-memory session captures and short-lived temporary analysis WAVs.
- Experimental v0.7.1 update validates and can roll back to v0.7.0.
- Automated tests include URL safety, selected-output capture, decoded audio
  into the DSP analyser, channel isolation and rollback.

## Unverified

- Real Windows Bluetooth/speaker capture needs on-device confirmation.
- No live streaming-service integration, YouTube audio download or inference
  from a music link alone. Playback capture requires permission under site terms.
- No optional CLAP/Essentia pretrained weights bundled or run.

## Next

- Test capture and timing on actual Windows outputs; calibrate capture levels.
- Build a human-rated real-music dataset to compare DSP against AI models.
