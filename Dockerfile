FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV MCP_HOST=0.0.0.0 \
    MCP_PORT=8100

EXPOSE 8100

# NUMU_API_URL must be provided at runtime (must include /api/v1)
CMD ["python", "-m", "numu_mcp"]
