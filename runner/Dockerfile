# Sandbox image for one eval run: Claude Code + Python 3, nothing else.
# The eval bundle and universes are bind-mounted read-only at /work at run time.
FROM ubuntu:24.04

ARG CLAUDE_VERSION=2.1.296
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    DISABLE_AUTOUPDATER=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-docx poppler-utils ripgrep unzip ca-certificates curl tzdata \
 && rm -rf /var/lib/apt/lists/*

# Install the native Claude Code binary system-wide so any --user uid can run it.
RUN curl -fsSL https://claude.ai/install.sh | bash -s "${CLAUDE_VERSION}" \
 && cp -L /root/.local/bin/claude /usr/local/bin/claude \
 && chmod 755 /usr/local/bin/claude \
 && rm -rf /root/.local /root/.claude /root/.claude.json \
 && claude --version

RUN mkdir -p /work /home/runner && chmod 1777 /home/runner
WORKDIR /work
