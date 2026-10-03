FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS test
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY worker.py .
COPY tests ./tests
RUN python -m pytest -q

FROM base AS runtime
RUN useradd --uid 10001 --no-create-home appuser
COPY worker.py .
USER 10001
CMD ["python", "worker.py"]
