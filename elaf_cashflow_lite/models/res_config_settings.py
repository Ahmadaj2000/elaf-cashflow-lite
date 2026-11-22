# Part of Elaf Development. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    elaf_cf_min_cash = fields.Monetary(
        related='company_id.elaf_cf_min_cash', readonly=False,
        currency_field='currency_id')
    elaf_cf_include_outstanding = fields.Boolean(
        related='company_id.elaf_cf_include_outstanding', readonly=False)
