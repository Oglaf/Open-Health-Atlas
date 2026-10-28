# Runtime image for the Flask panel and the hermes-bridge write broker.
# Two long-lived containers (panel, broker) plus a one-off admin container
# share this image — see docs/DOCKER.md and docs/ARCHITECTURE.md for the
# privilege split this preserves.
FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/Oglaf/Open-Health-Atlas" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.title="Open Health Atlas" \
      org.opencontainers.image.description="Flask panel and hermes-bridge write-broker runtime for Open Health Atlas"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=wsgi.py

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only the runtime surface: application, toolkit, the three broker files
# (deploy/hermes-bridge imports its adjacent bridge_commands.py by sitting next
# to it, same as the systemd install), the database/vault initializer, generic
# collector env examples, the optional scheduled Google Health collector
# script (deploy/ghealth-sync — see docs/DOCKER.md "Scheduled wearable
# collector"), the bundled anatomy seed document, and the gunicorn entrypoint.
# No tests, no desktop adapter, no docs images.
COPY app/ app/
COPY toolkit/ toolkit/
COPY config/ config/
COPY docs/authored-submuscle-map.md docs/authored-submuscle-map.md
COPY scripts/init_hermes.py scripts/init_hermes.py
COPY deploy/hermes-bridge deploy/hermesctl deploy/bridge_commands.py deploy/ghealth-sync deploy/
COPY wsgi.py .
COPY deploy/docker/oha /usr/local/bin/oha

# Single non-root identity for both panel and broker containers: the
# privilege split comes from which volumes each container mounts read-only
# vs read-write, not from differing uids (see docs/DOCKER.md). A real passwd
# entry (not just USER 1000) keeps pwd.getpwuid() lookups in deploy/hermesctl
# from crashing; home points at /app since nothing needs a writable HOME.
RUN chmod 0755 /usr/local/bin/oha deploy/hermes-bridge deploy/hermesctl deploy/ghealth-sync && \
    groupadd -g 1000 oha && \
    useradd -u 1000 -g 1000 -M -d /app -s /usr/sbin/nologin oha && \
    mkdir -p /data /state /sock /audit && \
    chown -R oha:oha /app /data /state /sock /audit

USER oha
EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=3).status == 200 else 1)"

ENTRYPOINT ["/usr/local/bin/oha"]
CMD ["panel"]
