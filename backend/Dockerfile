FROM python:3.12-slim

WORKDIR /app

# Install system build dependencies for cryptographic and network libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject.toml and backend app code so wheel build succeeds
COPY pyproject.toml .
COPY backend/app ./backend/app
COPY backend/app ./app

# Install Python dependencies
RUN pip install --no-cache-dir .

# Copy database migrations configuration
COPY backend/alembic ./alembic
COPY backend/alembic.ini .

# Copy compiled frontend distribution into static directory for SPA serving
COPY frontend/dist ./static

RUN mkdir -p /app/credentials

ENV PYTHONPATH=/app
ENV APP_HOST=0.0.0.0
ENV APP_PORT=8090

EXPOSE 8090

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
