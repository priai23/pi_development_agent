# Odoo ERP Implementation Agent

An internal, human-approved Odoo 19 implementation agent. The application combines a FastAPI API, PostgreSQL persistence, a LangGraph agent, and a Next.js 16 frontend.

## Security model

- Local Argon2-backed accounts with administrator and member roles
- Organization-scoped projects and ERP connections
- HttpOnly session cookies, CSRF protection, explicit CORS/trusted-host configuration
- Fernet-encrypted ERP and OpenRouter credentials that are never returned by the API
- PostgreSQL-backed agent checkpoints and durable, user-bound action approvals
- Read-only tools run directly; every filesystem or supported ERP write requires explicit approval
- Server-owned workspace paths with traversal, prefix-collision, symlink, and size checks
- Audited requests, decisions, and execution results

The model never receives raw ORM methods, domains, SQL, shell commands, filesystem paths, or Git commands. It can inspect allowlisted metadata, create approved master data and draft transactions, and prepare module source. Installation and upgrades occur only through validated artifacts and the separate deployment runner.

## Prerequisites

- Python 3.11+
- Node.js 20+
- PostgreSQL
- An Odoo 19 instance whose hostname is explicitly allowed

## Backend setup

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
```

Generate `ENCRYPTION_KEY` once with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Keep it outside source control; losing or changing it makes stored credentials unreadable.

Set `ERP_ALLOWED_HOSTS` to exact approved hostnames or explicit suffixes such as `.internal.example`. Production deployments must use HTTPS, set `SECURE_COOKIES=true`, and set exact frontend and trusted-host values.

Migrations after the hardened baseline are incremental and preserve application data:

```bash
alembic upgrade head
python manage.py create-admin admin@example.com
uvicorn main:app --reload --port 8001
```

Run the durable tool/deployment worker in a second process:

```bash
cd backend
venv/bin/python worker.py
```

Agent use is disabled until an administrator configures an OpenRouter key, selects a catalogue-validated model, and sets an organization monthly budget.

## Frontend setup

```bash
cd frontend
npm install
npm run dev
```

Set `NEXT_PUBLIC_API_URL` when the API is not at `http://localhost:8001`.

## Quality gates

```bash
cd backend && pytest && python -m compileall -q .
cd frontend && npm run lint && npm test && npx tsc --noEmit && npm run build
```

After the first administrator signs in, create an organization, add projects, configure the OpenRouter model/key under Settings, and connect approved Odoo instances. Existing prototype credentials must be re-entered.

## Deployment targets

### On-premise Odoo 19

1. Install `odoo_addons/primacy_deployment_bridge` manually and generate a strong bridge token in Odoo system parameter `primacy_bridge.api_token`.
2. Create a least-privileged operating-system account that can write only the custom-addons and runner backup directories and can invoke only the configured Odoo command/service policy.
3. In the connection screen, configure the bridge URL/token. Copy the returned Ed25519 public key into a runner configuration based on `bridge_runner/config.example.json`.
4. Store `PRIMACY_BRIDGE_TOKEN` in the runner service secret store, not in its JSON file, and start `python -m bridge_runner.runner --config /etc/primacy-runner.json`.
5. Configure `PUBLIC_BASE_URL` as an HTTPS URL reachable by the runner. Allow that hostname in the runner's `allowed_artifact_hosts`.

The Odoo web process only stores and reports jobs. The runner independently verifies signature, nonce, expiry, digest, archive contents, module name, and fixed command arguments. Failed upgrades restore the retained prior artifact.

### Odoo.sh

Configure an administrator-approved `ssh://` or HTTPS repository URL, staging branch template, and encrypted deploy key on the instance. Deployments push the exact validated workspace commit to a `primacy/<project>/<release>`-style branch. Promotion remains gated on the same digest, successful staging, UAT evidence, rollback plan, and a different Class E approver.

Odoo Online custom Python deployment is intentionally unsupported.

## Recovery and operations

- The worker runs a recovery sweep on startup. Expired approvals are rejected into their waiting checkpoint; stale runs become retryable interruptions; unclaimed outbox work is returned to the queue.
- Run history includes typed tool/message/approval/usage events and support IDs. Administrators can search audit events, export safe CSV, inspect queue health, and revoke sessions.
- Back up PostgreSQL, the project workspace root, deployment-runner backups, bridge tokens, signing keys, and Odoo databases before production promotion. Rotate a bridge token or signing key by updating the application target and runner together.
