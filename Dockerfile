FROM python:3.11-slim

# Links the GHCR package to the repository
LABEL org.opencontainers.image.source="https://github.com/issararab/LakePlan" \
      org.opencontainers.image.title="LakePlan" \
      org.opencontainers.image.description="Plans a Databricks architecture for your use case and estimates its Azure cost"

# No .pyc writes, so the container also runs with a read-only root filesystem
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY data/ ./data/

ENV LLM_PROVIDER=databricks
ENV DATABRICKS_HOST=https://dbc-33ec6622-28d3.cloud.databricks.com/
ENV DATABRICKS_LLM_ENDPOINT=databricks-claude-opus-4-7
ENV DUCKDB_PATH=data/pricing.duckdb
ENV MAX_ROWS=100
ENV LOG_LEVEL=INFO

# The app only reads its files, so it runs as an unprivileged user
RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin lakeplan
USER 10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
