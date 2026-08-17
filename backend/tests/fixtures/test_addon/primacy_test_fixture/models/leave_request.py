from odoo import fields, models, api


class LeaveRequest(models.Model):
    _name = "primacy.leave.request"
    _description = "Leave Request"
    _order = "date_from desc"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(string="Reference", required=True)
    employee_id = fields.Many2one("hr.employee", string="Employee", required=True)
    date_from = fields.Date(string="From Date", required=True)
    date_to = fields.Date(string="To Date", required=True)
    leave_type_id = fields.Many2one("hr.leave.type", string="Leave Type", required=True)
    reason = fields.Text(string="Reason")
    state = fields.Selection(
        [("draft", "Draft"), ("submitted", "Submitted"), ("approved", "Approved"), ("refused", "Refused")],
        string="State",
        default="draft",
        tracking=True,
    )
    approved_by = fields.Many2one("res.users", string="Approved By", readonly=True)
    approved_date = fields.Datetime(string="Approval Date", readonly=True)

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for rec in self:
            if rec.date_from and rec.date_to and rec.date_from > rec.date_to:
                raise ValueError("Start date must be before or equal to end date.")

    @api.depends("date_from", "date_to")
    def _compute_duration(self):
        for rec in self:
            if rec.date_from and rec.date_to:
                rec.duration_days = (rec.date_to - rec.date_from).days + 1
            else:
                rec.duration_days = 0

    duration_days = fields.Integer(string="Duration (days)", compute="_compute_duration", store=True)

    def action_submit(self):
        self.write({"state": "submitted"})

    def action_approve(self):
        self.write({
            "state": "approved",
            "approved_by": self.env.user.id,
            "approved_date": fields.Datetime.now(),
        })

    def action_refuse(self):
        self.write({"state": "refused"})
