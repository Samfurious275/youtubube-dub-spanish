#!/usr/bin/env python3
"""Web page for the English -> Spanish video dubber.

Drop in a video, get it back speaking Spanish, with the original music kept,
Spanish subtitles, and a Spanish title and description for the upload form.

  ./serve.sh            macOS / Linux
  serve.bat             Windows

Either one installs whatever is missing on first run, starts the page and opens
your browser at it. Nothing is uploaded anywhere: the whole pipeline runs on this
machine.

  ./serve.sh --share                       also publish a public link (72 hours)
  ./serve.sh --share --password hunter2    ... with a password on it
"""

import argparse
import shutil
import tempfile
from pathlib import Path

import gradio as gr

import dub

VOICES = [
    "es-MX-JorgeNeural",     # male, Latin American
    "es-MX-DaliaNeural",     # female, Latin American
    "es-ES-AlvaroNeural",    # male, Spain
    "es-ES-ElviraNeural",    # female, Spain
]

# Settings the page does not expose; the CLI still has flags for all of them.
FIXED = dict(demucs_model="htdemucs", bg_gain=0.85, max_rate=45,
             max_atempo=1.35, no_cover=False, font=None, max_height=1080)


def process(video, voice, burn, keep_music, quality, english_title,
            progress=gr.Progress()):
    """Run the pipeline on one uploaded file."""
    if not video:
        raise gr.Error("Choose a video first.")

    work = Path(tempfile.mkdtemp(prefix="dub-"))
    source = work / "source.mp4"
    shutil.copy(video, source)

    progress(0.05, desc="Reading the audio")
    total = dub.duration_of(source)

    progress(0.10, desc="Transcribing the English (the slow part)")
    segments = dub.split_sentences(dub.merge_fragments(
        dub.transcribe(dub.extract_speech_audio(source, work), work, quality)))

    progress(0.40, desc=f"Translating {len(segments)} lines to Spanish")
    segments = dub.translate(segments, work, quality)
    srt_es = dub.write_srt(segments, "es", work / "espanol.srt")
    srt_en = dub.write_srt(segments, "en", work / "english.srt")

    title_en = (english_title or "").strip() or Path(video).stem.replace("_", " ")
    meta = dub.spanish_metadata(segments, title_en, "", [], work)

    progress(0.50, desc="Speaking the Spanish")
    avail = dub.slots(segments, total)
    clips = dub.synthesize(segments, avail, work, voice, FIXED["max_rate"])
    voice_track = dub.build_voice_track(segments, clips, avail, work, total,
                                        FIXED["max_atempo"])

    progress(0.70, desc="Separating voice from music" if keep_music else "Mixing")
    bed = dub.background_bed(source, work, FIXED["demucs_model"]) if keep_music else None
    audio = dub.mix(bed, voice_track, work, FIXED["bg_gain"])

    progress(0.85, desc="Rendering the video")
    band, overlays = None, None
    if burn:
        font = dub.find_font()
        if font is None:
            raise gr.Error("No TrueType font found on this machine, so the "
                           "subtitles cannot be drawn. Untick the burn option.")
        band = dub.detect_caption_band(source, work)
        w, h = dub.video_size(source)
        overlays = dub.render_subtitle_pngs(segments, "es", w, h, work, font)
    out = dub.mux(source, audio, srt_es, srt_en, work / "doblado.mp4",
                  burn, band, segments, overlays)

    progress(1.0, desc="Done")
    return str(out), str(srt_es), str(srt_en), meta["title"], meta["description"]


def build_ui():
    # gradio 6 renamed show_copy_button; this keeps 4 and 5 working too
    copy = ({"buttons": ["copy"]} if int(gr.__version__.split(".")[0]) >= 6
            else {"show_copy_button": True})

    with gr.Blocks(title="Doblaje al espanol") as demo:
        gr.Markdown(
            "# English &rarr; Spanish video dubber\n"
            "Choose a video **you own or have permission to use**. The English "
            "voice is replaced with Spanish, the original music and sound effects "
            "are kept, and you get subtitles plus a Spanish title and description.\n\n"
            "**This takes a while** — roughly five minutes of work per minute of "
            "video. The progress bar moves between stages, not within them, so long "
            "quiet stretches are normal. Try a short clip first."
        )
        with gr.Row():
            with gr.Column():
                video = gr.Video(label="Your English video")
                english_title = gr.Textbox(
                    label="English title (optional)",
                    placeholder="Used for the Spanish title; the filename is "
                                "used if you leave this blank")
                voice = gr.Dropdown(VOICES, value=VOICES[0], label="Spanish voice")
                burn = gr.Checkbox(
                    value=True, label="Burn Spanish subtitles into the picture",
                    info="Also paints over English captions baked into the video")
                keep_music = gr.Checkbox(
                    value=True, label="Keep the original music and sound effects",
                    info="Unticking this skips Demucs and is about 4x faster")
                quality = gr.Radio(
                    ["base", "small", "medium"], value="small",
                    label="Transcription quality",
                    info="base is ~3x faster; medium is slower and more accurate")
                go = gr.Button("Dub it", variant="primary")
            with gr.Column():
                out_video = gr.Video(label="Dubbed video")
                out_title = gr.Textbox(label="Titulo en espanol", **copy)
                out_desc = gr.Textbox(label="Descripcion en espanol", lines=6, **copy)
                out_es = gr.File(label="Spanish subtitles (.srt)")
                out_en = gr.File(label="English subtitles (.srt)")

        go.click(process,
                 inputs=[video, voice, burn, keep_music, quality, english_title],
                 outputs=[out_video, out_es, out_en, out_title, out_desc])
    return demo


def main():
    p = argparse.ArgumentParser(
        description="Serve the dubber as a web page on this machine.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--share", action="store_true",
                   help="also publish a public gradio.live link (expires in 72 h)")
    p.add_argument("--password", default=None,
                   help="require this password; strongly advised with --share")
    p.add_argument("--user", default="dub", help="username that goes with --password")
    p.add_argument("--host", default="127.0.0.1",
                   help="0.0.0.0 to allow other machines on your network")
    p.add_argument("--port", type=int, default=7860)
    a = p.parse_args()

    if a.share and not a.password:
        print("  ! --share puts this on the public internet with no password.\n"
              "    Anyone with the link can queue jobs on your machine.\n"
              "    Add --password to lock it.\n")

    print(f"\n  Starting the dubber on http://{a.host}:{a.port}")
    if a.password:
        # The username is easy to miss, and the login just says "Incorrect
        # Credentials" when it is wrong, so spell both out here.
        print(f"\n  The page will ask you to log in:")
        print(f"      username   {a.user}")
        print(f"      password   {a.password}\n")
    print("  Your browser should open by itself. Press Ctrl+C here to stop.\n")

    # queue() keeps long jobs alive; without it the browser gives up partway
    build_ui().queue(max_size=8).launch(
        server_name=a.host, server_port=a.port, share=a.share,
        auth=(a.user, a.password) if a.password else None,
        inbrowser=True)


if __name__ == "__main__":
    main()
