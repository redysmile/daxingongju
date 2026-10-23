FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
WORKDIR /app
RUN groupadd --gid 10001 scout && useradd --uid 10001 --gid scout --no-create-home scout && mkdir /data && chown scout:scout /data
COPY --chown=10001:10001 server.py auth.py manage.py ./
COPY --chown=10001:10001 web/ ./web/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=3000 SCOUT_DB=/data/scout.db SCOUT_REQUIRE_AUTH=1 SCOUT_COOKIE_SECURE=1
USER 10001:10001
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 CMD ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/api/health', timeout=3)"]
CMD ["python3", "server.py"]
