FROM python:3.12-slim

RUN pip install --no-cache-dir pytest==8.4.2 \
    && useradd --create-home --uid 1000 agent

COPY --chown=agent:agent workspace/ /workspace/
WORKDIR /workspace
USER agent
