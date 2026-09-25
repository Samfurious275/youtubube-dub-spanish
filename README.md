# youtube-dub

Turns an English video into a Spanish one. The English voice is replaced with a
Spanish voice, the original music and sound effects are kept, and you get Spanish
subtitles plus a Spanish title and description for the upload form.

Everything runs on your own machine. No API keys, no accounts, no paid services,
nothing uploaded anywhere.

**What you get back**

| File | What it is |
| --- | --- |
| `<name>.es.mp4` | The video, speaking Spanish |
| `<name>.es.srt` | Spanish subtitles |
| `<name>.en.srt` | English subtitles |
| `<name>.es.txt` | Spanish title and description |

---

## Before you start

You need **Python 3.11** specifically. Not 3.12, not 3.13 — the machine-learning
libraries this uses have no builds for newer versions yet.

Everything else (`ffmpeg`, `yt-dlp`, and all Python packages) installs itself the
first time you run it.

Budget **about 2 GB of disk** and 15–20 minutes for that first run. After that,
starting up takes seconds.

---

## Setup — macOS and Linux

### 1. Install the basics

macOS, using [Homebrew](https://brew.sh):

```bash
brew install git python@3.11
```

Linux (Debian/Ubuntu):

```bash
sudo apt update
sudo apt install -y git python3.11 python3.11-venv ffmpeg fonts-dejavu-core
```

If `python3.11` is not available on your release (Ubuntu 24.04 ships 3.12), add
the deadsnakes archive first:

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.11 python3.11-venv
```

### 2. Clone the repository

```bash
git clone https://github.com/Samfurious275/youtubube-dub-spanish.git
cd youtubube-dub-spanish
```

### 3. Start it

```bash
chmod +x serve.sh
./serve.sh
```

The first run installs everything and prints a lot of output — this is normal and
takes 15–20 minutes. When it finishes, your browser opens at
**http://127.0.0.1:7860** by itself.

Every run after this starts in seconds.

---

## Setup — Windows

### 1. Install Python 3.11

Download it from
[python.org/downloads/release/python-3119](https://www.python.org/downloads/release/python-3119/)
and run the installer.

> **Tick "Add python.exe to PATH"** on the first screen of the installer. Nothing
> works without it, and it is off by default.

Check it worked — open **Command Prompt** and run:

```bat
py -3.11 --version
```

You should see `Python 3.11.9`.

### 2. Install Git

Download from [git-scm.com/download/win](https://git-scm.com/download/win) and
accept the defaults.

### 3. Clone the repository

```bat
cd %USERPROFILE%\Downloads
git clone https://github.com/Samfurious275/youtubube-dub-spanish.git
cd youtubube-dub-spanish
```

### 4. Start it

```bat
serve.bat
```

You can also just double-click `serve.bat` in File Explorer.

> **If it installs ffmpeg**, it will tell you to close the window and open a new
> one. Do that, then run `serve.bat` again. Windows only notices newly installed
> programs in terminals opened afterwards.

The first run takes 15–20 minutes. When it finishes, your browser opens at
**http://127.0.0.1:7860**.

---

## Using it

Once the page is open:

1. **Choose your video** in the box on the left, or drag a file onto it.
2. **English title** — optional. Used to write the Spanish title. Left blank, the
   filename is used instead.
3. **Spanish voice** — four to choose from, male and female, Latin American and
   European.
4. **Burn Spanish subtitles into the picture** — on by default. This also paints
   over any English captions baked into the video, which a subtitle file cannot do.
5. **Keep the original music and sound effects** — on by default. Unticking it is
   about four times faster but throws away all background audio.
6. **Transcription quality** — `small` is a good default. `base` is roughly three
   times faster and less accurate.
7. Press **Dub it**.

Then wait. The results appear on the right: the dubbed video, the Spanish title
and description with copy buttons, and both subtitle files to download.

### It is not frozen

A 2.5-minute video takes roughly 13 minutes. The progress bar moves between
stages, not inside them, so it sits still for minutes at a time — transcription
alone is about four minutes with no visible movement.

**Try a 20-second clip first.** It proves the whole thing works in about two
minutes.

### Stopping it

Press `Ctrl+C` in the terminal, or just close the window.

---

## Sharing the page with someone else

```bash
./serve.sh --share --password something-hard
```

```bat
serve.bat --share --password something-hard
```

This prints a public `https://...gradio.live` link that works from anywhere for
72 hours. Your machine does the work, so it must stay awake.

The link asks for a login. **The username is `dub`** unless you change it with
`--user`; the password is whatever you passed. Both are printed in the terminal
when the server starts, so check there if the page says *Incorrect Credentials*.

Always set a password. Without one, anyone with the link can queue jobs on your
computer.

---

## Command line

If you would rather not use the web page, and want to dub straight from a YouTube
URL:

```bash
./run.sh "https://www.youtube.com/watch?v=XXXXXXXXXXX"          # macOS / Linux
```

```bat
run.bat "https://www.youtube.com/watch?v=XXXXXXXXXXX"
```

Any YouTube link shape works — `watch?v=`, `youtu.be/`, `/shorts/`, or a bare
video ID. Results land in the `output/` folder.

Useful options:

```bash
./run.sh "URL" --burn                    # draw the subtitles into the picture
./run.sh "URL" --no-bg                   # drop the original audio (much faster)
./run.sh "URL" --voice es-ES-AlvaroNeural
./run.sh "URL" --whisper-model base      # faster, less accurate
./run.sh --list-voices                   # show every Spanish voice
./run.sh "URL" --help                    # everything else
```

---

## How long it takes

Measured on a 2018 MacBook Pro (6-core i7) with a 151-second video:

| Stage | Time |
| --- | --- |
| Transcribing the English | 245 s |
| Translating | ~30 s |
| Speaking the Spanish | ~90 s |
| Separating voice from music | ~200 s |
| Rendering the video | ~240 s |
| **Total** | **~13 min** |

As a rule of thumb, **five minutes of work per minute of video**. A faster
processor helps almost linearly, since nearly all of it is CPU-bound.

To make it faster: untick "keep the original music" (saves about a quarter), and
set transcription quality to `base`.

---

## If something goes wrong

**`python3.11: command not found` / `py -3.11` not recognised**
Python 3.11 is not installed, or "Add to PATH" was not ticked on Windows. Reinstall
it, ticking that box.

**Windows: `ffmpeg is not recognized`**
Close the terminal, open a new one, and run `serve.bat` again.

**The page says "Connection errored out"**
The server stopped. Start it again with `./serve.sh` or `serve.bat`, and open a
fresh browser tab rather than reloading the old one.

**Port 7860 is already in use**
Something else is on that port, or a previous run is still going:

```bash
./serve.sh --port 7870
```

**"No TrueType font found"**
Only on Linux. Install the font the subtitles are drawn with:

```bash
sudo apt install fonts-dejavu-core
```

**The first run seems stuck**
It is downloading about 1 GB of models. Leave it. This only happens once.

---

## Notes

- Work is cached per video in `work/`, so re-running the same video only redoes
  what changed. Delete that folder to start clean.
- The `.venv` folder is about 1.2 GB. It is rebuilt automatically if you delete it.
- The translation is offline and free, which means it is decent but not perfect —
  idioms come out literally. Read the Spanish title before you publish it.
- **Only dub videos you own or have permission to use.** Dubbing someone else's
  video does not give you any rights to it, and YouTube's Content ID matches the
  picture regardless of what language the audio is in.
