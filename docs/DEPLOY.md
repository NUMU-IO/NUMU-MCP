# Deploying the NUMU MCP Server

Two very different deployment shapes. Pick by who runs it.

| Shape | Transport | Audit backend | Where |
|-------|-----------|---------------|-------|
| **Per-merchant / local** | `stdio` | `sqlite` (default) | the user's machine (Claude Desktop) |
| **Shared / remote service** | `http` (streamable-HTTP) | `api` (Postgres via NUMU API) | AWS (ECS/Fargate), etc. |

The golden rule for a remote deployment: **run it stateless.** Set
`NUMU_MCP_AUDIT_BACKEND=api` so the audit/undo trail lives in your Postgres
(written by the NUMU API), and the container keeps **no disk and no database
credentials**. Then you can scale it horizontally and redeploy freely.

> Prerequisite for `api` mode: the backend audit endpoints
> (`/api/v1/stores/{id}/audit`) must be live — they ship in the
> `feat/mcp-audit-endpoint` PR on `NUMU-api`. The PAT feature
> (`feat/personal-access-tokens`) must also be merged so the token authenticates.

---

## Local (stdio) — nothing to deploy

See the [README](../README.md). Claude Desktop launches the process; SQLite keeps
the audit in `mcp/data/audit.db`. No container, no ports.

---

## Remote (HTTP) with Docker

```bash
# Build
docker build -t numu-mcp:latest .

# Run (HTTP on :8765, durable audit via the API)
docker run --rm -p 8765:8765 \
  -e NUMU_MCP_BASE_URL=https://mystore.numueg.app \
  -e NUMU_MCP_STORE_ID=<STORE_UUID> \
  -e NUMU_MCP_ACCESS_TOKEN=numu_pat_xxx \
  -e NUMU_MCP_AUDIT_BACKEND=api \
  numu-mcp:latest
# MCP endpoint: http://localhost:8765/mcp
```

Or `docker compose up` (see `docker-compose.yml`).

---

## AWS (ECS / Fargate) — recommended for a shared service

This is the stateless pattern:

```
client ──HTTPS──► ALB (TLS) ──► Fargate task(s): numu-mcp (NO disk, NO db creds)
                                       │ HTTPS + PAT
                                       ▼
                                  NUMU API ──► RDS/Aurora Postgres (audit_logs)
```

### 1. Push the image to ECR
```bash
aws ecr create-repository --repository-name numu-mcp
aws ecr get-login-password --region <REGION> \
  | docker login --username AWS --password-stdin <ACCT>.dkr.ecr.<REGION>.amazonaws.com
docker tag numu-mcp:latest <ACCT>.dkr.ecr.<REGION>.amazonaws.com/numu-mcp:latest
docker push <ACCT>.dkr.ecr.<REGION>.amazonaws.com/numu-mcp:latest
```

### 2. Store the token as a secret (don't bake it into the task def)
```bash
aws secretsmanager create-secret --name numu-mcp/access-token \
  --secret-string "numu_pat_xxx"
```

### 3. Task definition (essentials)
- **Image:** your ECR URI.
- **Port mapping:** container `8765`.
- **Environment:**
  - `NUMU_MCP_BASE_URL = https://<store-subdomain>.numueg.app`
  - `NUMU_MCP_STORE_ID = <STORE_UUID>`
  - `NUMU_MCP_TRANSPORT = http`
  - `NUMU_MCP_HOST = 0.0.0.0`
  - `NUMU_MCP_PORT = 8765`
  - `NUMU_MCP_AUDIT_BACKEND = api`  ← keeps the task stateless
- **Secrets:** `NUMU_MCP_ACCESS_TOKEN` → from the Secrets Manager ARN above.
- **No volumes.** (If you ever choose `sqlite` here you'd need an EFS mount, which
  defeats the point — don't.)
- Logs → CloudWatch (the server logs to stderr).

### 4. ALB + target group
- Target group protocol **HTTP**, port **8765**.
- **Health check:** use a **TCP**-style check (or TCP target group). The MCP
  serves the protocol at `/mcp`; a plain HTTP GET there isn't a clean `200`, so
  don't point the health check at `/`. A connect-level check is the simplest
  reliable signal. (The container also has a TCP `HEALTHCHECK` built in.)
- Terminate **TLS** at the ALB; the PAT travels over HTTPS.

### 5. Service
- Launch type **Fargate**, desired count ≥ 1 (scale freely — it's stateless).
- Security group: inbound only from the ALB; outbound 443 to reach the NUMU API.

### 6. Point clients at it
Clients that support remote MCP servers connect to
`https://<alb-domain>/mcp`.

---

## One instance per store vs. multi-store

This server is **single-store** (`NUMU_MCP_STORE_ID` is fixed per instance). For
multiple merchants, run one task/service per store (each with its own store id +
PAT), or front them with your own router. Keeping one token = one store preserves
the tenant-isolation guarantees end to end.

---

## Security checklist

- PAT in **Secrets Manager**, never in the image or task-def plaintext.
- `NUMU_MCP_AUDIT_BACKEND=api` → **no DB credentials** anywhere in the MCP.
- TLS at the ALB; restrict the security group to the ALB only.
- Mint the PAT under a least-privilege role; rotate by minting new + revoking old.
- `NUMU_MCP_VERIFY_SSL=true` (default) in production.
