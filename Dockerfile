# Multi-stage production Dockerfile for PlanPilot
# Stage 1: Build virtual environment with dependencies
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build essentials
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast, reliable dependency resolution
RUN pip install --no-cache-dir uv

COPY pyproject.toml .
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install project dependencies
RUN uv pip install --no-cache .

# Stage 2: Runtime image (minimal footprint, non-root user)
FROM python:3.12-slim AS runner

WORKDIR /app

# Install runtime libraries required for DuckDB and OR-Tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -g 1000 planpilot && \
    useradd -u 1000 -g planpilot -s /bin/bash -m planpilot

# Copy virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Copy application source code and assets
COPY src/ /app/src/
COPY ui/ /app/ui/
COPY evals/ /app/evals/
COPY results/ /app/results/
COPY .cache/ /app/.cache/
COPY pyproject.toml /app/

# Install the application package itself into the venv
RUN pip install --no-deps -e /app

# Change ownership to non-root user
RUN chown -R planpilot:planpilot /app

USER planpilot

EXPOSE 8000 8501

# Default command runs the FastAPI API
CMD ["uvicorn", "planpilot.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
