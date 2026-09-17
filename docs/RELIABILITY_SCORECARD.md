# Reliability scorecard

Copy this file for each release or reporting period. A row is `PASS` only when
the linked evidence covers the entire scope of the row; otherwise use `PARTIAL`
or `BLOCKED`.

| Area | Status | Evidence | Owner | Reviewed at |
| --- | --- | --- | --- | --- |
| Clean install and startup | PARTIAL | CI setup job, `scripts/setup-local.sh` output |  |  |
| PostgreSQL migrations | PARTIAL | `alembic upgrade heads` and rollback evidence |  |  |
| Backend unit/integration tests | PARTIAL | CI test and coverage artifacts |  |  |
| Frontend lint/type/build | PARTIAL | CI frontend job |  |  |
| Browser critical journeys | PARTIAL | Local mocked cross-browser suite plus real disposable-backend smoke; deployed URL currently redirects to Odoo Discuss and is not this frontend (observed 2026-09-17) |  |  |
| Authentication and authorization | PARTIAL | API and browser security tests |  |  |
| ERP read-only integration | BLOCKED | Browser verified staging Odoo 19 connection (`agentai`, XML-RPC), but inspection run failed with `AgentBudgetExceeded`; deployed URL mismatch remains |  |  |
| Full agent lifecycle | BLOCKED | Requires real worker, ERP, staging, and deployment runner |  |  |
| Failure injection and recovery | PARTIAL | Worker/retry/rollback tests; outage evidence pending |  |  |
| Dependency and static security scans | PARTIAL | `pip-audit`, Bandit, and `npm audit` outputs |  |  |
| Staging deployment and rollback | BLOCKED | Disposable staging deployment evidence required; external deployment target mismatch must be resolved first |  |  |
| SLOs, metrics, and alerts | PARTIAL | `docs/RELIABILITY_OPERATIONS.md` and telemetry exports |  |  |
| Long-running/load testing | BLOCKED | Scheduled test history required |  |  |

## Release decision

- Release identifier:
- Commit:
- Environment:
- Decision: `HOLD` until no critical row is `BLOCKED`.
- Approver:
- Decision date:

Never convert `BLOCKED` to `PASS` because a mock, unit test, or static scan
passed. Replace it only after the required external environment and evidence
exist.
