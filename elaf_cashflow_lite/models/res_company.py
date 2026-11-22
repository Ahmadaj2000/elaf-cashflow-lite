# Part of Elaf Development. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    elaf_cf_min_cash = fields.Monetary(
        string='Minimum Cash Threshold',
        currency_field='currency_id',
        default=0.0,
        help='Projected balances below this amount are flagged as a liquidity shortage.',
    )
    elaf_cf_include_outstanding = fields.Boolean(
        string='Include Outstanding Payments in Opening Balance',
        default=True,
        help='When enabled, the forecast opening balance is the bank and cash ledger balance '
             'plus outstanding receipts minus outstanding payments (payments registered in Odoo '
             'but not yet matched with a bank statement line). They are always shown separately.',
    )
