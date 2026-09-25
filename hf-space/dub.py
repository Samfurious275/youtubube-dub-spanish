#!/usr/bin/env python3
"""
English -> Spanish video dubber.

Give it a YouTube URL and it gives you back the same video speaking Spanish,
with Spanish subtitles, keeping the original music and sound effects.

  1. Downloads the video with yt-dlp
  2. Transcribes the English speech with Whisper (local, no API key) -> timed segments
  3. Translates every segment to Spanish with Argos Translate (local, no API key)
  4. Speaks the Spanish with an Edge neural voice (free, no API key), one clip per segment
  5. Fits each clip into its original time slot so the dub stays in sync with the picture
     (Spanish runs ~20% longer than English, so clips get sped up as needed)
  6. Splits the original audio with Demucs, throws away the English voice and keeps
     the music/SFX bed, then ducks that bed underneath the Spanish voice
  7. Muxes it all back together -> output/<title>.es.mp4 + .es.srt + .en.srt

Usage (macOS/Linux ./run.sh, Windows run.bat -- same arguments):
  ./run.sh "https://www.youtube.com/watch?v=XXXX"
  ./run.sh "URL" --burn                 # Spanish subtitles drawn into the picture,
                                        #   painting over any burned-in English ones
  ./run.sh "URL" --voice es-ES-AlvaroNeural --whisper-model medium
  ./run.sh "URL" --no-bg                # drop the original audio entirely (skips Demucs)
  ./run.sh --list-voices                # show every Spanish voice you can pick

Without --burn the Spanish is a selectable subtitle track, which players may not
turn on by themselves, and it cannot replace captions baked into the video.

Everything is cached in work/<video>/, so re-running only redoes what changed.
The first run downloads the Whisper and Argos models (~1 GB) and is much slower than later ones.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

# Two Intel-macOS landmines, both set before the libraries that read them load.
#
# faster-whisper imports torch for its VAD, so torch's libiomp5 and ctranslate2's
# libiomp5 end up in one process and Intel's OpenMP aborts the run outright.
# Letting the duplicate through is the documented escape hatch; transcripts come
# back identical to a clean single-runtime run.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# Argos 1.11 defaults to a Stanza neural sentence splitter that hits the same clash
# from the other side and deadlocks at 0% CPU instead of aborting. Whisper already
# hands us roughly one sentence per segment, so the pure-python splitter loses
# nothing and is far faster.
os.environ.setdefault("ARGOS_CHUNK_TYPE", "MINISBD")

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "work"
OUTPUT = ROOT / "output"
SAMPLE_RATE = 48000
DEFAULT_VOICE = "es-MX-JorgeNeural"


# ---------------------------------------------------------------- helpers


def run(cmd, **kw):
    print("  $ " + " ".join(str(c) for c in cmd[:6]) + (" ..." if len(cmd) > 6 else ""))
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def ffmpeg(*args):
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args])


def ffmpeg_cached(out, *args):
    """Render to a temp name and rename into place.

    Everything under work/ is reused on the next run purely because the file is
    there, so a run killed midway through an encode would otherwise leave a
    truncated file that every later run trusts. Renaming is atomic, so a cached
    file is either complete or absent.
    """
    out = Path(out)
    tmp = out.with_name(f".partial-{out.name}")
    try:
        ffmpeg(*args, tmp)
        tmp.replace(out)
    finally:
        if tmp.exists():
            tmp.unlink()
    return out


def video_size(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        check=True, capture_output=True, text=True).stdout.strip()
    w, h = out.split("x")[:2]
    return int(w), int(h)


def duration_of(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True).stdout.strip()
    return float(out)


def slugify(text, fallback="video"):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:60].strip("-") or fallback


def load_json(path):
    return json.loads(path.read_text()) if path.exists() else None


def save_json(path, data):
    tmp = path.with_name(f".partial-{path.name}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    tmp.replace(path)          # same reason as ffmpeg_cached


# ---------------------------------------------------------------- 1. download


# Every spelling a video id shows up in: watch?v=, youtu.be/, /shorts/, /embed/,
# /live/. Anchored so a partial match inside a longer id can't slip through.
YT_ID = re.compile(
    r"(?:v=|/shorts/|/embed/|/live/|/v/|youtu\.be/)([0-9A-Za-z_-]{11})(?![0-9A-Za-z_-])")


def normalize_url(url):
    """Turn anything YouTube-shaped into a plain watch URL.

    Copying a Shorts link into a watch?v= link is an easy slip and yt-dlp just
    reports "Unsupported URL", so pull the id out and rebuild the link instead.
    Taking the last id handles exactly that nested case; a share ?si= tracker
    falls away with it. Anything unrecognised is passed through untouched so
    yt-dlp can still try non-YouTube sites.
    """
    ids = YT_ID.findall(url)
    if ids:
        return f"https://www.youtube.com/watch?v={ids[-1]}"
    if re.fullmatch(r"[0-9A-Za-z_-]{11}", url.strip()):
        return f"https://www.youtube.com/watch?v={url.strip()}"
    return url



VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov")


def find_source(d):
    """The merged download, never one of yt-dlp's per-stream leftovers.

    An interrupted download leaves files like source.f399.mp4 next to the finished
    source.mp4, and those sort first alphabetically, so picking by glob order could
    quietly hand back a video-only or audio-only fragment.
    """
    merged = d / "source.mp4"
    if merged.exists():
        return merged
    return next((p for p in sorted(d.glob("source.*"))
                 if p.suffix.lower() in VIDEO_EXTS and ".f" not in p.stem), None)


def download(url, d, max_height=1080):
    """Fetch the video once; later runs reuse what is already in work/.

    The picture is copied through untouched, so whatever yt-dlp grabs is what you
    upload. Left uncapped it will happily take a 2160x2160 60fps AV1 and hand back
    180 MB for a two-minute short, so ask for something sane and fall back only if
    the video has nothing smaller.
    """
    video = find_source(d)
    if video is None:
        fmt = (f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/"
               f"bestvideo[height<={max_height}]+bestaudio/"
               f"best[height<={max_height}]/best")
        run(["yt-dlp", "--no-playlist", "--write-info-json",
             "-f", fmt,
             "--merge-output-format", "mp4",
             "-o", str(d / "source.%(ext)s"), url])
        video = find_source(d)
        if video is None:
            sys.exit("yt-dlp finished but left no video file behind.")
    else:
        print(f"  cached download: {video.name}")

    info = load_json(d / "source.info.json") or {}
    return video, info.get("title") or video.stem, info


def extract_speech_audio(video, d):
    """16 kHz mono is what Whisper wants."""
    wav = d / "speech16k.wav"
    if not wav.exists():
        ffmpeg_cached(wav, "-i", video, "-vn", "-ac", "1", "-ar", "16000")
    return wav


# ---------------------------------------------------------------- 2. transcribe


def transcribe(wav, d, model_size):
    cache = d / f"transcript.{model_size}.json"
    cached = load_json(cache)
    if cached:
        print(f"  cached transcript: {len(cached)} segments")
        return cached

    from faster_whisper import WhisperModel

    print(f"  loading whisper '{model_size}' (first run downloads the model) ...")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    try:
        segments, _ = model.transcribe(
            str(wav), language="en", vad_filter=True, beam_size=5,
            vad_parameters={"min_silence_duration_ms": 400})
        segments = list(segments)
    except Exception as e:                      # onnxruntime missing / VAD failure
        print(f"  voice-activity filter unavailable ({e}); transcribing without it")
        segments, _ = model.transcribe(str(wav), language="en", beam_size=5)
        segments = list(segments)

    out = []
    for s in segments:
        text = s.text.strip()
        if text:
            out.append({"start": round(s.start, 3), "end": round(s.end, 3), "en": text})
    if not out:
        sys.exit("Whisper found no speech in this video.")
    save_json(cache, out)
    print(f"  {len(out)} segments transcribed")
    return out


# ---------------------------------------------------------------- 3. translate


def ensure_argos(from_code="en", to_code="es"):
    import argostranslate.package as pkg

    if any(p.from_code == from_code and p.to_code == to_code
           for p in pkg.get_installed_packages()):
        return
    print("  downloading the offline en->es translation model (first run only) ...")
    pkg.update_package_index()
    match = next((p for p in pkg.get_available_packages()
                  if p.from_code == from_code and p.to_code == to_code), None)
    if match is None:
        sys.exit("No Argos en->es package available; check your internet connection.")
    pkg.install_from_path(match.download())


SENTENCE_END = re.compile(r"(?<=[.!?…])[\"')\]]*\s+")
SENTENCE_FINAL = re.compile(r"[.!?…][\"')\]]*$")


def merge_fragments(segments, max_gap=1.2):
    """Glue back sentences that Whisper cut at a breath pause.

    Whisper breaks on silence, so "...and nearly" and "hurt her." can arrive as two
    segments. Translated apart, the second loses its subject and comes back as a
    sentence of its own ("La lastimó."), so rejoin anything that does not end on a
    full stop before it reaches the translator.
    """
    out = []
    for seg in segments:
        joinable = (out
                    and not SENTENCE_FINAL.search(out[-1]["en"])
                    and seg["start"] - out[-1]["end"] <= max_gap)
        if joinable:
            out[-1]["en"] = f"{out[-1]['en']} {seg['en']}".strip()
            out[-1]["end"] = seg["end"]
        else:
            out.append(dict(seg))
    return out


def split_sentences(segments, min_span=3.0):
    """Whisper breaks on pauses, not on full stops, so one segment often holds two
    sentences. Splitting them gives one subtitle per sentence and a tighter slot for
    each spoken line; time is shared out by character count, which is close enough
    for speech at a steady pace.
    """
    out = []
    for seg in segments:
        parts = [p.strip() for p in SENTENCE_END.split(seg["en"]) if p.strip()]
        span = seg["end"] - seg["start"]
        if len(parts) < 2 or span < min_span:
            out.append(seg)
            continue
        chars = sum(len(p) for p in parts)
        t = seg["start"]
        for p in parts:
            share = span * len(p) / chars
            out.append({"start": round(t, 3),
                        "end": round(min(t + share, seg["end"]), 3), "en": p})
            t += share
    return out


def translate(segments, d, model_size):
    cache = d / f"translated.{model_size}.json"
    cached = load_json(cache)
    if cached and len(cached) == len(segments):
        print(f"  cached translation: {len(cached)} segments")
        return cached

    import argostranslate.translate as tr

    ensure_argos()
    out = []
    for i, seg in enumerate(segments, 1):
        es = tr.translate(seg["en"], "en", "es").strip()
        out.append({**seg, "es": es})
        if i % 20 == 0 or i == len(segments):
            print(f"  translated {i}/{len(segments)}")
    save_json(cache, out)
    return out


# ---------------------------------------------------------------- 4. subtitles


URL_OR_TAG = re.compile(r"^\s*(https?://|#|@)")


def spanish_metadata(segments, title_en, desc_en, tags, d):
    """A Spanish title and description to paste into the upload form.

    Most short-form videos ship an empty description, so the useful description is
    a synopsis taken from the dub itself. Lines that are links or hashtags are left
    alone -- running those through a translator only mangles them.
    """
    cache = d / "metadata.es.json"
    cached = load_json(cache)
    if cached:
        return cached

    import argostranslate.translate as tr

    ensure_argos()
    title_es = tr.translate(title_en, "en", "es").strip() if title_en else ""
    if not title_es and segments:
        title_es = segments[0]["es"].rstrip(".")[:90]

    body = []
    for line in (desc_en or "").splitlines():
        if not line.strip() or URL_OR_TAG.match(line):
            body.append(line)
        else:
            body.append(tr.translate(line, "en", "es").strip())

    synopsis = " ".join(s["es"] for s in segments[:3])
    parts = [synopsis] + ([("\n".join(body)).strip()] if any(body) else [])
    hashtags = " ".join(dict.fromkeys(
        (tags if isinstance(tags, list) else [tags or ""]) + ["#espanol", "#doblaje"]))
    parts.append(hashtags.strip())

    meta = {"title": title_es[:100], "description": "\n\n".join(p for p in parts if p)}
    save_json(cache, meta)
    return meta


def write_metadata(meta, path):
    path.write_text(
        f"TITULO\n{'=' * 40}\n{meta['title']}\n\n"
        f"DESCRIPCION\n{'=' * 40}\n{meta['description']}\n", encoding="utf-8")
    return path


def srt_time(seconds):
    seconds = max(0.0, seconds)
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{int(round((seconds % 1) * 1000)):03d}"


def wrap(text, width=42, max_lines=2):
    def greedy(w):
        lines, line = [], ""
        for word in text.split():
            if line and len(line) + 1 + len(word) > w:
                lines.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        if line:
            lines.append(line)
        return lines

    lines = greedy(width)
    # A sentence too long for two lines reads better spread evenly than as one
    # full-width line next to a stub, so widen until it fits instead of regrouping.
    w = max(width, -(-len(text) // max_lines))
    while len(lines) > max_lines and w <= len(text):
        lines = greedy(w)
        w += 4
    return "\n".join(lines)


def write_srt(segments, key, path):
    blocks = []
    for i, seg in enumerate(segments, 1):
        end = max(seg["end"], seg["start"] + 0.4)
        if i < len(segments):                   # never let two lines overlap on screen
            end = min(end, segments[i]["start"] - 0.02)
            end = max(end, seg["start"] + 0.3)
        blocks.append(f"{i}\n{srt_time(seg['start'])} --> {srt_time(end)}\n"
                      f"{wrap(seg[key])}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


# ---------------------------------------------------------------- 5. speak


def slots(segments, total):
    """How much wall-clock each line may occupy: up to the next line's start."""
    out = []
    for i, seg in enumerate(segments):
        nxt = segments[i + 1]["start"] if i + 1 < len(segments) else total
        out.append(max(0.4, nxt - seg["start"] - 0.05))
    return out


