"""Full video transcripts.

1. Captions published with the video (uploader subtitles, then platform auto-captions),
   read from the yt-dlp info dict. Fast and free.
2. Otherwise, speech-to-text: download the audio track with yt-dlp and run
   faster-whisper locally. Optional (`pip install faster-whisper`), slower, CPU is fine.

Output is plain text in timestamped paragraphs: "[1:23] words words words".
"""

import html
import logging
import re
import tempfile
import threading
import time
from pathlib import Path

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)

PARAGRAPH_SECONDS = 30
_TAG_RE = re.compile(r"<[^>]+>")
_VTT_TIME_RE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})\s+-->")

Segment = tuple[float, str]  # (start seconds, text)


# ---------------------------------------------------------------- formatting


def _clock(seconds: float) -> str:
    s = int(seconds)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_segments(segments: list[Segment], paragraph_seconds: int = PARAGRAPH_SECONDS) -> str | None:
    """Group caption lines into timestamped paragraphs and drop rolling-caption repeats."""
    paragraphs: list[str] = []
    current: list[str] = []
    start = 0.0
    prev = ""
    for t, raw in segments:
        line = " ".join(html.unescape(_TAG_RE.sub("", raw)).split())
        if not line or line == prev:
            continue
        # Rolling auto-captions repeat the previous line as a prefix of the next one.
        text = line[len(prev):].strip() if prev and line.startswith(prev) else line
        prev = line
        if not text:
            continue
        if current:
            elapsed = t - start
            ends_sentence = current[-1][-1:] in ".?!"
            if (elapsed >= paragraph_seconds and ends_sentence) or elapsed >= 2 * paragraph_seconds:
                paragraphs.append(f"[{_clock(start)}] " + " ".join(current))
                current = []
        if not current:
            start = t
        current.append(text)
    if current:
        paragraphs.append(f"[{_clock(start)}] " + " ".join(current))
    return "\n\n".join(paragraphs) or None


# ---------------------------------------------------------------- captions


def parse_json3(data: dict) -> list[Segment]:
    out = []
    for ev in data.get("events") or []:
        segs = ev.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).replace("\n", " ").strip()
        if text:
            out.append((ev.get("tStartMs", 0) / 1000, text))
    return out


def parse_vtt(text: str) -> list[Segment]:
    out = []
    start = None
    lines: list[str] = []
    for line in text.splitlines() + [""]:
        m = _VTT_TIME_RE.match(line.strip())
        if m:
            h, mnt, s, ms = m.groups()
            start = int(h or 0) * 3600 + int(mnt) * 60 + int(s) + int(ms) / 1000
            lines = []
        elif not line.strip():
            if start is not None and lines:
                out.append((start, " ".join(lines)))
            start, lines = None, []
        elif start is not None:
            lines.append(line.strip())
    return out


def pick_caption_track(info: dict, prefer: str | None = None) -> tuple[str, str, str] | None:
    """Return (url, ext, source) for the best caption track.

    By default the video's own language (most accurate; Claude translates when it summarizes).
    With `prefer="en"`, English comes first, using the platform's machine translation if needed,
    so summaries made without Claude are still readable.
    """
    lang = (info.get("language") or "").split("-")[0].lower()
    subs = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    auto = info.get("automatic_captions") or {}

    def base(k: str) -> str:
        return k.split("-")[0].lower()

    ordered: list[tuple[list, str]] = []
    if prefer:
        ordered += [(v, "captions") for k, v in subs.items() if base(k) == prefer]
        if prefer in auto:
            ordered.append((auto[prefer], "auto-captions" if lang in ("", prefer) else "translated captions"))
    if lang:
        ordered += [(v, "captions") for k, v in subs.items() if base(k) == lang]
    ordered += [(v, "auto-captions") for k, v in auto.items() if k.endswith("-orig")]  # untranslated original
    if lang and lang in auto:
        ordered.append((auto[lang], "auto-captions"))
    ordered += [(v, "captions") for k, v in subs.items() if base(k) == "en"]
    ordered += [(v, "captions") for v in subs.values()]
    if not lang and "en" in auto:
        ordered.append((auto["en"], "auto-captions"))

    for formats, source in ordered:
        by_ext = {f.get("ext"): f for f in formats if f.get("url")}
        for ext in ("json3", "vtt", "srt"):
            if ext in by_ext:
                return by_ext[ext]["url"], ext, source
    return None


