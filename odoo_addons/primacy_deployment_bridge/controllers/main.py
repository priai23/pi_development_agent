import json
from odoo import http
from odoo.http import request


class PrimacyDeploymentBridge(http.Controller):

    def _check_auth(self):
        token = request.env['ir.config_parameter'].sudo().get_param('primacy_bridge.api_token')
        auth_header = request.httprequest.headers.get('Authorization')
        if not token or not auth_header or auth_header != f"Bearer {token}":
            raise request.not_found()

    @http.route('/primacy_bridge/jobs', type='http', auth='public', methods=['GET', 'POST'], csrf=False)
    def handle_jobs(self, **kwargs):
        self._check_auth()
        
        if request.httprequest.method == 'GET':
            status_filter = request.httprequest.args.get('status', 'pending')
            jobs = request.env['primacy.deployment.job'].sudo().search([('status', '=', status_filter)], limit=10)
            return request.make_response(
                json.dumps([{
                    'id': job.id,
                    'module_name': job.module_name,
                    'artifact_url': job.artifact_url,
                    'signature_base64': job.signature_base64,
                    'digest': job.digest,
                    'nonce': job.nonce,
                    'expiry': job.expiry,
                    'status': job.status,
                } for job in jobs]),
                headers=[('Content-Type', 'application/json')]
            )

        # POST creates a new job
        data = json.loads(request.httprequest.data)
        job = request.env['primacy.deployment.job'].sudo().create({
            'module_name': data.get('module_name'),
            'artifact_url': data.get('artifact_url'),
            'signature_base64': data.get('signature_base64'),
            'digest': data.get('digest'),
            'nonce': data.get('nonce'),
            'expiry': data.get('expiry'),
            'status': 'pending',
        })
        return request.make_response(
            json.dumps({'status': 'success', 'job_id': job.id}),
            headers=[('Content-Type', 'application/json')]
        )

    @http.route('/primacy_bridge/jobs/<int:job_id>', type='http', auth='public', methods=['PUT'], csrf=False)
    def update_job(self, job_id, **kwargs):
        self._check_auth()
        job = request.env['primacy.deployment.job'].sudo().browse(job_id)
        if not job.exists():
            return request.not_found()
            
        data = json.loads(request.httprequest.data)
        update_vals = {}
        if 'status' in data:
            update_vals['status'] = data['status']
        if 'logs' in data:
            # Append logs
            update_vals['logs'] = (job.logs or '') + data['logs'] + '\n'
            
        if update_vals:
            job.write(update_vals)
            
        return request.make_response(
            json.dumps({'status': 'success'}),
            headers=[('Content-Type', 'application/json')]
        )
