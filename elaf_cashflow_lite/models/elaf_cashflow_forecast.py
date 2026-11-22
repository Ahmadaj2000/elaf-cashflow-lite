# Part of Elaf Development. See LICENSE file for full copyright and licensing details.
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .elaf_cashflow_usage import LITE_MAX_HORIZON_DAYS

FLOW_SOURCES = [
    ('receivable', 'Customer invoice'),
    ('payable', 'Vendor bill'),
]
DOC_TYPES = [
    ('invoice', 'Invoice'),
    ('bill', 'Bill'),
    ('credit_note', 'Credit note'),
    ('payment', 'Unmatched payment'),
    ('entry', 'Journal entry'),
]

# Context key of the internal store path (``_store_result``). It is honoured only together
# with ``env.su``, which an RPC caller cannot obtain, so it cannot be forged from the client.
STORE_CTX = 'elaf_cf_store'
# Forecast fields that only the engine may set (the saved snapshot).
RESULT_FIELDS = frozenset({
    'state', 'granularity', 'date_from', 'date_to', 'computed_by_id', 'computed_at', 'threshold',
    'cash_actual', 'outstanding_net', 'include_outstanding', 'opening_balance', 'total_inflow',
    'total_outflow', 'closing_balance', 'overdue_inflow', 'overdue_outflow', 'beyond_inflow',
    'beyond_outflow', 'lowest_balance', 'lowest_date', 'shortage', 'shortage_date', 'shortage_gap',
    'max_gap', 'shortage_days', 'line_ids', 'flow_ids', 'day_ids',
})


def _is_internal_store(env):
    return env.su and bool(env.context.get(STORE_CTX))


def _check_internal_store(self):
    """Buckets, daily detail and flows are written by the engine only."""
    if not _is_internal_store(self.env):
        raise AccessError(_('Forecast buckets and flows are part of a saved forecast and '
                            'cannot be created or changed. Generate a new forecast instead.'))


