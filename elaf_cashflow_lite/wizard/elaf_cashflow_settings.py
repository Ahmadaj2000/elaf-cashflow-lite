# Part of Elaf Development. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models
from odoo.exceptions import AccessError


class ElafCashflowSettings(models.TransientModel):
    """Cash Flow settings for Cash Flow managers (who are not necessarily Settings admins)."""
    _name = 'elaf.cashflow.settings'
    _description = 'Cash Flow Settings'

    company_id = fields.Many2one('res.company', required=True, readonly=True,
                                 default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    min_cash = fields.Monetary(string='Minimum Cash Threshold', currency_field='currency_id',
                               help='Projected balances below this amount are flagged as a liquidity shortage.')
    include_outstanding = fields.Boolean(
        string='Include Outstanding Payments in Opening Balance',
        help='Add outstanding receipts and subtract outstanding payments (registered payments not '
             'yet matched with a bank statement) to the opening balance of the forecast.')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        company = self.env.company
        res.setdefault('min_cash', company.elaf_cf_min_cash)
        res.setdefault('include_outstanding', company.elaf_cf_include_outstanding)
        return res

    def action_save(self):
        self.ensure_one()
        if not self.env.user.has_group('elaf_cashflow_lite.group_cashflow_manager'):
            raise AccessError(self.env._('Only Cash Flow managers can change these settings.'))
        if self.company_id not in self.env.companies:
            raise AccessError(self.env._('You cannot change settings of this company.'))
        # sudo: Cash Flow managers may not have write access on res.company; the group and
        # company checks above authorise exactly these two fields.
        self.company_id.sudo().write({
            'elaf_cf_min_cash': self.min_cash,
            'elaf_cf_include_outstanding': self.include_outstanding,
        })
        return {'type': 'ir.actions.client', 'tag': 'elaf_cashflow_dashboard'}
