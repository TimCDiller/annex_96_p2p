# Isolation for running collected submissions. See docs/security.md.
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml requirements.txt README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[dev]"

COPY config ./config
COPY scripts ./scripts
COPY tests ./tests
COPY examples ./examples
COPY agents ./agents

# Submissions are mounted read-only at run time, not baked in:
#   docker run --rm --network none --memory 2g --cpus 2 \
#     -v "$PWD/examples/original_submissions:/app/examples/original_submissions:ro" annex96 \
#     python -m annex96p2p tournament --config config/default.yaml
RUN mkdir -p submissions && useradd -m runner && chown -R runner /app
USER runner

CMD ["python", "-m", "annex96p2p", "run", "--config", "config/smoke.yaml"]
