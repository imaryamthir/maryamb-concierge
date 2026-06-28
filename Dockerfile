# Container image for deploying Layla to Cloud Run (the "Deployability" concept).
# Build:  docker build -t maryamb-concierge .
# Run:    docker run -p 8080:8080 --env-file .env maryamb-concierge
#
# For a managed deploy you can instead use the ADK CLI:
#   adk deploy cloud_run --project YOUR_PROJECT --region us-central1 .
# which generates an equivalent container automatically.

FROM python:3.12-slim

# Node is needed only if you later add npx-based community MCP servers.
# Our own catalog server is pure Python, so the base image is enough.

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# `adk web` serves the chat UI + API. Cloud Run provides $PORT (default 8080).
ENV PORT=8080
EXPOSE 8080

# Bind to 0.0.0.0 so the container is reachable; serve the project root so ADK
# discovers the `layla` agent package.
CMD ["sh", "-c", "adk web --host 0.0.0.0 --port ${PORT} ."]