class ElafCashflowForecast(models.Model):
    _name = 'elaf.cashflow.forecast'
    _description = 'Cash Flow Forecast'
    _inherit = ['mail.thread']
    _order = 'create_date desc, id desc'

    name = fields.Char(required=True, default=lambda self: _('New forecast'), tracking=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True,
                                 default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id', store=True)
    state = fields.Selection([('draft', 'Draft'), ('done', 'Computed')], default='draft',
                             required=True, readonly=True, tracking=True)
    horizon = fields.Selection([('7', '7 days')], string='Horizon', required=True, default='7')
    granularity = fields.Selection([('day', 'Daily'), ('week', 'Weekly')], readonly=True)
    date_from = fields.Date(string='Forecast Start', readonly=True,
                            help='The forecast starts today; the actual balance is measured at this date.')
    date_to = fields.Date(string='Forecast End', readonly=True)
    computed_by_id = fields.Many2one('res.users', readonly=True)
    computed_at = fields.Datetime(readonly=True)
    threshold = fields.Monetary(string='Minimum Cash Threshold', readonly=True)
    cash_actual = fields.Monetary(string='Actual Bank and Cash', readonly=True,
                                  help='Actual: posted balance of the bank and cash journal accounts.')
    outstanding_net = fields.Monetary(string='Outstanding Payments (net)', readonly=True)
    include_outstanding = fields.Boolean(readonly=True)
    opening_balance = fields.Monetary(string='Opening Balance', readonly=True)
    total_inflow = fields.Monetary(string='Expected Inflows', readonly=True)
    total_outflow = fields.Monetary(string='Expected Outflows', readonly=True)
    closing_balance = fields.Monetary(string='Projected Closing Balance', readonly=True)
    overdue_inflow = fields.Monetary(string='Overdue Receivables (first bucket)', readonly=True)
    overdue_outflow = fields.Monetary(string='Overdue Payables (first bucket)', readonly=True)
    beyond_inflow = fields.Monetary(string='Receivables Beyond Horizon', readonly=True)
    beyond_outflow = fields.Monetary(string='Payables Beyond Horizon', readonly=True)
    lowest_balance = fields.Monetary(string='Lowest Projected Balance', readonly=True)
    lowest_date = fields.Date(string='Lowest Balance Date', readonly=True)
    shortage = fields.Boolean(string='Liquidity Shortage', readonly=True)
    shortage_date = fields.Date(string='First Shortage Date', readonly=True)
    shortage_gap = fields.Monetary(string='Gap on First Shortage Date', readonly=True)
    max_gap = fields.Monetary(string='Largest Gap', readonly=True)
    shortage_days = fields.Integer(string='Days Below Threshold', readonly=True)
    line_ids = fields.One2many('elaf.cashflow.forecast.line', 'forecast_id', string='Buckets', readonly=True)
    flow_ids = fields.One2many('elaf.cashflow.forecast.flow', 'forecast_id', string='Flows', readonly=True)
    day_ids = fields.One2many('elaf.cashflow.forecast.day', 'forecast_id', string='Daily Detail', readonly=True)
    note = fields.Text()

    # ------------------------------------------------------------------
    @api.model
    def _get_engine(self):
        return self.env['elaf.cashflow.engine']

    def _compute_params(self):
        self.ensure_one()
        return self._get_engine()._prepare_params(self.company_id, {'horizon': int(self.horizon)})

    @api.constrains('horizon')
    def _check_horizon(self):
        engine = self._get_engine()
        for rec in self:
            if not engine._elaf_cashflow_is_pro() and int(rec.horizon) > LITE_MAX_HORIZON_DAYS:
                raise UserError(_(
                    'The Lite edition forecasts up to %(days)s days. 30/60/90-day horizons are a '
                    'Pro edition feature.', days=LITE_MAX_HORIZON_DAYS))

    _EDITABLE_AFTER_COMPUTE = {'name', 'note', 'message_follower_ids', 'message_ids',
                               'activity_ids'}

    @api.model
    def _result_fields(self):
        """Fields only the engine may set (extended by Pro)."""
        return set(RESULT_FIELDS)

    def _check_no_result_vals(self, vals, defaults=False):
        """Refuse figures / a computed state supplied by the caller (vals or default_* context)."""
        protected = self._result_fields()
        for key, value in vals.items():
            if key == 'state' and value in (False, 'draft'):
                continue
            if key in protected:
                raise UserError(_('The figures of a forecast are calculated by Cash Flow '
                                           'and cannot be entered. Generate the forecast instead.'))
        if defaults:
            self._check_no_result_vals({
                key[8:]: value for key, value in self.env.context.items()
                if key.startswith('default_') and key[8:] in protected})

    @api.model_create_multi
    def create(self, vals_list):
        # A forecast always starts as a draft: figures come from action_compute only.
        if not _is_internal_store(self.env):
            for vals in vals_list:
                self._check_no_result_vals(vals, defaults=True)
        return super().create(vals_list)

    def copy_data(self, default=None):
        # A copy is a new draft: the saved figures and buckets are never copied.
        vals_list = super().copy_data(default=default)
        protected = self._result_fields()
        for vals in vals_list:
            for key in protected:
                vals.pop(key, None)
            vals['state'] = 'draft'
        return vals_list

    def write(self, vals):
        # Saved forecasts are snapshots: their figures can only be written by the engine
        # (_store_result, under sudo with the internal flag; not reachable from RPC).
        if not _is_internal_store(self.env):
            self._check_no_result_vals(vals)
            if set(vals) - self._EDITABLE_AFTER_COMPUTE and any(rec.state == 'done' for rec in self):
                raise UserError(_('A computed forecast is a saved snapshot and cannot be '
                                           'changed. Generate a new forecast instead.'))
        return super().write(vals)

    def unlink(self):
        if not self.env.user.has_group('elaf_cashflow_lite.group_cashflow_manager'):
            raise UserError(_('Only Cash Flow managers can delete forecasts.'))
        return super().unlink()

    def action_compute(self):
        """Generate and save the forecast. In Lite this consumes one calculation
        (in the same transaction, after a successful computation). An already
        computed forecast is returned unchanged (idempotent, no consumption)."""
        engine = self._get_engine()
        engine._check_user()
        Usage = self.env['elaf.cashflow.usage']
        for rec in self:
            # _store_result writes under sudo: check the caller's own write access first.
            rec.check_access_rights('write')
            rec.check_access_rule('write')
            Usage._check_company(rec.company_id)
            if not engine._elaf_cashflow_is_pro():
                # Lock the usage row first, then re-check idempotency.
                Usage._lock_row(Usage._get_row().id)
                rec.invalidate_recordset(['state'])
                rec.env.cr.execute('SELECT state FROM elaf_cashflow_forecast WHERE id = %s FOR UPDATE',
                                   (rec.id,))
                if rec.env.cr.fetchone()[0] == 'done':
                    continue
            elif rec.state == 'done':
                continue
            result = engine.compute(rec._compute_params())
            rec._store_result(result)
            Usage._consume(rec.company_id, rec)
        return True

    @api.model
    def action_generate(self, filters=None):
        """Dashboard entry point: create a forecast for the current company, compute it and
        return its id."""
        engine = self._get_engine()
        engine._check_user()
        filters = dict(filters or {})
        horizon = str(int(filters.get('horizon') or LITE_MAX_HORIZON_DAYS))
        vals = self._prepare_generate_vals(filters, horizon)
        forecast = self.create(vals)
        forecast.action_compute()
        return forecast.id

    @api.model
    def _prepare_generate_vals(self, filters, horizon):
        today = fields.Date.context_today(self)
        return {
            'name': _('Forecast %(date)s (%(days)s days)', date=fields.Date.to_string(today), days=horizon),
            'horizon': horizon,
            'company_id': self.env.company.id,
        }

    def _store_result(self, result):
        """Save the engine result as the forecast snapshot. Internal: called by action_compute
        after the group, access and usage checks. sudo + the internal flag: users have read-only
        access to buckets / flows and cannot write the figures themselves."""
        self.ensure_one()
        result_vals = self._prepare_result_vals(result, result['position'])
        store = self.sudo().with_context(**{STORE_CTX: True})
        store.line_ids.unlink()
        store.flow_ids.unlink()
        store.day_ids.unlink()
        lines = store.env['elaf.cashflow.forecast.line'].create([{
            'forecast_id': self.id,
            'sequence': b['index'],
            'date_start': b['date_start'],
            'date_end': b['date_end'],
            'opening_balance': b['opening'],
            'inflow': b['inflow'],
            'outflow': b['outflow'],
            'overdue_inflow': b['overdue_inflow'],
            'overdue_outflow': b['overdue_outflow'],
            'closing_balance': b['closing'],
            'lowest_balance': b['lowest'],
            'lowest_date': b['lowest_date'],
            'shortage': b['shortage'],
            'gap': b['gap'],
        } for b in result['buckets']])
        by_index = {line.sequence: line for line in lines}
        store.env['elaf.cashflow.forecast.flow'].create([
            self._prepare_flow_vals(f, by_index.get(f.get('bucket'))) for f in result['flows']])
        store.env['elaf.cashflow.forecast.day'].create([{
            'forecast_id': self.id,
            'date': d['date'],
            'opening_balance': d['opening'],
            'inflow': d['inflow'],
            'outflow': d['outflow'],
            'closing_balance': d['closing'],
            'shortage': d['shortage'],
        } for d in result['daily']])
        store.write(result_vals)

    def _prepare_result_vals(self, result, position):
        return {
            'state': 'done',
            'granularity': result['granularity'],
            'date_from': result['date_from'],
            'date_to': result['date_to'],
            'computed_by_id': self.env.uid,
            'computed_at': fields.Datetime.now(),
            'threshold': result['threshold'],
            'cash_actual': position['cash'],
            'outstanding_net': position['outstanding_net'],
            'include_outstanding': position['include_outstanding'],
            'opening_balance': result['opening'],
            'total_inflow': result['total_inflow'],
            'total_outflow': result['total_outflow'],
            'closing_balance': result['closing'],
            'overdue_inflow': result['overdue_inflow'],
            'overdue_outflow': result['overdue_outflow'],
            'beyond_inflow': result['beyond']['inflow'],
            'beyond_outflow': result['beyond']['outflow'],
            'lowest_balance': result['lowest'],
            'lowest_date': result['lowest_date'],
            'shortage': result['shortage'],
            'shortage_date': result['shortage_date'] or False,
            'shortage_gap': result['shortage_gap'],
            'max_gap': result['max_gap'],
            'shortage_days': len([d for d in result['daily'] if d['shortage']]),
        }

    def _prepare_flow_vals(self, flow, line):
        return {
            'forecast_id': self.id,
            'line_id': line.id if line else False,
            'source': flow['source'],
            'doc_type': flow.get('doc_type') if flow.get('doc_type') in dict(DOC_TYPES) else False,
            'direction': flow['direction'],
            'amount': flow['amount'],
            'date': flow['date'],
            'bucket_date': flow.get('bucket_date'),
            'orig_date': flow.get('orig_date'),
            'overdue': flow.get('overdue'),
            'days_overdue': flow.get('days_overdue') or 0,
            'res_model': flow.get('res_model') or False,
            'res_id': flow.get('res_id') or 0,
            'move_line_id': flow.get('aml_id') or False,
            'name': flow.get('name') or '',
            'partner_id': flow.get('partner_id') or False,
            'foreign_currency': flow.get('currency') or False,
            'amount_currency': flow.get('amount_currency') or 0.0,
            'note': flow.get('note') or False,
        }

    # ------------------------------------------------------------------
    # Dashboard serialisation (same shape as a live engine result)
    # ------------------------------------------------------------------
    def _to_dashboard(self):
        self.ensure_one()
        ds = fields.Date.to_string
        engine = self._get_engine()
        lines = self.line_ids.sorted('sequence')
        flows = []
        for f in self.flow_ids:
            flows.append({
                'key': 'flow-%s' % f.id,
                'source': f.source,
                'doc_type': f.doc_type or f.source,
                'direction': f.direction,
                'amount': f.amount,
                'date': ds(f.date),
                'orig_date': ds(f.orig_date) if f.orig_date else False,
                'bucket_date': ds(f.bucket_date) if f.bucket_date else False,
                'bucket': f.line_id.sequence if f.line_id else None,
                'overdue': f.overdue,
                'days_overdue': f.days_overdue,
                'res_model': f.res_model or False,
                'res_id': f.res_id or False,
                'aml_id': f.move_line_id.id or False,
                'name': f.name or '',
                'partner_name': f.partner_id.display_name or '',
                'currency': f.foreign_currency or False,
                'amount_currency': f.amount_currency or False,
                'note': f.note or '',
            })
        return {
            'id': self.id,
            'name': self.name,
            'saved': True,
            'computed_at': fields.Datetime.to_string(self.computed_at) if self.computed_at else False,
            'computed_by': self.computed_by_id.name or '',
            'date_from': ds(self.date_from),
            'date_to': ds(self.date_to),
            'horizon': int(self.horizon),
            'granularity': self.granularity,
            'threshold': self.threshold,
            'opening': self.opening_balance,
            'position': {
                'cash': self.cash_actual,
                'outstanding_net': self.outstanding_net,
                'include_outstanding': self.include_outstanding,
                'opening': self.opening_balance,
            },
            'buckets': [{
                'index': line.sequence,
                'date_start': ds(line.date_start),
                'date_end': ds(line.date_end),
                'opening': line.opening_balance,
                'inflow': line.inflow,
                'outflow': line.outflow,
                'overdue_inflow': line.overdue_inflow,
                'overdue_outflow': line.overdue_outflow,
                'net': line.net,
                'closing': line.closing_balance,
                'lowest': line.lowest_balance,
                'lowest_date': ds(line.lowest_date) if line.lowest_date else False,
                'shortage': line.shortage,
                'gap': line.gap,
                'inflow_count': len(line.flow_ids.filtered(lambda f: f.direction == 'in')),
                'outflow_count': len(line.flow_ids.filtered(lambda f: f.direction == 'out')),
            } for line in lines],
            'daily': [{
                'date': ds(d.date), 'opening': d.opening_balance, 'inflow': d.inflow,
                'outflow': d.outflow, 'closing': d.closing_balance, 'shortage': d.shortage,
            } for d in self.day_ids.sorted('date')],
            'flows': flows,
            'beyond': {'inflow': self.beyond_inflow, 'outflow': self.beyond_outflow},
            'total_inflow': self.total_inflow,
            'total_outflow': self.total_outflow,
            'closing': self.closing_balance,
            'lowest': self.lowest_balance,
            'lowest_date': ds(self.lowest_date) if self.lowest_date else False,
            'shortage': self.shortage,
            'shortage_date': ds(self.shortage_date) if self.shortage_date else False,
            'shortage_gap': self.shortage_gap,
            'max_gap': self.max_gap,
            'reconciles': self.currency_id.is_zero(
                self.opening_balance + self.total_inflow - self.total_outflow - self.closing_balance),
            'overdue_inflow': self.overdue_inflow,
            'overdue_outflow': self.overdue_outflow,
            'extra': engine._forecast_extra(self),
        }

    # ------------------------------------------------------------------
    # Drill-down buttons (form view)
    # ------------------------------------------------------------------
    def _action_flows(self, domain, name):
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': 'elaf.cashflow.forecast.flow',
            'view_mode': 'tree,form',
            'domain': domain,
            'context': {'create': False},
        }

    def action_view_inflows(self):
        self.ensure_one()
        return self._action_flows([('forecast_id', '=', self.id), ('direction', '=', 'in')],
                                  _('Expected Inflows'))

    def action_view_outflows(self):
        self.ensure_one()
        return self._action_flows([('forecast_id', '=', self.id), ('direction', '=', 'out')],
                                  _('Expected Outflows'))

    def action_open_dashboard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'elaf_cashflow_dashboard',
            'name': _('Cash Flow'),
            'params': {'forecast_id': self.id},
        }


