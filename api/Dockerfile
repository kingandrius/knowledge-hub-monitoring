# Monitoring API (CS2 code, unchanged) as a container.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KH_STORAGE_BACKEND=sqlite \
    KH_SQLITE_PATH=/data/khapi.db \
    KH_PROXY_HOPS=0

WORKDIR /app

COPY requirements.txt .
# gunicorn is not in requirements.txt because App Service brings its own.
RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0

COPY app.py .
COPY khapi/ khapi/

# Non-root user; /data holds the SQLite file (named volume in compose).
RUN useradd --system --uid 10001 app \
    && mkdir /data && chown app /data
USER app

EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "app:app"]
