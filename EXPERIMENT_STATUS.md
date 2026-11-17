# Vibe Finder — experiment/audio-embeddings-benchmark

## Goal

An independent measurable audio similarity bake-off: current production DSP,
LAION CLAP music checkpoint, Essentia Discogs track embeddings. This branch
is **not a published application update**; live Vibe Finder remains v0.6.0.

## Completed

- New opt-in model adapters: `experiments/audio_models.py` (DSP, CLAP, Essentia).
- CLAP consumes actual 48 kHz 10-second mono audio windows; normalises/averages
  patch embeddings. The music checkpoint must be provided locally.
- Essentia consumes actual 16 kHz mono audio and pools Discogs track embeddings;
  requires TensorFlow Essentia in a supported environment and a local graph.
- Reproducible evaluation CLI: `python -m experiments.audio_benchmark`.
- Explicit human relevance ratings 0..3, held identical across backends.
- NDCG@K, precision@K, per-query rankings, timings, cache reuse/invalidation.
- Clear `unavailable` status instead of pretending optional models ran.
- 57 automated offline tests pass (44 prior + 13 new).

## Not completed — must not claim

- No real pretrained CLAP or Essentia weights installed or inferred here.
- No comparisons against a human-rated corpus of real songs yet.
- No evidence yet that any AI embedding beats DSP at finding similar music.
- No Windows/WSL deployment testing of model installation or performance.
- No changes to live Streamlit UI, updater feed, or published releases.
- No permission/licence determination for commercial redistribution of models.

## Next actions

1. Obtain and verify appropriately licensed pretrained model files (size,
   upstream checksum, model card) in optional isolated environments.
2. Build a human-rated benchmark with real, lawfully acquired music excerpts.
3. Run the exact same dataset against each model, compare per-query NDCG@3,
   precision@3, processing time and RAM/VRAM.
4. Only after a reproducible win, decide whether to put a model into
   Vibe Finder's production search pipeline. Preserve an easy DSP fallback.