async def _speak(text, voice, rate, path, sem):
    import edge_tts
    async with sem:
        for attempt in range(3):
            try:
                await edge_tts.Communicate(text, voice, rate=rate).save(str(path))
                return
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(2 * (attempt + 1))


async def _speak_all(jobs, sem_size=4):
    sem = asyncio.Semaphore(sem_size)
    await asyncio.gather(*(_speak(t, v, r, p, sem) for t, v, r, p in jobs))


def clip_path(tts_dir, text, voice, rate):
    key = hashlib.sha1(f"{voice}|{rate}|{text}".encode()).hexdigest()[:16]
    return tts_dir / f"{key}.mp3"


# Edge pads about a second of silence onto every clip, and that padding does not
# shrink when the voice speeds up. Left in, it delays each line against the picture
# and makes short lines measure far longer than the speech they contain, which would
# trigger speed-ups that are not needed. Trim both ends only, so pauses inside a
# sentence survive.
_TRIM_END = "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05"
TRIM = f"{_TRIM_END},areverse,{_TRIM_END},areverse"


def trimmed_wav(mp3):
    """mp3 -> silence-trimmed mono wav, cached next to it."""
    wav = mp3.with_suffix(".trim.wav")
    if not wav.exists():
        ffmpeg_cached(wav, "-i", mp3, "-filter:a", TRIM, "-ac", "1", "-ar", SAMPLE_RATE)
    return wav


