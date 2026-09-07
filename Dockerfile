# Whopper & Watt als Container.
#
# Kein Build-Schritt, keine Abhaengigkeiten: der Server ist reine
# Standardbibliothek, die PWA hat weder Bundler noch npm-Pakete. Das Image ist
# damit im Wesentlichen Python plus ein paar hundert Kilobyte eigener Code
# sowie der Datenbestand.
#
#   docker build -t whopper-watt .
#   docker run -p 8000:8000 whopper-watt
#
# Frischere Daten ohne Neubau: das Datenverzeichnis als Volume einhaengen.
#   docker run -p 8000:8000 -v ./server/data:/app/server/data whopper-watt

FROM python:3.13-slim

# Kein Bytecode-Muell, keine gepufferte Ausgabe (sonst fehlen Logs beim Absturz).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Nur was der Server wirklich braucht. Die Android-App liegt im selben Repo und
# hat im Image nichts verloren.
COPY server/ /app/server/
COPY web/ /app/web/

# Ohne eigenen Nutzer laeuft alles als root, auch wenn es nichts zu schreiben gibt.
RUN useradd --system --uid 10001 --home /app whopper \
    && chown -R whopper:whopper /app
USER whopper

EXPOSE 8000

# Prueft den Endpunkt, der die Datenbank wirklich anfasst, nicht nur den Port.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if 'burgers' in urllib.request.urlopen('http://127.0.0.1:8000/api/meta', timeout=4).read().decode() else 1)"

# --trust-proxy ist gesetzt, weil ein Container praktisch immer hinter einem
# Reverse Proxy steht. Ohne Proxy sieht der Server dann die Docker-Bridge-IP,
# was die Bremse pro Client zu einer Bremse fuer alle macht: dann weglassen.
CMD ["python3", "server/app.py", "--host", "0.0.0.0", "--port", "8000", "--trust-proxy"]
