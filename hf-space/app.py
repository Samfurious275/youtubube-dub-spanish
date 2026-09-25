"""Gradio front end for the English -> Spanish dubber.

Takes an uploaded file rather than a YouTube URL: Hugging Face runs in a
datacenter and YouTube blocks datacenter IPs, and it keeps the Space from
fetching anyone else's video.
"""

import shutil
import tempfile
from argparse import Namespace
from pathlib import Path

import gradio as gr

import dub

VOICES = [
    "es-MX-JorgeNeural",     # male, Latin American
    "es-MX-DaliaNeural",     # female, Latin American
    "es-ES-AlvaroNeural",    # male, Spain
    "es-ES-ElviraNeural",    # female, Spain
]

DEFAULTS = dict(demucs_model="htdemucs", bg_gain=0.85, max_rate=45,
                max_atempo=1.35, no_cover=False, font=None, max_height=1080)


def process(video, voice, burn, keep_music, quality, english_title,
            progress=gr.Progress()):
    if not video:
        raise gr.Error("Upload a video first.")

    work = Path(tempfile.mkdtemp(prefix="dub-"))
    source = work / "source.mp4"
    shutil.copy(video, source)
    a = Namespace(voice=voice, whisper_model=quality, no_bg=not keep_music,
                  burn=burn, **DEFAULTS)

    progress(0.05, desc="Reading the audio")
    total = dub.duration_of(source)

    progress(0.10, desc="Transcribing the English")
    segments = dub.split_sentences(dub.merge_fragments(
        dub.transcribe(dub.extract_speech_audio(source, work), work,
                       a.whisper_model)))

    progress(0.40, desc=f"Translating {len(segments)} lines to Spanish")
    segments = dub.translate(segments, work, a.whisper_model)
    srt_es = dub.write_srt(segments, "es", work / "espanol.srt")
    srt_en = dub.write_srt(segments, "en", work / "english.srt")

    title_en = (english_title or "").strip() or Path(video).stem.replace("_", " ")
    meta = dub.spanish_metadata(segments, title_en, "", [], work)

    progress(0.50, desc="Speaking the Spanish")
    avail = dub.slots(segments, total)
    clips = dub.synthesize(segments, avail, work, voice, a.max_rate)
    voice_track = dub.build_voice_track(segments, clips, avail, work, total,
                                        a.max_atempo)

    progress(0.70, desc="Separating voice from music" if keep_music else "Mixing")
    bed = None if a.no_bg else dub.background_bed(source, work, a.demucs_model)
    audio = dub.mix(bed, voice_track, work, a.bg_gain)

    progress(0.85, desc="Rendering the video")
    band, overlays = None, None
    if burn:
        font = dub.find_font()
        if font is None:
            raise gr.Error("No TrueType font found in the container.")
        band = dub.detect_caption_band(source, work)
        w, h = dub.video_size(source)
        overlays = dub.render_subtitle_pngs(segments, "es", w, h, work, font)
    out = dub.mux(source, audio, srt_es, srt_en, work / "doblado.mp4",
                  burn, band, segments, overlays)

    progress(1.0, desc="Done")
    return str(out), str(srt_es), str(srt_en), meta["title"], meta["description"]


with gr.Blocks(title="Doblaje al espanol") as demo:
    gr.Markdown(
        "# English &rarr; Spanish video dubber\n"
        "Upload a video **you own or have permission to use**. The English voice is "
        "replaced with Spanish, the original music and effects are kept, and you get "
        "subtitles plus a Spanish title and description for the upload form.\n\n"
        "Processing takes several minutes and runs one job at a time."
    )
    with gr.Row():
        with gr.Column():
            video = gr.Video(label="Your English video")
            english_title = gr.Textbox(
                label="English title (optional)",
                placeholder="Used to write the Spanish title; the filename is used if blank")
            voice = gr.Dropdown(VOICES, value=VOICES[0], label="Spanish voice")
            burn = gr.Checkbox(
                value=True, label="Burn Spanish subtitles into the picture",
                info="Also paints over English captions baked into the video")
            keep_music = gr.Checkbox(
                value=True, label="Keep the original music and sound effects",
                info="Uses Demucs. Turning this off is about 4x faster")
            quality = gr.Radio(
                ["base", "small", "medium"], value="small",
                label="Transcription quality",
                info="base is ~3x faster, medium is slower and more accurate")
            go = gr.Button("Dub it", variant="primary")
        with gr.Column():
            out_video = gr.Video(label="Dubbed video")
            out_title = gr.Textbox(label="Titulo en espanol", show_copy_button=True)
            out_desc = gr.Textbox(label="Descripcion en espanol", lines=6,
                                  show_copy_button=True)
            out_es = gr.File(label="Spanish subtitles (.srt)")
            out_en = gr.File(label="English subtitles (.srt)")

    go.click(process,
             inputs=[video, voice, burn, keep_music, quality, english_title],
             outputs=[out_video, out_es, out_en, out_title, out_desc])

if __name__ == "__main__":
    # queue() keeps long jobs alive; without it the browser gives up before the
    # pipeline finishes.
    demo.queue(max_size=8).launch()
