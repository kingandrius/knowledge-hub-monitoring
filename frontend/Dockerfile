# Web frontend (Flask + Entra ID sign-in) as a container.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY khfront/ khfront/

# Non-root user; Flask-Session writes session files to /app/flask_session.
RUN useradd --system --uid 10001 app \
    && mkdir flask_session && chown app flask_session
USER app

EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