def synthesize(segments, avail, d, voice, max_rate):
    """Speak every line, then speed up the ones that overflow their slot.

    Edge's own `rate` is used first because sped-up neural speech sounds far better
    than time-stretching the rendered audio; atempo only mops up what is left.
    """
    tts = d / "tts"
    tts.mkdir(exist_ok=True)

    # pass 1: everything at natural speed
    jobs, mp3s = [], []
    for seg in segments:
        p = clip_path(tts, seg["es"], voice, "+0%")
        mp3s.append(p)
        if not p.exists() and seg["es"]:
            jobs.append((seg["es"], voice, "+0%", p))
    if jobs:
        print(f"  speaking {len(jobs)} lines ({len(mp3s) - len(jobs)} cached) ...")
        asyncio.run(_speak_all(jobs))

    # pass 2: re-speak the lines that still overflow, measured on real speech only
    jobs = []
    for i, (seg, mp3, slot) in enumerate(zip(segments, mp3s, avail)):
        if not mp3.exists():
            continue
        over = duration_of(trimmed_wav(mp3)) / slot
        if over <= 1.02:
            continue
        rate = f"+{min(int(round((over - 1) * 100)), max_rate)}%"
        faster = clip_path(tts, seg["es"], voice, rate)
        mp3s[i] = faster
        if not faster.exists():
            jobs.append((seg["es"], voice, rate, faster))
    if jobs:
        print(f"  re-speaking {len(jobs)} long lines at a faster rate ...")
        asyncio.run(_speak_all(jobs))

    return [trimmed_wav(p) if p.exists() else None for p in mp3s]


