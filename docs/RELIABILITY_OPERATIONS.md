# Reliability operations

This document defines the minimum production operating contract for the agent.
It is intentionally measurable; “100% reliable” is not a valid operational
claim.

## Service objectives

Measure these over a rolling 30-day window from structured request/run/deployment
events:

| Signal | Objective | Alert threshold |
| --- | --- | --- |
| API availability | 99.9% successful health responses | 1% failed checks over 5 minutes |
| API latency | 99% of non-stream requests under 2 seconds | p99 over 5 seconds for 10 minutes |
| Worker liveness | 99.9% of intervals with a fresh lease | lease older than 30 seconds |
| Run completion | 99% of runs reach a terminal state within configured timeout | 5 stalled runs in 10 minutes |
| Deployment safety | 100% of production mutations have staging evidence and backup | any mutation without either |
| Authorization safety | 0 unauthorized writes | any confirmed violation |

## Health and diagnosis

1. Check `GET /health` and record `worker`, `queue_depth`, and
   `worker_last_seen_at`.
2. Check `GET /admin/health` as an administrator and capture the support ID
   from the affected run or audit event.
3. Inspect queue depth, retry counts, failure category, and the last tool event
   for the support ID.
4. Do not retry a write blindly. Confirm idempotency state and approval status
   first.

## Recovery procedures

### Worker unavailable

- Stop new deployments.
- Confirm the API remains healthy and the outbox queue is durable.
- Restart one worker and verify its lease becomes fresh.
- Confirm stale runs are marked `interrupted` and `retryable`.
- Retry only the affected run after reviewing its audit trail.

### Database unavailable

- Stop workers and mutation requests.
- Preserve logs and the database error/support ID.
- Restore PostgreSQL connectivity and run `alembic upgrade heads`.
- Verify `GET /health`, queue depth, and checkpoint access before resuming.

### Failed deployment

- Keep the failed environment isolated.
- Confirm the artifact hash, backup, smoke-test result, and recovery state.
- Execute the approved rollback path and verify the previous artifact is live.
- Run smoke tests again; do not declare recovery from process exit alone.

### Suspected unauthorized action

- Immediately revoke the affected session and pause mutation workers.
- Preserve audit events, request metadata, and artifact hashes.
- Confirm approval ownership, expiry, idempotency key, and permission scope.
- Rotate affected credentials only after evidence is preserved.

## Required evidence for release

Every release record must link to:

- CI static, unit, integration, security, and browser results.
- PostgreSQL migration and health-check output.
- Staging deployment, smoke test, backup, and rollback evidence.
- Open incidents and their regression tests, or an explicit zero-incident report.

The release is not production-ready until each item is present and reviewed by
an operator with authority over the target environment.
