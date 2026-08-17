from odoo import fields, models


class DeploymentJob(models.Model):
    _name = "primacy.deployment.job"
    _description = "Primacy Deployment Job"
    _order = "create_date desc"

    job_uuid = fields.Char(required=True, index=True, copy=False)
    operation = fields.Selection([('install', 'Install'), ('upgrade', 'Upgrade')], required=True)
    module_name = fields.Char(required=True)
    module_version = fields.Char(required=True)
    artifact_url = fields.Char(required=True)
    artifact_digest = fields.Char(required=True)
    nonce = fields.Char(required=True)
    expires_at = fields.Datetime(required=True)
    signature = fields.Char(required=True)
    runner_id = fields.Char()

    status = fields.Selection([
        ('queued', 'Queued'),
        ('running', 'Running'),
        ('succeeded', 'Succeeded'),
        ('failed', 'Failed'),
        ('rolled_back', 'Rolled back'),
    ], default='queued', required=True)
    
    logs = fields.Text()
