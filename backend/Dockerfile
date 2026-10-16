FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

COPY pyproject.toml ./
COPY app ./app
# Add ",transcribe" (and ENABLE_WHISPER=true) for speech-to-text on videos without captions.
RUN pip install --no-cache-dir ".[postgres]"

EXPOSE 8000
# Hosts like Railway/Render inject $PORT.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
