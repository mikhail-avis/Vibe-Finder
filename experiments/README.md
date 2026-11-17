# Vibe Finder — acoustic model bake-off (EXPERIMENT, not a release)

**Purpose:** Compare the *same real sound recordings* using Vibe Finder's current
hand-engineered audio scorer, a local LAION CLAP music model, and Essentia's
Discogs **track** embedding model. Only promote a new scorer if genuine
human-rated music-matching results show a reproducible improvement.

Nothing here changes the live `app.py`, `candidate_pipeline.py`, updater feed, or
0.6.0 package. No candidate is scored using metadata or titles. The evaluation
uses actual audio waveform decoding for every available backend.

## Dependencies and limitations

- `dsp`: uses Vibe Finder's existing `librosa`, `numpy` and `soundfile` dependencies.
- `clap`: **optional**, `laion-clap` and its dependencies. The music checkpoint
  `music_audioset_epoch_15_esc_90.14.pt` is approximately **2.35 GB**. Download
  and verify a checkpoint separately, only from an authorised trusted source;
  Torch checkpoints can be executable pickle files. Model weights are NOT
  included in the repo or auto-downloaded. Experimental setup on Windows
  should use a **separate Python 3.11** virtual environment; performance on
  Windows and AMD GPUs is unverified, so expect CPU-only until tested.
- `essentia`: **optional**, `essentia-tensorflow` plus
  `discogs_track_embeddings-effnet-bs64-1.pb` in a supported Linux/macOS or
  WSL environment. Essentia's **TensorFlow-enabled Python wheels are not
  published for ordinary native Windows installs**. Do not present it as a
  one-click Windows model. Check Essentia's AGPLv3/commercial-licensing
  obligations and the specific model metadata before production distribution.
- The CLAP library code lists an Apache-2.0 licence. Check the independent
  model-weights metadata and upstream data restrictions before commercial use.
- Data must be recordings you have permission to decode and analyse. Do not
  download copyrighted audio from YouTube, Spotify or other restricted services.

No new dependency is added to the production `requirements.txt`.

## Prepare a real-music test set

Start with `examples/audio_benchmark_manifest.json` and **replace all fake
paths** with paths to actual locally available recordings. The manifest paths
are relative to the JSON file, and each `reference` must have at least two
*different* candidate files. Each candidate requires your own 0..3 relevance
judgment based on **how much it sounds like the reference**:

- 0: not alike
- 1: weakly similar
- 2: clearly similar
- 3: very similar

Aim for at least 20 references spanning different tempos, production styles,
voices and instrumentation, with five or more judged candidate songs each.
Include obvious decoys and genuinely similar alternatives. Never use the
identical audio file as both reference and candidate. Label before viewing any
model's scores to avoid bias. A human-rated ground truth is essential: a
synthetic test cannot establish that a model finds a better *vibe*.

## Run

From the repository root, using Python 3.11/3.12 and installed base project
requirements:

```powershell
python -m experiments.audio_benchmark --dataset examples\my_judgments.json --methods dsp --out dsp-report.json
```

To test both embeddings when the appropriate packages and checkpoint files have
been separately installed:

```powershell
python -m experiments.audio_benchmark --dataset examples\my_judgments.json --methods dsp,clap,essentia --clap-checkpoint C:\Models\music_audioset_epoch_15_esc_90.14.pt --essentia-graph C:\Models\discogs_track_embeddings-effnet-bs64-1.pb --out comparison-report.json
```

For Essentia, actually run this command in the WSL/Linux environment with a
Linux-accessible model path, rather than assuming the Windows path will work.
It is also fine to run only `dsp,clap` on Windows and only `essentia` under WSL;
the same manifest and judged songs must be used to compare the two reports.

Results report **NDCG@3** (respects 0..3 relevance grades), **precision@3**
(binary relevance threshold >= 2), per-query rankings and execution time.
CLAP/Essentia scores are cosine similarities on [-1,1], **not accuracy
percentages**. DSP scores remain its original 0..100 heuristic index. Compare
rankings and judged-quality metrics, never raw scores across methods.

An unavailable model appears as `unavailable` with its reason and **does not
silently fall back to DSP**. Failed/partial evaluations are separately marked.
Local feature caches use file size, modification timestamp, model-file identity
and analysis settings as keys. Raw music is never stored in the cache/report.
File paths do appear in internal cache keys only as cryptographic hashes.

## Interpretation policy

- Synthetic WAV tests validate plumbing, audio decoding and score computation.
  They **do not measure music matching accuracy**.
- A genuine model win requires consistent improved NDCG/precision across real
  references (not just one convenient example), tolerable latency and licence
  clearance. Keep per-query comparisons, including cases where a model fails.
- Optional optimisations (FAISS, fusion weighting, production search UI) come
  **after** the offline benchmark has proven an improvement.

Upstream references:
- https://github.com/LAION-AI/CLAP
- https://essentia.upf.edu/models.html
- https://essentia.upf.edu/machine_learning.html
- https://essentia.upf.edu/installing.html

## Experimental 0.7.1: music URLs via real playback capture

The experimental Streamlit lab accepts **Upload audio** or **Music link** independently
for the reference and comparison songs. Recognised services include YouTube,
YouTube Music, SoundCloud, Spotify, Bandcamp, Jamendo and Wikimedia Commons; other
public HTTPS music-page links are accepted as browser destinations. Links are
**never** treated as audio or fetched from streaming services.

On **Windows**, choose an available speaker or Bluetooth output, play the link in
your browser, then explicitly capture 10, 20 or 30 seconds using Windows WASAPI
loopback. Repeat for the second track and select **Analyse and compare actual
audio**. The audio lab offers a 1-second signal check and playback preview of
each captured sample, so you can confirm the intended song was recorded before
comparing. Recording duration does not include Windows device startup time.

Audio bytes remain in Streamlit session memory until discarded/session end; the
backend writes temporary WAV files only during comparison, then deletes them.
YouTube/Spotify audio is not downloaded, ripped or inferred from metadata. Use
only playback you're authorised to analyse, and respect each site's terms.
This is a Windows-only capture path, not an automatic YouTube-to-waveform API.

CLAP and Essentia model weights remain separate optional dependencies. DSP works
with the standard installation and provides the baseline acoustic score.