def build_voice_track(segments, paths, avail, d, total, max_atempo):
    """Lay every clip onto one silent timeline at its original start time."""
    import numpy as np
    import soundfile as sf

    track = np.zeros(int(total * SAMPLE_RATE) + SAMPLE_RATE, dtype=np.float32)
    fitted = d / "fitted"
    fitted.mkdir(exist_ok=True)
    stretched = 0

    for seg, clean, slot in zip(segments, paths, avail):
        if clean is None:
            continue
        over = duration_of(clean) / slot
        wav = clean
        if over > 1.02:                         # still long: time-stretch the rest away
            tempo = min(over, max_atempo)
            wav = fitted / f"{clean.stem}.{tempo:.3f}.wav"
            stretched += 1
            if not wav.exists():
                ffmpeg_cached(wav, "-i", clean, "-filter:a", f"atempo={tempo:.4f}",
                              "-ac", "1", "-ar", SAMPLE_RATE)

        clip, sr = sf.read(wav, dtype="float32", always_2d=True)
        clip = clip.mean(axis=1)
        at = int(seg["start"] * SAMPLE_RATE)
        end = at + len(clip)
        if end > len(track):                    # last line running past the video
            track = np.pad(track, (0, end - len(track)))
        track[at:end] += clip

    if stretched:
        print(f"  time-stretched {stretched} lines to hold sync")

    peak = float(abs(track).max())
    if peak > 1.0:
        track /= peak                           # clip guard for overlapping lines
    voice = d / "voice_es.wav"
    sf.write(voice, track, SAMPLE_RATE)
    return voice


