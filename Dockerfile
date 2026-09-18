FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    DAGSTER_HOME=/opt/dagster/home

WORKDIR /app

# Dependencies first so edits to source do not invalidate the install layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY dbt/ ./dbt/
COPY pyproject.toml ./

RUN mkdir -p /opt/dagster/home /app/data

EXPOSE 3000

CMD ["dagster", "dev", "-m", "starlink_drag.orchestration.definitions", "--host", "0.0.0.0", "--port", "3000"]
