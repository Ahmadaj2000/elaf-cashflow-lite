# Part of Elaf Development. See LICENSE file for full copyright and licensing details.
"""Lite edition usage counter.

The counter is a plain database row protected by the ORM: no group (not even
Settings / base.group_system) may create, write or unlink it through the ORM.
Only ``_consume`` changes it, inside the same transaction as the operation it
counts, so a failed operation rolls the increment back with it.

Honest limitation: anyone with direct SQL access (database superuser) can change
these rows. This is a fair-use counter, not DRM.
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

# Set to False to switch off cumulative caps (functional limits stay).
ELAF_USAGE_CAPS_ENABLED = True

# Lite edition limits (Python constants, intentionally not configurable).
LITE_MAX_FORECASTS = 5
LITE_MAX_HORIZON_DAYS = 7
USAGE_KEY_FORECASTS = 'cashflow_forecasts'

_INTERNAL_CTX = 'elaf_cf_usage_internal'


class ElafCashflowUsage(models.Model):
    _name = 'elaf.cashflow.usage'
    _description = 'Cash Flow Lite Usage Counter'
    _order = 'key'

    key = fields.Char(required=True, readonly=True)
    used = fields.Integer(readonly=True, default=0)
    usage_limit = fields.Integer(string='Limit', readonly=True)
    bound_company_id = fields.Many2one('res.company', string='Bound Company', readonly=True)
    last_used_at = fields.Datetime(readonly=True)

    _key_uniq = models.Constraint('unique(key)', 'One usage counter per key.')

    # ------------------------------------------------------------------
    # ORM protection
    # ------------------------------------------------------------------
    def _check_internal(self):
        if not (self.env.su and self.env.context.get(_INTERNAL_CTX)):
            raise AccessError(self.env._('Usage counters cannot be modified manually.'))

    @api.model_create_multi
    def create(self, vals_list):
        self._check_internal()
        return super().create(vals_list)

    def write(self, vals):
        self._check_internal()
        return super().write(vals)

    def unlink(self):
        self._check_internal()
        return super().unlink()

    # ------------------------------------------------------------------
    # Business API
    # ------------------------------------------------------------------
    @api.model
    def _get_row(self, key=USAGE_KEY_FORECASTS):
        Usage = self.sudo().with_context(**{_INTERNAL_CTX: True})
        row = Usage.search([('key', '=', key)], limit=1)
        if not row:
            row = Usage.create({'key': key, 'used': 0, 'usage_limit': LITE_MAX_FORECASTS})
        return row

    @api.model
    def _lock_row(self, row_id):
        """Row-level lock held until the end of the transaction."""
        self.env.cr.execute(
            'SELECT id, used, bound_company_id FROM elaf_cashflow_usage WHERE id = %s FOR UPDATE',
            (row_id,))
        return self.env.cr.fetchone()

    @api.model
    def _caps_enabled(self):
        return ELAF_USAGE_CAPS_ENABLED

    @api.model
    def _get_info(self, company=None):
        """Plain-JSON usage information for the dashboard / comparison page."""
        is_pro = self.env['elaf.cashflow.engine']._elaf_cashflow_is_pro()
        row = self.sudo().search([('key', '=', USAGE_KEY_FORECASTS)], limit=1)
        used = row.used if row else 0
        limit = LITE_MAX_FORECASTS
        company = company or self.env.company
        bound = row.bound_company_id if row else self.env['res.company']
        caps = self._caps_enabled()
        exhausted = (not is_pro) and caps and used >= limit
        wrong_company = (not is_pro) and bool(bound) and bound != company
        return {
            'is_pro': is_pro,
            'caps_enabled': caps,
            'used': used,
            'limit': limit,
            'remaining': max(0, limit - used),
            'percent': min(100, round(100.0 * used / limit)) if limit else 0,
            'exhausted': exhausted,
            'bound_company': bound.name if bound else False,
            'wrong_company': wrong_company,
            'can_generate': not exhausted and not wrong_company,
            'max_horizon': LITE_MAX_HORIZON_DAYS,
        }

    @api.model
    def _check_company(self, company):
        """Lite works with one company: raise for any other company."""
        if self.env['elaf.cashflow.engine']._elaf_cashflow_is_pro():
            return
        row = self._get_row()
        if row.bound_company_id and row.bound_company_id != company:
            raise UserError(self.env._(
                'The Lite edition works with one company: %(company)s. '
                'The Pro edition supports multi-company treasury.',
                company=row.bound_company_id.name))

    @api.model
    def _consume(self, company, record):
        """Consume one forecast calculation for ``company``.

        Must be called in the same transaction as the operation's completion.
        Returns the new used count (or False when no limit applies)."""
        if self.env['elaf.cashflow.engine']._elaf_cashflow_is_pro():
            return False
        row = self._get_row()
        locked = self._lock_row(row.id)
        used = locked[1]
        bound_id = locked[2]
        if bound_id and bound_id != company.id:
            self._log('denied', record, note='other company')
            raise UserError(self.env._(
                'The Lite edition works with one company: %(company)s. '
                'The Pro edition supports multi-company treasury.',
                company=self.env['res.company'].browse(bound_id).sudo().name))
        if self._caps_enabled() and used >= LITE_MAX_FORECASTS:
            self._log('denied', record, note='limit reached')
            raise UserError(self.env._(
                'Lite forecast allowance used (%(used)s/%(limit)s). Your saved forecasts stay '
                'available. The Pro edition removes this limit and adds 30/60/90-day horizons '
                'and scenarios.', used=used, limit=LITE_MAX_FORECASTS))
        self.env.cr.execute(
            """UPDATE elaf_cashflow_usage
                  SET used = used + 1,
                      last_used_at = now() at time zone 'UTC',
                      bound_company_id = COALESCE(bound_company_id, %s)
                WHERE id = %s
            RETURNING used""", (company.id, row.id))
        new_used = self.env.cr.fetchone()[0]
        row.invalidate_recordset(['used', 'last_used_at', 'bound_company_id'])
        self._log('consumed', record)
        return new_used

    @api.model
    def _log(self, event, record, note=False):
        self.env['elaf.cashflow.usage.log'].sudo().with_context(**{_INTERNAL_CTX: True}).create({
            'key': USAGE_KEY_FORECASTS,
            'event': event,
            'user_id': self.env.uid,
            'res_ref': record and '%s,%s' % (record._name, record.id) or False,
            'note': note,
        })


class ElafCashflowUsageLog(models.Model):
    _name = 'elaf.cashflow.usage.log'
    _description = 'Cash Flow Lite Usage Log'
    _order = 'id desc'

    key = fields.Char(required=True, readonly=True)
    event = fields.Selection(
        [('consumed', 'Consumed'), ('denied', 'Denied')], required=True, readonly=True)
    user_id = fields.Many2one('res.users', readonly=True)
    res_ref = fields.Char(string='Record', readonly=True)
    note = fields.Char(readonly=True)
    at = fields.Datetime(default=fields.Datetime.now, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not (self.env.su and self.env.context.get(_INTERNAL_CTX)):
            raise AccessError(self.env._('Usage logs are append-only and created by the system.'))
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(self.env._('Usage logs are append-only.'))

    def unlink(self):
        raise AccessError(self.env._('Usage logs are append-only.'))
