FROM python:3.11-slim

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

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
