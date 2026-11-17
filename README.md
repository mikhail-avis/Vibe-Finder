# Vibe Finder — Windows prototype 0.3 (updates supported)

**Benchmark:** HXVRMXN — ECLIPSE (Night Drive Phonk), https://www.youtube.com/watch?v=JD0Ve7psY_I

## What you can do

- **Normal mode:** paste a YouTube link and click **Find music**. Vibe Finder identifies the video via YouTube's oEmbed information and searches public music catalogue *metadata* for discovery suggestions. **This is not actual acoustic similarity.**
- **Advanced mode:** compare authorised reference audio against local audio files using rhythm, bass, timbre, atmosphere and energy estimates. The results are heuristic, not proven sound-alike probabilities.

## First-time Windows setup

1. Install Python 3.11 or 3.12 if needed; tick **Add Python to PATH**.
2. Extract this ZIP once into a **permanent** folder, such as `C:\AI\Vibe-Finder`. **Don't delete the folder after you open it.**
3. Double-click `run-windows.bat`. First launch creates `.venv` and installs free packages.
4. Open http://localhost:8501 if your browser doesn't open automatically.
5. Paste a YouTube link and press **Find music**. Reference audio is not needed in normal mode.

## Updating without reinstalling

This version adds an **App updates** panel in the left sidebar. Updates are applied to the **same installed folder**; your `library` folder, `.venv`, saved settings, and `update-source.json` aren't replaced.

**Update ZIP (working now):** When a newer official Vibe Finder release ZIP is available, open **App updates**, upload the ZIP, click **Prepare this update**, then close Vibe Finder and double-click your existing `run-windows.bat`. The launcher installs the staged update **before** reopening the app. If the required packages change, the launcher installs updated dependencies on the next launch. You do **not** extract the update ZIP or reinstall the app.

**Online updates (ready for a release host, not live yet):** The panel also has **Check for online updates**. `update-source.json` in this package is preconfigured to the public GitHub update feed. Once `releases/latest.json` and the ZIP are published, the app can check for new versions. Then **Check for online updates** / **Download and prepare update** works without manually fetching ZIP files. A public GitHub repository hosts the free release feed. To point an older installation at the feed, put the HTTPS URL into `update-source.json` under `manifest_url`. The manifest must contain `app_id` (`vibe-finder`), `version`, `archive_url` (same HTTPS host), and `sha256` for the whole ZIP.

**Moving from older Vibe Finder 0.1/0.2:** This is a **one-time switch** to the updater-equipped version. Extract version 0.3 to a permanent folder and copy the old `library` contents to the new folder if you had local tracks. After that, use the updater rather than reinstalling future versions.

**Safety:** Official release ZIPs have a per-file checksum manifest; the updater rejects mismatches, attempts to overwrite personal folders, downgrades and path traversal, and backs up replaced files for rollback on failed copying. Remote download requires HTTPS and a matching SHA-256 hash; never install updates from an untrusted source. Restart the app before a staged update is applied. `run-windows.bat` stays untouched to maintain a safe launcher.

## Limitations

- YouTube links don't expose unrestricted audio downloads via YouTube's API. This prototype doesn't rip them.
- Apple's Search API supplies catalogue metadata and store listings only; previews aren't downloaded, played, or analysed.
- Discovery results are leads, **not measured audio matches**; click-through YouTube buttons search by artist/title.
- Online updates require the published ZIP and manifest in the public GitHub repository and a newer version number; no newer update is promised by the initial 0.3.0 publication.
- The Streamlit app has not been run or verified on the user's Windows PC.

## Test commands

`python -m unittest discover -s tests -v`