def _download_track(url: str, ext: str) -> str | None:
    s = get_settings()
    for attempt in range(2):
        try:
            r = httpx.get(url, timeout=30, headers={"User-Agent": s.user_agent}, follow_redirects=True)
            if r.status_code == 429 and attempt == 0:
                time.sleep(3)  # YouTube throttles translated captions; one polite retry
                continue
            r.raise_for_status()
            segments = parse_json3(r.json()) if ext == "json3" else parse_vtt(r.text)
            return format_segments(segments)
        except (httpx.HTTPError, ValueError) as e:
            log.info("caption download failed: %s", e)
            return None
    return None


def captions_from_info(info: dict, prefer: str | None = None) -> tuple[str, str] | None:
    """(transcript, source). Falls back to the original-language track if the preferred one fails."""
    tracks = [pick_caption_track(info, prefer)]
    if prefer:
        tracks.append(pick_caption_track(info))
    tried = set()
    for track in tracks:
        if not track or track[0] in tried:
            continue
        tried.add(track[0])
        text = _download_track(track[0], track[1])
        if text:
            return text, track[2]
    return None


# ---------------------------------------------------------------- speech-to-text

_model = None
_model_lock = threading.Lock()   # load once
_run_lock = threading.Lock()     # one transcription at a time: whisper uses every CPU core


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _whisper_model(force_cpu: bool = False):
    global _model
    with _model_lock:
        if _model is None or force_cpu:
            from faster_whisper import WhisperModel

            s = get_settings()
            device = "cpu" if force_cpu else s.whisper_device
            log.info("loading whisper model %s on %s (first run downloads it)", s.whisper_model, device)
            _model = WhisperModel(s.whisper_model, device=device, compute_type="int8")
    return _model


def _transcribe_with(model, audio):
    # The voice filter skips silence and noise but also drops sung vocals, so music videos
    # come back empty: retry those without it.
    for vad in (True, False):
        segments, info = model.transcribe(audio, vad_filter=vad, beam_size=5)
        result = [(seg.start, seg.text) for seg in segments]  # generator: decoding happens here
        if result:
            break
    return result, info


def _run_whisper(audio):
    try:
        return _transcribe_with(_whisper_model(), audio)
    except RuntimeError as e:
        if not any(k in str(e).lower() for k in ("cuda", "cublas", "cudnn")):
            raise
        log.warning("GPU transcription failed (%s); falling back to CPU", e)
        return _transcribe_with(_whisper_model(force_cpu=True), audio)


def _download_audio(url: str, folder: Path) -> Path | None:
    import yt_dlp

    opts = {
        "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True, "socket_timeout": 20,
        # Audio-only streams need no ffmpeg; muxed video (Instagram/TikTok) is decoded the same way.
        "format": "bestaudio[ext=m4a]/bestaudio/best[height<=480]/best",
        "outtmpl": str(folder / "media.%(ext)s"),
        "max_filesize": 500 * 1024 * 1024,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = [p for p in folder.iterdir() if p.name.startswith("media.")]
    return files[0] if files else None


def _decode_audio(path: Path):
    """Decode any audio/video file to 16 kHz mono float32, the input whisper expects.

    Done here with PyAV directly because faster-whisper's own decoder breaks on newer PyAV releases.
    """
    import av
    import numpy as np

    chunks = []
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    with av.open(str(path)) as container:
        stream = next(s for s in container.streams if s.type == "audio")
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):  # flush
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return None
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def whisper_transcribe(url: str) -> str | None:
    with tempfile.TemporaryDirectory(prefix="linkvault-") as tmp:
        media = _download_audio(url, Path(tmp))
        audio = _decode_audio(media) if media else None
        if audio is None:
            return None
        with _run_lock:
            result, info = _run_whisper(audio)
        log.info("transcribed %s (%s, %.0fs)", url, info.language, info.duration)
        return format_segments(result)


def can_transcribe_audio(duration: float | None) -> bool:
    s = get_settings()
    if not (s.enable_whisper and whisper_available()):
        return False
    return duration is None or duration <= s.whisper_max_minutes * 60
