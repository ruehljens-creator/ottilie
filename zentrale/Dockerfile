# Ottilie-Zentrale – Abbild für Docker (amd64; arm64 baut genauso).
# Baukontext ist das Paket aus `zentrale/paket.sh` bzw. das Repository selbst: darin `zentrale/` und die wenigen
# Module aus Ottilie, deren Regeln die Zentrale wiederverwendet (Übergabe-JSON prüfen, Stufe 3 A).
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ZENTRALE_DATEN=/data ZENTRALE_PORT=8470
RUN groupadd --system --gid 10470 zentrale \
 && useradd --system --uid 10470 --gid zentrale --home-dir /app --shell /usr/sbin/nologin zentrale
WORKDIR /app
COPY zentrale/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r /app/requirements.txt
COPY zentrale/app /app/app
COPY zentrale/web /app/web
COPY tonmischung/__init__.py /app/tonmischung/__init__.py
COPY tonmischung/core/__init__.py tonmischung/core/fileio.py tonmischung/core/mixstate.py /app/tonmischung/core/
COPY tonmischung/helmut/__init__.py tonmischung/helmut/mapping.py /app/tonmischung/helmut/
COPY tonmischung/dropfolder/__init__.py tonmischung/dropfolder/handover.py /app/tonmischung/dropfolder/
RUN mkdir -p /data && chown zentrale:zentrale /data
USER zentrale
EXPOSE 8470
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8470/gesund', timeout=4)" || exit 1
CMD ["python", "-m", "app.run"]
