from odoo import fields, models


class DeploymentJob(models.Model):
    _name = "primacy.deployment.job"
    _description = "Primacy Deployment Job"
    _order = "create_date desc"

    module_name = fields.Char(required=True)
    artifact_url = fields.Char(required=True)
    signature_base64 = fields.Char(required=True)
    digest = fields.Char(required=True)
    nonce = fields.Char(required=True)
    expiry = fields.Float(required=True)
    
    status = fields.Selection([
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('success', 'Success'),
        ('failed', 'Failed')
    ], default='pending', required=True)
    
    logs = fields.Text()