# ---------------------------------------------------------------- 6. audio bed


def background_bed(video, d, model):
    """Demucs splits the original into voice + everything else; keep everything else."""
    bed = d / "background.wav"
    if bed.exists():
        print("  cached background bed")
        return bed

    src = d / "original48k.wav"
    if not src.exists():
        ffmpeg_cached(src, "-i", video, "-vn", "-ac", "2", "-ar", SAMPLE_RATE)

    tmp = d / "demucs"
    print("  separating voice from music/SFX with Demucs (slow on CPU) ...")
    run([sys.executable, "-m", "demucs", "--two-stems", "vocals",
         "-n", model, "-o", str(tmp), str(src)])
    produced = next(tmp.rglob("no_vocals.wav"))
    shutil.move(str(produced), bed)
    shutil.rmtree(tmp, ignore_errors=True)
    return bed


def mix(bed, voice, d, bg_gain):
    """Spanish on top, original bed ducked underneath whenever the voice speaks."""
    out = d / "final_audio.m4a"
    if bed is None:
        ffmpeg("-i", voice, "-af",
               "aformat=channel_layouts=stereo,loudnorm=I=-14:TP=-1.5:LRA=11",
               "-c:a", "aac", "-b:a", "192k", out)
        return out

    ffmpeg("-i", bed, "-i", voice, "-filter_complex",
           f"[0:a]aformat=channel_layouts=stereo,aresample={SAMPLE_RATE},"
           f"volume={bg_gain}[bg];"
           f"[1:a]aformat=channel_layouts=stereo,aresample={SAMPLE_RATE},"
           "asplit=2[v1][v2];"
           "[bg][v1]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=500[duck];"
           "[duck][v2]amix=inputs=2:normalize=0:duration=longest[m];"
           "[m]loudnorm=I=-14:TP=-1.5:LRA=11[out]",
           "-map", "[out]", "-c:a", "aac", "-b:a", "192k", out)
    return out


