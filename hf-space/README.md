---
title: Spanish Video Dubber
emoji: 🎙️
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# Spanish Video Dubber

Upload an English video, get it back speaking Spanish — with the original music and
sound effects intact, Spanish subtitles, and a Spanish title and description ready
to paste into the upload form.

Everything runs inside the Space. No API keys, no accounts, nothing metered:

| Step | Tool |
| --- | --- |
| English speech → text | faster-whisper |
| English → Spanish | Argos Translate (offline) |
| Spanish → speech | edge-tts |
| Voice / music separation | Demucs |
| Everything else | ffmpeg |

Processing takes several minutes and the free CPU tier runs one job at a time.
Turning off "keep the original music" skips Demucs and is roughly four times faster.

**Upload only videos you own or have permission to use.** Dubbing a video does not
grant rights to it, and Content ID matches the picture regardless of the audio.
