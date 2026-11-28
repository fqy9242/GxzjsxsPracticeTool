FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PRACTICE_DATA_DIR=/data

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 practice \
    && useradd --uid 10001 --gid practice --no-create-home --shell /usr/sbin/nologin practice \
    && mkdir /data && chown practice:practice /data

COPY --chown=practice:practice practice_tool ./practice_tool
USER practice:practice
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3).read()"

CMD ["python", "-m", "practice_tool", "web", "--server", "--host", "0.0.0.0", "--port", "8765", "--data-dir", "/data"]
