FROM python:3.11-slim

WORKDIR /app

# Install build tooling first
RUN pip install --no-cache-dir hatchling

# Copy dependency files
COPY pyproject.toml ./

# Install runtime dependencies (using pip with the pyproject.toml)
RUN pip install --no-cache-dir \
    "fastapi>=0.115.0,<1.0.0" \
    "uvicorn[standard]>=0.30.0,<1.0.0" \
    "pydantic>=2.6.0,<3.0.0" \
    "google-adk[gcp]>=2.0.0,<3.0.0" \
    "google-cloud-aiplatform[agent-engines]>=1.156.0"

# Copy application source
COPY submission_frontend/ ./submission_frontend/

# Expose port 8080
EXPOSE 8080

# Start uvicorn server
CMD ["uvicorn", "submission_frontend.main:app", "--host", "0.0.0.0", "--port", "8080"]
