# Production image for the SecureMUD game server.
# Build:   docker build -t securemud .
# Deploy:  fly deploy   (uses this Dockerfile via fly.toml)

FROM python:3.13-slim

WORKDIR /app

# cryptography is the only runtime dep (used for self-signed cert generation
# and PBKDF2 password hashing). Pinned to a major version so reproducible.
RUN pip install --no-cache-dir "cryptography>=42,<46"

# Copy code and story content. data/ is intentionally NOT copied —
# it lives on a Fly volume mounted at /app/data so player state survives
# deploys. See fly.toml [mounts].
COPY server/ ./server/
COPY story/ ./story/

EXPOSE 4443
ENV MUD_HOST=0.0.0.0 \
    MUD_PORT=4443 \
    PYTHONUNBUFFERED=1

CMD ["python3", "server/server.py"]