class ElafCashflowForecastLine(models.Model):
    _name = 'elaf.cashflow.forecast.line'
    _description = 'Cash Flow Forecast Bucket'
    _order = 'forecast_id, sequence'

    @api.model_create_multi
    def create(self, vals_list):
        _check_internal_store(self)
        return super().create(vals_list)

    def write(self, vals):
        _check_internal_store(self)
        return super().write(vals)

    forecast_id = fields.Many2one('elaf.cashflow.forecast', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='forecast_id.company_id', store=True)
    currency_id = fields.Many2one(related='forecast_id.currency_id', store=True)
    sequence = fields.Integer()
    date_start = fields.Date(string='From')
    date_end = fields.Date(string='To')
    opening_balance = fields.Monetary(string='Opening')
    inflow = fields.Monetary(string='Inflows')
    outflow = fields.Monetary(string='Outflows')
    overdue_inflow = fields.Monetary(string='of which Overdue Inflows')
    overdue_outflow = fields.Monetary(string='of which Overdue Outflows')
    net = fields.Monetary(string='Net', compute='_compute_net', store=True)
    closing_balance = fields.Monetary(string='Projected Closing')
    lowest_balance = fields.Monetary(string='Lowest Projected')
    lowest_date = fields.Date(string='Lowest On')
    shortage = fields.Boolean(string='Shortage')
    gap = fields.Monetary(string='Gap to Threshold')
    flow_ids = fields.One2many('elaf.cashflow.forecast.flow', 'line_id', string='Flows')

    @api.depends('inflow', 'outflow')
    def _compute_net(self):
        for line in self:
            line.net = line.inflow - line.outflow

    def _action_flows(self, direction):
        self.ensure_one()
        name = _('Inflows %(start)s - %(end)s', start=self.date_start, end=self.date_end) \
            if direction == 'in' else \
            _('Outflows %(start)s - %(end)s', start=self.date_start, end=self.date_end)
        return self.forecast_id._action_flows(
            [('line_id', '=', self.id), ('direction', '=', direction)], name)

    def action_view_inflows(self):
        return self._action_flows('in')

    def action_view_outflows(self):
        return self._action_flows('out')


