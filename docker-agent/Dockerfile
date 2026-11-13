# docker-agent: reads the Docker daemon, sends container metrics to the API.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py .

# Non-root user. Access to /var/run/docker.sock is given in compose with
# group_add (the docker group ID of the host), not by running as root.
RUN useradd --system --uid 10001 app
USER app

CMD ["python", "agent.py"]