# ---------------------------------------------------------------- 7. mux


def detect_caption_band(video, d, samples=40):
    """Find subtitles burned into the picture, and return the rows they occupy.

    Shorts like these ship karaoke captions baked into the pixels. No subtitle track
    can replace those -- they are part of the image, so the only way to show Spanish
    instead is to paint over the band and draw on top of it. Caption glyphs are
    bright with a hard dark outline, a combination almost nothing else in a frame
    has, so score each row by bright pixels that have a dark pixel within a few rows
    and keep the rows that stand out. Returns None when the video is clean.
    """
    cache = d / "caption_band.json"
    cached = load_json(cache)
    if cached is not None:
        return tuple(cached) if cached else None

    import numpy as np

    w, h = video_size(video)
    fps = samples / max(duration_of(video), 1.0)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video),
         "-vf", f"fps={fps:.4f},format=gray", "-f", "rawvideo", "-"],
        capture_output=True).stdout
    n = len(raw) // (w * h)
    if n == 0:
        save_json(cache, None)
        return None

    f = np.frombuffer(raw[:n * w * h], dtype=np.uint8).reshape(n, h, w)
    mid = f[:, :, int(w * 0.2):int(w * 0.8)]          # captions sit centred
    bright, dark = mid > 240, mid < 40
    outline = np.zeros_like(bright)
    for s in range(1, 7):
        outline[:, s:, :] |= dark[:, :-s, :]
        outline[:, :-s, :] |= dark[:, s:, :]
    score = (bright & outline).sum(axis=2).mean(axis=0)
    score[:h // 2] = 0                                 # ignore the top half

    if score.max() < 6:                                # nothing burned in
        save_json(cache, None)
        return None
    rows = np.flatnonzero(score >= max(3.0, score.max() * 0.15))
    pad = int(h * 0.015)
    band = (max(0, int(rows.min()) - pad), min(h, int(rows.max()) + pad))
    save_json(cache, list(band))
    return band


# Where a bold sans-serif lives on each platform. Pillow draws the subtitles
# because this ffmpeg has no libass, freetype or fontconfig, so there is no
# subtitles/ass/drawtext filter to hand the job to -- and doing it here means the
# result looks identical on macOS and Windows.
FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def find_font(explicit=None):
    for c in ([explicit] if explicit else []) + FONT_CANDIDATES:
        if c and Path(c).exists():
            return c
    return None


def render_subtitle_pngs(segments, key, w, h, d, font_path):
    """Draw each line onto its own transparent strip, ready to overlay.

    Returns the strips and their height; the caller anchors them so the text sits
    where the original captions were.
    """
    from PIL import Image, ImageDraw, ImageFont

    out = d / "subpng"
    out.mkdir(exist_ok=True)
    size = max(16, int(h * 0.038))
    font = ImageFont.truetype(font_path, size)
    line_h = int(size * 1.25)
    strip_h = line_h * 2 + int(size * 0.5)
    chars = max(16, int(w / (size * 0.52)))          # fits the frame width

    paths = []
    for i, seg in enumerate(segments):
        p = out / f"{i:04d}.png"
        paths.append(p)
        if p.exists():
            continue
        lines = wrap(seg[key], width=chars).split("\n")[:2]
        img = Image.new("RGBA", (w, strip_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        y = strip_h - line_h * len(lines) - int(size * 0.15)
        for ln in lines:
            draw.text((w // 2, y), ln, font=font, fill=(255, 255, 255, 255),
                      stroke_width=max(2, size // 11), stroke_fill=(0, 0, 0, 255),
                      anchor="ma")
            y += line_h
        img.save(p)
    return paths, strip_h


def mux(video, audio, srt_es, srt_en, out, burn, band=None,
        segments=None, overlays=None):
    if burn:
        w, h = video_size(video)
        # Sit the Spanish where the original captions were, so it reads as a
        # replacement rather than a second row of text. MarginV is measured up
        # from the bottom edge.
        pngs, strip_h = overlays
        bottom = band[1] if band else int(h * 0.94)
        y_text = max(0, bottom - strip_h)

        chain = []
        if band:
            y0, bh = band[0], band[1] - band[0]
            # Blur the strip hard enough to destroy the English glyphs, then darken
            # it so the Spanish drawn on top stays legible over any background.
            chain.append(
                f"[0:v]split=2[base][strip];"
                f"[strip]crop=iw:{bh}:0:{y0},gblur=sigma={max(8, bh // 4)},"
                f"eq=brightness=-0.12[blur];"
                f"[base][blur]overlay=0:{y0}[covered]")
            cur = "[covered]"
        else:
            cur = "[0:v]"

        # One overlay per line, gated to its own moment. A timeline-disabled filter
        # passes the frame straight through, so only the line on screen costs work.
        inputs = []
        for i, (png, seg) in enumerate(zip(pngs, segments)):
            inputs += ["-i", str(png)]
            nxt = f"[o{i}]"
            end = seg["end"] if i + 1 >= len(segments) else min(
                seg["end"], segments[i + 1]["start"] - 0.02)
            chain.append(f"{cur}[{i + 2}:v]overlay=0:{y_text}:"
                         f"enable=between(t\\,{seg['start']:.3f}\\,"
                         f"{max(end, seg['start'] + 0.3):.3f}){nxt}")
            cur = nxt

        ffmpeg("-i", video, "-i", audio, *inputs,
               "-filter_complex", ";".join(chain),
               "-map", cur, "-map", "1:a:0",
               "-c:v", "libx264", "-preset", "medium", "-crf", "20",
               "-c:a", "copy", "-movflags", "+faststart", "-shortest", out)
    else:
        ffmpeg("-i", video, "-i", audio, "-i", srt_es, "-i", srt_en,
               "-map", "0:v:0", "-map", "1:a:0", "-map", "2", "-map", "3",
               "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text",
               "-metadata:s:a:0", "language=spa",
               "-metadata:s:s:0", "language=spa", "-metadata:s:s:0", "title=Espanol",
               "-metadata:s:s:1", "language=eng", "-metadata:s:s:1", "title=English",
               "-movflags", "+faststart", "-shortest", out)
    return out


# ---------------------------------------------------------------- driver


def list_voices():
    import edge_tts

    voices = asyncio.run(edge_tts.list_voices())
    for v in sorted(voices, key=lambda v: v["ShortName"]):
        if v["Locale"].startswith("es-"):
            print(f"  {v['ShortName']:<28} {v['Gender']:<7} {v['Locale']}")


def dub_one(raw_url, a):
    # normalise first so the cache key is the same however the link was pasted
    url = normalize_url(raw_url)
    d = WORK / hashlib.sha1(url.encode()).hexdigest()[:12]
    d.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(exist_ok=True)

    if url != raw_url:
        print(f"\n      (read that link as {url})")
    print(f"\n[1/7] download  {url}")
    video, title, info = download(url, d, a.max_height)
    total = duration_of(video)
    slug = slugify(title)
    print(f"      {title}  ({total/60:.1f} min)")

    print("[2/7] transcribe english")
    segments = transcribe(extract_speech_audio(video, d), d, a.whisper_model)
    segments = split_sentences(merge_fragments(segments))

    print("[3/7] translate to spanish")
    segments = translate(segments, d, a.whisper_model)

    print("[4/7] write subtitles")
    srt_es = write_srt(segments, "es", OUTPUT / f"{slug}.es.srt")
    srt_en = write_srt(segments, "en", OUTPUT / f"{slug}.en.srt")
    meta = spanish_metadata(segments, title, info.get("description"),
                            info.get("tags"), d)
    txt = write_metadata(meta, OUTPUT / f"{slug}.es.txt")

    print("[5/7] speak spanish")
    avail = slots(segments, total)
    paths = synthesize(segments, avail, d, a.voice, a.max_rate)
    voice = build_voice_track(segments, paths, avail, d, total, a.max_atempo)

    print("[6/7] rebuild the audio")
    bed = None if a.no_bg else background_bed(video, d, a.demucs_model)
    audio = mix(bed, voice, d, a.bg_gain)

    print("[7/7] mux")
    band, overlays = None, None
    if a.burn:
        font = find_font(a.font)
        if font is None:
            sys.exit("--burn needs a TrueType font; pass one with --font path/to.ttf")
        if not a.no_cover:
            band = detect_caption_band(video, d)
            print(f"      burned-in captions at rows {band[0]}-{band[1]}, covering them"
                  if band else "      no burned-in captions found")
        w, h = video_size(video)
        overlays = render_subtitle_pngs(segments, "es", w, h, d, font)
    out = mux(video, audio, srt_es, srt_en,
              OUTPUT / f"{slug}.es.mp4", a.burn, band, segments, overlays)
    print(f"\n  -> {out}")
    print(f"  -> {srt_es}")
    print(f"  -> {srt_en}")
    print(f"  -> {txt}")
    print(f"\n  Titulo: {meta['title']}")
    return out


def main():
    p = argparse.ArgumentParser(
        description="Dub an English YouTube video into Spanish, voice and subtitles.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("urls", nargs="*", help="one or more YouTube URLs")
    p.add_argument("--voice", default=DEFAULT_VOICE, help="Edge Spanish voice")
    p.add_argument("--list-voices", action="store_true",
                   help="print every Spanish voice and exit")
    p.add_argument("--whisper-model", default="small",
                   choices=["tiny", "base", "small", "medium", "large-v3"],
                   help="bigger is more accurate and slower")
    p.add_argument("--max-height", type=int, default=1080,
                   help="cap the downloaded picture; the video is copied as-is, "
                        "so this decides the output file size")
    p.add_argument("--demucs-model", default="htdemucs",
                   help="htdemucs (fast) or htdemucs_ft (cleaner, ~4x slower)")
    p.add_argument("--bg-gain", type=float, default=0.85,
                   help="volume of the original music/SFX bed")
    p.add_argument("--max-rate", type=int, default=45,
                   help="most the voice may be sped up, in percent, to fit a line")
    p.add_argument("--max-atempo", type=float, default=1.35,
                   help="hard time-stretch cap for lines still too long after that")
    p.add_argument("--no-bg", action="store_true",
                   help="drop the original audio entirely instead of keeping music/SFX")
    p.add_argument("--burn", action="store_true",
                   help="burn the Spanish subtitles into the picture (re-encodes video); "
                        "any English captions baked into the source are painted over")
    p.add_argument("--font", default=None,
                   help="TrueType font for burned-in subtitles (auto-detected)")
    p.add_argument("--no-cover", action="store_true",
                   help="with --burn, leave burned-in English captions visible")
    a = p.parse_args()

    if a.list_voices:
        list_voices()
        return
    if not a.urls:
        p.error("give me at least one YouTube URL (or --list-voices)")

    for url in a.urls:
        dub_one(url, a)


if __name__ == "__main__":
    main()
