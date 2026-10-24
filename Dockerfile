FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements-server.txt \
    && useradd --uid 10001 --create-home office \
    && mkdir -p /app/data /app/output && chown -R office:office /app
COPY --chown=office:office office_agent ./office_agent
COPY --chown=office:office gunicorn.conf.py ./
USER office
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz',timeout=3)"
CMD ["gunicorn", "--config", "gunicorn.conf.py", "office_agent.hosting:create_application()"]
