# NUMU MCP server — container image for remote (streamable-HTTP) deployment.
# Stateless when NUMU_MCP_AUDIT_BACKEND=api (the default below), so it scales
# horizontally with no disk and no database credentials.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install the package (build deps come from pyproject/hatchling).
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Run as a non-root user.
RUN useradd --create-home --uid 10001 numu
USER numu

# Deploy defaults — override any of these at runtime.
#   HTTP transport, bind all interfaces, durable (Postgres-via-API) audit.
ENV NUMU_MCP_TRANSPORT=http \
    NUMU_MCP_HOST=0.0.0.0 \
    NUMU_MCP_PORT=8765 \
    NUMU_MCP_AUDIT_BACKEND=api

EXPOSE 8765

# TCP liveness check (the MCP endpoint is /mcp; a bare HTTP probe there is not
# a clean 200, so we check the listening socket instead).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import socket,os; socket.create_connection(('127.0.0.1', int(os.environ.get('NUMU_MCP_PORT','8765'))), 3).close()" || exit 1

CMD ["numu-mcp"]