class ElafCashflowForecastDay(models.Model):
    _name = 'elaf.cashflow.forecast.day'
    _description = 'Cash Flow Forecast Daily Detail'
    _order = 'forecast_id, date'

    @api.model_create_multi
    def create(self, vals_list):
        _check_internal_store(self)
        return super().create(vals_list)

    def write(self, vals):
        _check_internal_store(self)
        return super().write(vals)

    forecast_id = fields.Many2one('elaf.cashflow.forecast', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='forecast_id.company_id', store=True)
    currency_id = fields.Many2one(related='forecast_id.currency_id', store=True)
    date = fields.Date()
    opening_balance = fields.Monetary(string='Opening')
    inflow = fields.Monetary(string='Inflows')
    outflow = fields.Monetary(string='Outflows')
    closing_balance = fields.Monetary(string='Projected Closing')
    shortage = fields.Boolean()


class ElafCashflowForecastFlow(models.Model):
    _name = 'elaf.cashflow.forecast.flow'
    _description = 'Cash Flow Forecast Flow'
    _order = 'bucket_date, direction, id'

    @api.model_create_multi
    def create(self, vals_list):
        _check_internal_store(self)
        return super().create(vals_list)

    def write(self, vals):
        _check_internal_store(self)
        return super().write(vals)

    forecast_id = fields.Many2one('elaf.cashflow.forecast', required=True, ondelete='cascade', index=True)
    line_id = fields.Many2one('elaf.cashflow.forecast.line', string='Bucket', ondelete='cascade', index=True)
    company_id = fields.Many2one(related='forecast_id.company_id', store=True)
    currency_id = fields.Many2one(related='forecast_id.currency_id', store=True)
    source = fields.Selection(FLOW_SOURCES, required=True)
    doc_type = fields.Selection(DOC_TYPES, string='Document Type')
    direction = fields.Selection([('in', 'Inflow'), ('out', 'Outflow')], required=True)
    amount = fields.Monetary(help='Amount in company currency (foreign residuals converted at the forecast date).')
    date = fields.Date(string='Expected Date')
    orig_date = fields.Date(string='Due Date')
    bucket_date = fields.Date(string='Bucket Date')
    overdue = fields.Boolean()
    days_overdue = fields.Integer()
    res_model = fields.Char(string='Document Model')
    res_id = fields.Many2oneReference(model_field='res_model', string='Document ID')
    move_line_id = fields.Many2one('account.move.line', string='Journal Item', ondelete='set null')
    name = fields.Char(string='Document')
    partner_id = fields.Many2one('res.partner', ondelete='set null')
    foreign_currency = fields.Char(string='Original Currency')
    amount_currency = fields.Float(string='Original Amount')
    note = fields.Char()

    def action_open_document(self):
        self.ensure_one()
        if not self.res_model or not self.res_id:
            raise UserError(_('This flow has no source document.'))
        return self.env['elaf.cashflow.engine'].action_open_document(self.res_model, self.res_id)
