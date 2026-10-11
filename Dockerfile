FROM python:3.12-slim

# ffmpeg (for ffprobe) -- quality_detect.py reads a downloaded file's own
# resolution/codec directly, needed once this sidecar imports files itself
# instead of trusting Whisparr's grab-time quality metadata (Milestone 2,
# 2026-09-17). Not needed before that.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/ .

ENV PYTHONUNBUFFERED=1
EXPOSE 5000

# --timeout-graceful-shutdown: open /events streams never end on their own;
# without it a restart would wait for every browser tab to go away.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5000", "--timeout-graceful-shutdown", "3"]
