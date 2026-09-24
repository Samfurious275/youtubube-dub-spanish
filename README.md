# youtube-dub

Give it a YouTube URL and it gives you back the same video speaking Spanish, with
Spanish subtitles, keeping the original music and sound effects.

Everything runs locally — no API keys, no paid services.

## How it works

1. Downloads the video with `yt-dlp`
2. Transcribes the English speech with Whisper (local) into timed segments
3. Translates every segment to Spanish with Argos Translate (local)
4. Speaks the Spanish with a free Edge neural voice, one clip per segment
5. Fits each clip into its original time slot so the dub stays in sync with the
   picture (Spanish runs ~20% longer than English, so clips get sped up as needed)
6. Splits the original audio with Demucs, throws away the English voice, keeps the
   music/SFX bed, then ducks that bed underneath the Spanish voice
7. Muxes it back together → `output/<title>.es.mp4` + `.es.srt` + `.en.srt`

## Requirements

- macOS/Linux with Homebrew, or Windows
- Python 3.11
- `ffmpeg` and `yt-dlp` (installed automatically by `run.sh`)

## Usage

`run.sh` (macOS/Linux) and `run.bat` (Windows) take the same arguments and install
everything on first run.

```bash
./run.sh "https://www.youtube.com/watch?v=XXXX"

./run.sh "URL" --burn                 # Spanish subtitles drawn into the picture,
                                      #   painting over any burned-in English ones
./run.sh "URL" --voice es-ES-AlvaroNeural --whisper-model medium
./run.sh "URL" --no-bg                # drop the original audio entirely (skips Demucs)
./run.sh --list-voices                # show every Spanish voice you can pick
```

Without `--burn` the Spanish is a selectable subtitle track, which players may not
turn on by themselves, and it cannot replace captions baked into the video.

## Notes

- Work is cached in `work/<video>/`, so re-running only redoes what changed.
- The first run downloads the Whisper and Argos models (~1 GB) and is much slower
  than later ones.
- On Intel Macs, `torch` 2.2.2 is the last release with x86_64 macOS builds, which
  is why `requirements.txt` pins it.
