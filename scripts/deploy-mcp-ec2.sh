#!/usr/bin/env bash
# =============================================================================
# Deploy the NUMU MCP server on the prod storefront EC2 (mcp.numueg.app).
#
# Run ON THE BOX (CI SSHes in and executes this):
#   NUMU_MCP_IMAGE=ghcr.io/numu-io/numu-mcp:prod ./scripts/deploy-mcp-ec2.sh
#
# Steps: pull image → compose up (hard resource caps, no host ports) → gate on
# container health → ensure the storefront nginx routes mcp.numueg.app here
# (idempotent insert + config-test + graceful reload; storefront traffic is
# never interrupted) → prune old images.
# =============================================================================
set -euo pipefail

IMAGE="${NUMU_MCP_IMAGE:?Set NUMU_MCP_IMAGE (e.g. ghcr.io/numu-io/numu-mcp:prod)}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="$DIR/deploy/docker-compose.mcp.ec2.yml"
NGINX_CONF="/opt/numu-storefront/deploy/nginx/ec2-prod.conf"
NGINX_CONTAINER="numu-storefront-nginx"

echo "==> Pulling $IMAGE"
docker pull "$IMAGE"

echo "==> Starting numu-mcp-prod (384M / 0.5 vCPU caps, no host ports)"
NUMU_MCP_IMAGE="$IMAGE" docker compose -f "$COMPOSE" up -d

echo "==> Waiting for container health"
for i in $(seq 1 20); do
  status="$(docker inspect --format '{{.State.Health.Status}}' numu-mcp-prod 2>/dev/null || echo starting)"
  [ "$status" = "healthy" ] && break
  [ "$status" = "unhealthy" ] && { echo "!! numu-mcp-prod is unhealthy"; docker logs --tail 50 numu-mcp-prod; exit 1; }
  sleep 3
done
[ "$status" = "healthy" ] || { echo "!! health timeout"; docker logs --tail 50 numu-mcp-prod; exit 1; }
echo "    healthy."

# ---------------------------------------------------------------------------
# Ensure the storefront nginx has the mcp.numueg.app server block. The block
# also lives in the numu-storefront repo (so storefront deploys re-ship it);
# this insert is a self-healing backstop and is a NO-OP when already present.
# ---------------------------------------------------------------------------
if ! grep -q "server_name mcp.numueg.app" "$NGINX_CONF"; then
  echo "==> Inserting mcp.numueg.app server block into storefront nginx conf"
  python3 - "$NGINX_CONF" <<'PYEOF'
import sys

path = sys.argv[1]
with open(path) as f:
    conf = f.read()

BLOCK = """
    # mcp.numueg.app — NUMU MCP server (AI merchant assistant; repo
    # NUMU-IO/NUMU-MCP, deployed by its own CD). A specific server_name wins
    # over the `_` default, so every other Host still hits the storefront.
    # Lazy upstream (`set $mcp`) — if the MCP container is down, ONLY
    # mcp.numueg.app 502s; storefront traffic is completely unaffected.
    server {
        listen 80;
        server_name mcp.numueg.app;
        client_max_body_size 10M;

        location / {
            limit_req zone=sf_limit burst=30 nodelay;
            set $mcp http://numu-mcp-prod:8765;
            proxy_pass $mcp;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;   # CF terminated TLS
            proxy_set_header Authorization $http_authorization;
            proxy_buffering off;          # SSE-friendly (streamable HTTP)
            proxy_read_timeout 300s;
            proxy_send_timeout 300s;
        }
    }

"""

anchor = "    server {\n        listen 80 default_server;"
if anchor not in conf:
    sys.exit("anchor (default_server block) not found in nginx conf")
with open(path, "w") as f:
    f.write(conf.replace(anchor, BLOCK + anchor, 1))
print("    block inserted")
PYEOF
else
  echo "==> nginx already routes mcp.numueg.app (no conf change)"
fi

echo "==> nginx config test + graceful reload (zero downtime)"
docker exec "$NGINX_CONTAINER" nginx -t
docker exec "$NGINX_CONTAINER" nginx -s reload

echo "==> Pruning dangling images"
docker image prune -f >/dev/null || true

echo "==> Done. numu-mcp-prod is live behind mcp.numueg.app"
