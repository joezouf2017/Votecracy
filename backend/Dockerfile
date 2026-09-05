FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user. Nothing here needs root, and a container process that
# does not have it cannot be talked into using it. The chown matters for a
# deployment without the compose bind mount — with the mount, the host's
# ownership wins at runtime, and Python simply skips writing __pycache__ if it
# cannot.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8000

# Migrations run before the app, and a failure here stops the container rather
# than letting it serve against a stale schema.
CMD ["sh", "-c", "alembic upgrade head && uvicorn game.main:app --host 0.0.0.0 --port 8000"]
