import hmac

from odoo import fields, http
from odoo.http import request
from werkzeug.exceptions import Forbidden, NotFound


def _authenticate():
    configured = request.env["ir.config_parameter"].sudo().get_param("primacy_bridge.api_token", "")
    supplied = request.httprequest.headers.get("Authorization", "").removeprefix("Bearer ")
    if not configured or not supplied or not hmac.compare_digest(configured, supplied):
        raise Forbidden()


class PrimacyDeploymentBridge(http.Controller):
    @http.route("/primacy/bridge/v1/health", type="json", auth="none", methods=["POST"], csrf=False)
    def health(self):
        _authenticate()
        return {"status": "ok", "version": "1"}

    @http.route("/primacy/bridge/v1/jobs", type="json", auth="none", methods=["POST"], csrf=False)
    def create_job(self, **payload):
        _authenticate()
        allowed = {
            "job_uuid", "operation", "module_name", "module_version", "artifact_url",
            "artifact_digest", "nonce", "expires_at", "signature",
        }
        if set(payload) != allowed:
            raise Forbidden("Unexpected job fields")
        job = request.env["primacy.deployment.job"].sudo().create(payload)
        return {"job_uuid": job.job_uuid, "state": job.status}

    @http.route("/primacy/bridge/v1/jobs/next", type="json", auth="none", methods=["POST"], csrf=False)
    def next_job(self, runner_id=None):
        _authenticate()
        job = request.env["primacy.deployment.job"].sudo().search([
            ("status", "=", "queued"), ("expires_at", ">", fields.Datetime.now())
        ], order="create_date", limit=1)
        if not job:
            return None
        job.write({"status": "running", "runner_id": runner_id})
        return {field: job[field] for field in (
            "job_uuid", "operation", "module_name", "module_version", "artifact_url",
            "artifact_digest", "nonce", "expires_at", "signature",
        )}

    @http.route("/primacy/bridge/v1/jobs/status", type="json", auth="none", methods=["POST"], csrf=False)
    def job_status(self, job_uuid=None):
        _authenticate()
        job = request.env["primacy.deployment.job"].sudo().search([("job_uuid", "=", job_uuid)], limit=1)
        if not job:
            raise NotFound()
        return {"job_uuid": job.job_uuid, "state": job.status, "logs": job.logs or ""}

    @http.route("/primacy/bridge/v1/jobs/<string:job_uuid>/result", type="json", auth="none", methods=["POST"], csrf=False)
    def job_result(self, job_uuid, state=None, logs=None, result_digest=None):
        _authenticate()
        job = request.env["primacy.deployment.job"].sudo().search([("job_uuid", "=", job_uuid)], limit=1)
        if not job:
            raise NotFound()
        if state not in {"running", "succeeded", "failed", "rolled_back"}:
            raise Forbidden("Invalid result state")
        job.write({"status": state, "logs": (logs or "")[-100000:]})
        return {"job_uuid": job.job_uuid, "state": job.status}
