FROM python:3.11-slim

# ffmpeg does the audio/video work; dejavu is the font Pillow draws subtitles with
# on Linux (dub.py looks for it by name, and --burn cannot draw text without it).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Spaces run the container as uid 1000, so everything written at runtime has to
# live somewhere that user owns.
RUN useradd -m -u 1000 user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    GRADIO_SERVER_NAME=0.0.0.0 \
    GRADIO_SERVER_PORT=7860 \
    GRADIO_TEMP_DIR=/home/user/tmp
WORKDIR $HOME/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

USER user
RUN mkdir -p $HOME/tmp
COPY --chown=user dub.py app.py ./

# Bake the models into the image. Left to runtime these are ~600 MB downloaded
# onto a disk that is wiped on every restart, so the first visitor after each
# wake-up would wait for them.
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
RUN python -c "import argostranslate.package as p; p.update_package_index(); \
p.install_from_path(next(x for x in p.get_available_packages() \
if x.from_code=='en' and x.to_code=='es').download())"
RUN python -c "from demucs.pretrained import get_model; get_model('htdemucs')"

EXPOSE 7860
CMD ["python", "app.py"]
