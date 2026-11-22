# Part of Elaf Development. See LICENSE file for full copyright and licensing details.
"""Cash flow computation engine (pure computation, returns plain Python data).

Definitions (all amounts in the company currency):

* Actual cash = posted balance (date <= today) of the default accounts of the
  company's bank and cash journals.
* Outstanding receipts / payments = posted balance of the payment-method
  accounts of those journals (payments registered but not yet matched with a
  bank statement). Shown separately; added to the opening balance when the
  company setting "Include outstanding payments" is enabled.
* Inflows = open residuals of posted receivable journal items, outflows = open
  residuals of posted payable journal items, dated by due date (date_maturity,
  falling back to the accounting date). Foreign-currency residuals are converted
  at today's rate. Items due before today are overdue and placed in the first
  bucket, flagged as overdue. Items due after the horizon are reported as
  "beyond horizon" and excluded.
* Drafts and cancelled documents are never cash, receivables or payables: only posted journal
  items are read. Partial payments and credit notes are already netted in ``amount_residual``;
  unmatched payments sit on the receivable / payable account with the opposite sign and reduce
  the open total until they are matched (their cash is in the outstanding accounts above).
* Reconciliation invariant (S5-C3): every forecast flow (in the horizon or beyond it) comes
  from exactly one source; the engine total per source equals an
  independent SQL sum (``_reconciliation``): inflows = posted open receivable residuals (+ Pro
  sources), outflows = posted open payable residuals (+ Pro sources).
* Bucket closing = bucket opening + inflows - outflows; next opening = closing.
* A bucket is a shortage when its lowest daily projected balance is below the
  minimum cash threshold; the gap is threshold - lowest balance.
"""
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare, float_is_zero

from .elaf_cashflow_usage import LITE_MAX_HORIZON_DAYS


class ElafCashflowEngine(models.AbstractModel):
    _name = 'elaf.cashflow.engine'
    _description = 'Cash Flow Forecast Engine'

    # ------------------------------------------------------------------
    # Edition hook (Pro overrides this and every limit disappears)
    # ------------------------------------------------------------------
    def _elaf_cashflow_is_pro(self):
        return False

    @api.model
    def _allowed_horizons(self):
        return [LITE_MAX_HORIZON_DAYS]

    @api.model
    def _check_user(self):
        if not self.env.user.has_group('elaf_cashflow_lite.group_cashflow_user'):
            raise AccessError(_('You need the Cash Flow / User access right.'))

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------
    @api.model
    def _prepare_params(self, company, filters=None):
        """Normalise user filters into computation parameters."""
        filters = dict(filters or {})
        today = fields.Date.context_today(self)
        if filters.get('date_from'):
            today = fields.Date.to_date(filters['date_from'])
        horizon = int(filters.get('horizon') or LITE_MAX_HORIZON_DAYS)
        if horizon not in self._allowed_horizons():
            if not self._elaf_cashflow_is_pro() and horizon > LITE_MAX_HORIZON_DAYS:
                raise UserError(_(
                    'The Lite edition forecasts up to %(days)s days. 30/60/90-day horizons are '
                    'a Pro edition feature.', days=LITE_MAX_HORIZON_DAYS))
            raise UserError(_('Unsupported forecast horizon: %s days.', horizon))
        threshold = filters.get('threshold')
        if threshold in (None, False, ''):
            threshold = company.elaf_cf_min_cash
        return {
            'company': company,
            'date_from': today,
            'horizon': horizon,
            'granularity': 'day' if horizon <= 7 else 'week',
            'threshold': float(threshold or 0.0),
            'include_outstanding': company.elaf_cf_include_outstanding,
            'filters': filters,
        }

    # ------------------------------------------------------------------
    # Actual cash position
    # ------------------------------------------------------------------
    @api.model
    def _liquidity_journals(self, company):
        return self.env['account.journal'].search([
            ('company_id', '=', company.id), ('type', 'in', ('bank', 'cash'))], order='sequence, id')

    @api.model
    def _outstanding_accounts(self, journals, company):
        """Outstanding receipt / payment accounts: those set on the journals' payment methods
        plus the chart-template defaults that Odoo uses when a method has none."""
        bank_accounts = journals.default_account_id
        receipts = journals.inbound_payment_method_line_ids.payment_account_id
        payments = journals.outbound_payment_method_line_ids.payment_account_id
        # Odoo 17 keeps the default outstanding accounts on the company.
        receipts |= company.account_journal_payment_debit_account_id
        payments |= company.account_journal_payment_credit_account_id
        return receipts - bank_accounts, payments - bank_accounts

    @api.model
    def _account_balances(self, company, accounts, date_to):
        if not accounts:
            return {}
        groups = self.env['account.move.line']._read_group(
            [('company_id', '=', company.id), ('account_id', 'in', accounts.ids),
             ('parent_state', '=', 'posted'), ('date', '<=', date_to)],
            ['account_id'], ['balance:sum'])
        return {account.id: balance for account, balance in groups}

    @api.model
    def _get_cash_position(self, company, date_to=None, include_outstanding=None):
        date_to = date_to or fields.Date.context_today(self)
        if include_outstanding is None:
            include_outstanding = company.elaf_cf_include_outstanding
        cur = company.currency_id
        journals = self._liquidity_journals(company)
        receipts_acc, payments_acc = self._outstanding_accounts(journals, company)
        balances = self._account_balances(
            company, journals.default_account_id | receipts_acc | payments_acc, date_to)
        rows = []
        seen = set()
        for journal in journals:
            account = journal.default_account_id
            if not account or account.id in seen:
                continue
            seen.add(account.id)
            rows.append({
                'journal_id': journal.id,
                'journal_name': journal.name,
                'journal_type': journal.type,
                'account_id': account.id,
                'account_name': account.display_name,
                'balance': cur.round(balances.get(account.id, 0.0)),
            })
        outstanding = []
        for account in receipts_acc | payments_acc:
            if account in receipts_acc and account in payments_acc:
                kind = 'both'
            else:
                kind = 'receipts' if account in receipts_acc else 'payments'
            outstanding.append({
                'kind': kind,
                'account_id': account.id,
                'account_name': account.display_name,
                'balance': cur.round(balances.get(account.id, 0.0)),
            })
        cash = cur.round(sum(r['balance'] for r in rows))
        # Debit balances = money received not yet on a statement; credit = money paid out.
        out_receipts = cur.round(sum(o['balance'] for o in outstanding if o['balance'] > 0))
        out_payments = cur.round(sum(o['balance'] for o in outstanding if o['balance'] < 0))
        outstanding_net = cur.round(out_receipts + out_payments)
        opening = cur.round(cash + (outstanding_net if include_outstanding else 0.0))
        return {
            'date': fields.Date.to_string(date_to),
            'journals': rows,
            'cash': cash,
            'outstanding': outstanding,
            'outstanding_receipts': out_receipts,
            # payments are credit balances (negative); expose the positive amount too
            'outstanding_payments': out_payments,
            'outstanding_net': outstanding_net,
            'include_outstanding': bool(include_outstanding),
            'opening': opening,
            'liquidity_account_ids': journals.default_account_id.ids,
            'outstanding_account_ids': (receipts_acc | payments_acc).ids,
        }

    # ------------------------------------------------------------------
    # Flows
    # ------------------------------------------------------------------
    @api.model
    def _open_items_domain(self, company, kind):
        account_type = 'asset_receivable' if kind == 'in' else 'liability_payable'
        return [
            ('company_id', '=', company.id),
            ('parent_state', '=', 'posted'),
            ('account_id.account_type', '=', account_type),
            ('reconciled', '=', False),
            '|', ('amount_residual', '!=', 0.0), ('amount_residual_currency', '!=', 0.0),
        ]

    @api.model
    def _line_amount_company(self, line, company, date):
        """Open residual of ``line`` in company currency (foreign residual converted at ``date``)."""
        cur = company.currency_id
        if line.currency_id and line.currency_id != cur:
            return line.currency_id._convert(line.amount_residual_currency, cur, company, date)
        return cur.round(line.amount_residual)

    @api.model
    def _source_label(self, line, kind):
        move = line.move_id
        if move.move_type in ('out_invoice', 'in_invoice', 'out_receipt', 'in_receipt'):
            return 'invoice' if kind == 'in' else 'bill'
        if move.move_type in ('out_refund', 'in_refund'):
            return 'credit_note'
        if move.payment_id:
            return 'payment'
        return 'entry'

    @api.model
    def _collect_open_items(self, params):
        company = params['company']
        today = params['date_from']
        flows = []
        AML = self.env['account.move.line']
        for kind in ('in', 'out'):
            lines = AML.search(self._open_items_domain(company, kind), order='date_maturity, id')
            for line in lines:
                amount = self._line_amount_company(line, company, today)
                if kind == 'out':
                    amount = -amount
                if company.currency_id.is_zero(amount):
                    continue
                due = line.date_maturity or line.date
                flows.append({
                    'key': 'aml-%s' % line.id,
                    'source': 'receivable' if kind == 'in' else 'payable',
                    'doc_type': self._source_label(line, kind),
                    'direction': kind,
                    'amount': amount,
                    'date': due,
                    'orig_date': due,
                    'res_model': 'account.move',
                    'res_id': line.move_id.id,
                    'aml_id': line.id,
                    'name': line.move_id.name or line.name or '',
                    'partner_id': line.partner_id.id or False,
                    'partner_name': line.partner_id.display_name or '',
                    'currency': line.currency_id.name if line.currency_id != company.currency_id else False,
                    'amount_currency': abs(line.amount_residual_currency)
                    if line.currency_id != company.currency_id else False,
                    'note': '',
                })
        return flows

    @api.model
    def _collect_flows(self, params):
        """Extension point: Pro adds pipeline and recurring items."""
        return self._collect_open_items(params)

    @api.model
    def _apply_assumptions(self, flows, params):
        """Extension point: Pro applies scenario assumptions. Lite: identity."""
        return flows

    # ------------------------------------------------------------------
    # Buckets
    # ------------------------------------------------------------------
    @api.model
    def _make_buckets(self, date_from, horizon, granularity):
        date_to = date_from + timedelta(days=horizon - 1)
        step = 1 if granularity == 'day' else 7
        buckets = []
        start = date_from
        while start <= date_to:
            end = min(start + timedelta(days=step - 1), date_to)
            buckets.append({'index': len(buckets), 'date_start': start, 'date_end': end})
            start = end + timedelta(days=1)
        return buckets, date_to

    @api.model
    def compute(self, params):
        """Run the forecast. Returns a dict with plain Python values (dates as date objects)."""
        if not self.env.su:
            self._check_user()
        company = params['company']
        cur = company.currency_id
        date_from = params['date_from']
        position = self._get_cash_position(company, date_from, params['include_outstanding'])
        flows = self._apply_assumptions(self._collect_flows(params), params)
        source_totals = self._source_totals(flows, cur)
        buckets, date_to = self._make_buckets(date_from, params['horizon'], params['granularity'])
        threshold = params['threshold']

        day_in = defaultdict(float)
        day_out = defaultdict(float)
        beyond = {'inflow': 0.0, 'outflow': 0.0, 'count': 0}
        kept = []
        for flow in flows:
            exp = flow['date']
            # overdue = already past its original due date (even if an assumption moved it later)
            flow['overdue'] = bool(flow.get('overdue')) or (flow.get('orig_date') or exp) < date_from
            flow['days_overdue'] = (date_from - flow['orig_date']).days \
                if flow['orig_date'] and flow['orig_date'] < date_from else 0
            if exp > date_to:
                beyond['inflow' if flow['direction'] == 'in' else 'outflow'] += flow['amount']
                beyond['count'] += 1
                continue
            day = max(exp, date_from)
            flow['bucket_date'] = day
            (day_in if flow['direction'] == 'in' else day_out)[day] += flow['amount']
            kept.append(flow)

        # daily projection
        daily = []
        balance = position['opening']
        day = date_from
        while day <= date_to:
            inflow = cur.round(day_in.get(day, 0.0))
            outflow = cur.round(day_out.get(day, 0.0))
            opening = balance
            balance = cur.round(balance + inflow - outflow)
            daily.append({'date': day, 'opening': opening, 'inflow': inflow,
                          'outflow': outflow, 'closing': balance,
                          'shortage': float_compare(balance, threshold, precision_rounding=cur.rounding) < 0})
            day += timedelta(days=1)

        # buckets
        opening = position['opening']
        for bucket in buckets:
            days = [d for d in daily if bucket['date_start'] <= d['date'] <= bucket['date_end']]
            bflows = [f for f in kept if bucket['date_start'] <= f['bucket_date'] <= bucket['date_end']]
            for f in bflows:
                f['bucket'] = bucket['index']
            inflow = cur.round(sum(d['inflow'] for d in days))
            outflow = cur.round(sum(d['outflow'] for d in days))
            closing = cur.round(opening + inflow - outflow)
            low = min(days, key=lambda d: d['closing'])
            gap = cur.round(threshold - low['closing'])
            bucket.update({
                'opening': opening,
                'inflow': inflow,
                'outflow': outflow,
                'overdue_inflow': cur.round(sum(f['amount'] for f in bflows
                                                if f['direction'] == 'in' and f['overdue'])),
                'overdue_outflow': cur.round(sum(f['amount'] for f in bflows
                                                 if f['direction'] == 'out' and f['overdue'])),
                'net': cur.round(inflow - outflow),
                'closing': closing,
                'lowest': low['closing'],
                'lowest_date': low['date'],
                'shortage': gap > 0 and not cur.is_zero(gap),
                'gap': gap if gap > 0 else 0.0,
                'inflow_count': len([f for f in bflows if f['direction'] == 'in']),
                'outflow_count': len([f for f in bflows if f['direction'] == 'out']),
            })
            opening = closing

        total_in = cur.round(sum(b['inflow'] for b in buckets))
        total_out = cur.round(sum(b['outflow'] for b in buckets))
        closing = buckets[-1]['closing'] if buckets else position['opening']
        lowest = min(daily, key=lambda d: d['closing']) if daily else None
        first_short = next((d for d in daily if d['shortage']), None)
        max_gap = max([b['gap'] for b in buckets] or [0.0])
        check = cur.round(position['opening'] + total_in - total_out - closing)
        result = {
            'company_id': company.id,
            'currency_id': cur.id,
            'date_from': date_from,
            'date_to': date_to,
            'horizon': params['horizon'],
            'granularity': params['granularity'],
            'threshold': threshold,
            'position': position,
            'opening': position['opening'],
            'buckets': buckets,
            'daily': daily,
            'flows': kept,
            'beyond': {k: (cur.round(v) if k != 'count' else v) for k, v in beyond.items()},
            'total_inflow': total_in,
            'total_outflow': total_out,
            'closing': closing,
            'lowest': lowest['closing'] if lowest else position['opening'],
            'lowest_date': lowest['date'] if lowest else date_from,
            'shortage': bool(first_short),
            'shortage_date': first_short['date'] if first_short else False,
            'shortage_gap': cur.round(threshold - first_short['closing']) if first_short else 0.0,
            'max_gap': max_gap,
            'reconciles': cur.is_zero(check),
            'overdue_inflow': cur.round(sum(f['amount'] for f in kept if f['direction'] == 'in' and f['overdue'])),
            'overdue_outflow': cur.round(sum(f['amount'] for f in kept if f['direction'] == 'out' and f['overdue'])),
            'source_totals': source_totals,
        }
        result['reconciliation'] = self._reconciliation(params, result)
        return result

    # ------------------------------------------------------------------
    # Reconciliation (S5-C3 / S5-C6): engine totals per source vs independent SQL sums
    # ------------------------------------------------------------------
    @api.model
    def _source_totals(self, flows, cur):
        """Engine totals per source over ALL collected flows (in the horizon and beyond)."""
        totals = {}
        for flow in flows:
            row = totals.setdefault(flow['source'], {'in': 0.0, 'out': 0.0, 'count': 0})
            row[flow['direction']] += flow['amount']
            row['count'] += 1
        return {k: {'in': cur.round(v['in']), 'out': cur.round(v['out']), 'count': v['count']}
                for k, v in totals.items()}

    @api.model
    def _independent_open_total(self, company, kind, date):
        """Posted open receivable (kind 'in') or payable (kind 'out') residuals, summed in SQL
        per currency and converted once per currency at ``date``. Positive = expected inflow
        (receivables) / expected outflow (payables). Returns (amount, number of foreign lines)."""
        account_type = 'asset_receivable' if kind == 'in' else 'liability_payable'
        cur = company.currency_id
        self.env['account.move.line'].flush_model()
        self.env['account.move'].flush_model(['state'])
        self.env.cr.execute("""
            SELECT aml.currency_id, SUM(aml.amount_residual), SUM(aml.amount_residual_currency), COUNT(*)
              FROM account_move_line aml
              JOIN account_account acc ON acc.id = aml.account_id
             WHERE aml.company_id = %s
               AND aml.parent_state = 'posted'
               AND acc.account_type = %s
               AND aml.reconciled IS NOT TRUE
          GROUP BY aml.currency_id
        """, (company.id, account_type))
        total = 0.0
        foreign_lines = 0
        for currency_id, residual, residual_currency, count in self.env.cr.fetchall():
            currency = self.env['res.currency'].browse(currency_id)
            if currency and currency != cur:
                total += currency._convert(residual_currency or 0.0, cur, company, date)
                foreign_lines += count
            else:
                total += residual or 0.0
        total = cur.round(total)
        return (total if kind == 'in' else -total), foreign_lines

    @api.model
    def _reconciliation_checks(self, params, result):
        """[(source, direction, label, expected, converted_lines)]; Pro adds its sources."""
        company = params['company']
        date = params['date_from']
        ar, ar_fx = self._independent_open_total(company, 'in', date)
        ap, ap_fx = self._independent_open_total(company, 'out', date)
        return [
            ('receivable', 'in', _('Posted open receivable residuals'), ar, ar_fx),
            ('payable', 'out', _('Posted open payable residuals'), ap, ap_fx),
        ]

    @api.model
    def _reconciliation_applicable(self, params):
        """False when scenario assumptions scale amounts (collection rate, probability)."""
        return True

    @api.model
    def _reconciliation(self, params, result):
        """Invariant: total forecast inflow (in the horizon + beyond it) = sum of the
        independent per-source expectations; same for outflows. A per-source difference is
        accepted only within currency rounding (one rounding unit per converted line)."""
        cur = params['company'].currency_id
        totals = result['source_totals']
        checks = []
        for source, direction, label, expected, fx_lines in self._reconciliation_checks(params, result):
            engine = cur.round(totals.get(source, {}).get(direction, 0.0))
            diff = cur.round(engine - expected)
            tolerance = cur.rounding * max(fx_lines, 0) + cur.rounding / 2
            checks.append({
                'source': source, 'direction': direction, 'label': label,
                'engine': engine, 'expected': cur.round(expected), 'difference': diff,
                'ok': bool(cur.is_zero(diff) or abs(diff) <= tolerance),
            })
        checked = {(c['source'], c['direction']) for c in checks}
        # every collected amount must belong to a checked source (nothing unexplained)
        unexplained = cur.round(sum(
            amount for source, row in totals.items() for direction, amount in row.items()
            if direction in ('in', 'out') and (source, direction) not in checked))
        engine_in = cur.round(result['total_inflow'] + result['beyond']['inflow'])
        engine_out = cur.round(result['total_outflow'] + result['beyond']['outflow'])
        expected_in = cur.round(sum(c['expected'] for c in checks if c['direction'] == 'in'))
        expected_out = cur.round(sum(c['expected'] for c in checks if c['direction'] == 'out'))
        applicable = self._reconciliation_applicable(params)
        tolerance = cur.rounding * (1 + sum(1 for c in checks if not cur.is_zero(c['difference'])))
        return {
            'applicable': applicable,
            'checks': checks,
            'engine_inflow': engine_in,
            'expected_inflow': expected_in,
            'engine_outflow': engine_out,
            'expected_outflow': expected_out,
            'unexplained': unexplained,
            'ok': bool(applicable and all(c['ok'] for c in checks) and cur.is_zero(unexplained)
                       and abs(engine_in - expected_in) <= tolerance
                       and abs(engine_out - expected_out) <= tolerance),
        }

    # ------------------------------------------------------------------
    # Drill-down actions (re-check access: they only return records the user can read)
    # ------------------------------------------------------------------
    @api.model
    def action_open_move_lines(self, line_ids, title=None):
        self._check_user()
        lines = self.env['account.move.line'].search([('id', 'in', [int(i) for i in line_ids or []])])
        return {
            'type': 'ir.actions.act_window',
            'name': title or _('Journal Items'),
            'res_model': 'account.move.line',
            'views': [(self.env.ref('elaf_cashflow_lite.elaf_cf_move_line_list').id, 'tree'), (False, 'form')],
            'domain': [('id', 'in', lines.ids)],
            'context': {'create': False, 'edit': False, 'delete': False},
            'target': 'current',
        }

    @api.model
    def action_open_liquidity_lines(self, account_ids, title=None):
        self._check_user()
        company = self.env.company
        accounts = self.env['account.account'].search([('id', 'in', [int(a) for a in account_ids or []])])
        return {
            'type': 'ir.actions.act_window',
            'name': title or _('Cash Journal Items'),
            'res_model': 'account.move.line',
            'views': [(self.env.ref('elaf_cashflow_lite.elaf_cf_move_line_list').id, 'tree'), (False, 'form')],
            'domain': [('company_id', '=', company.id), ('account_id', 'in', accounts.ids),
                       ('parent_state', '=', 'posted'), ('date', '<=', fields.Date.context_today(self))],
            'context': {'create': False, 'edit': False, 'delete': False},
            'target': 'current',
        }

    @api.model
    def action_open_document(self, res_model, res_id):
        self._check_user()
        allowed = self._drill_models()
        if res_model not in allowed:
            raise AccessError(_('This document type cannot be opened from the forecast.'))
        record = self.env[res_model].browse(int(res_id)).exists()
        if not record:
            raise UserError(_('The document no longer exists.'))
        record.check_access_rights('read')
        record.check_access_rule('read')
        return {
            'type': 'ir.actions.act_window',
            'res_model': res_model,
            'res_id': record.id,
            'views': [(False, 'form')],
            'target': 'current',
        }

    @api.model
    def _forecast_extra(self, forecast):
        """Extension point: extra JSON attached to a saved forecast (Pro: scenario)."""
        return {}

    @api.model
    def _drill_models(self):
        return ['account.move', 'account.move.line']

    # ------------------------------------------------------------------
    # Serialisation helpers for the dashboard
    # ------------------------------------------------------------------
    @api.model
    def _serialize_flow(self, flow):
        return {
            'key': flow['key'],
            'source': flow['source'],
            'doc_type': flow.get('doc_type') or flow['source'],
            'direction': flow['direction'],
            'amount': flow['amount'],
            'date': fields.Date.to_string(flow['date']),
            'orig_date': fields.Date.to_string(flow['orig_date']) if flow.get('orig_date') else False,
            'bucket_date': fields.Date.to_string(flow.get('bucket_date')) if flow.get('bucket_date') else False,
            'bucket': flow.get('bucket'),
            'overdue': bool(flow.get('overdue')),
            'days_overdue': flow.get('days_overdue') or 0,
            'res_model': flow.get('res_model') or False,
            'res_id': flow.get('res_id') or False,
            'aml_id': flow.get('aml_id') or False,
            'name': flow.get('name') or '',
            'partner_name': flow.get('partner_name') or '',
            'currency': flow.get('currency') or False,
            'amount_currency': flow.get('amount_currency') or False,
            'note': flow.get('note') or '',
        }

    @api.model
    def _serialize_result(self, result):
        ds = fields.Date.to_string
        buckets = []
        for b in result['buckets']:
            buckets.append({
                'index': b['index'],
                'date_start': ds(b['date_start']),
                'date_end': ds(b['date_end']),
                'opening': b['opening'], 'inflow': b['inflow'], 'outflow': b['outflow'],
                'overdue_inflow': b['overdue_inflow'], 'overdue_outflow': b['overdue_outflow'],
                'net': b['net'], 'closing': b['closing'],
                'lowest': b['lowest'], 'lowest_date': ds(b['lowest_date']),
                'shortage': b['shortage'], 'gap': b['gap'],
                'inflow_count': b['inflow_count'], 'outflow_count': b['outflow_count'],
            })
        return {
            'date_from': ds(result['date_from']),
            'date_to': ds(result['date_to']),
            'horizon': result['horizon'],
            'granularity': result['granularity'],
            'threshold': result['threshold'],
            'opening': result['opening'],
            'position': result['position'],
            'buckets': buckets,
            'daily': [{'date': ds(d['date']), 'opening': d['opening'], 'inflow': d['inflow'],
                       'outflow': d['outflow'], 'closing': d['closing'], 'shortage': d['shortage']}
                      for d in result['daily']],
            'flows': [self._serialize_flow(f) for f in result['flows']],
            'beyond': result['beyond'],
            'total_inflow': result['total_inflow'],
            'total_outflow': result['total_outflow'],
            'closing': result['closing'],
            'lowest': result['lowest'],
            'lowest_date': ds(result['lowest_date']),
            'shortage': result['shortage'],
            'shortage_date': ds(result['shortage_date']) if result['shortage_date'] else False,
            'shortage_gap': result['shortage_gap'],
            'max_gap': result['max_gap'],
            'reconciles': result['reconciles'],
            'overdue_inflow': result['overdue_inflow'],
            'overdue_outflow': result['overdue_outflow'],
            'reconciliation': result.get('reconciliation') or False,
        }

    @api.model
    def _currency_info(self, currency):
        return {
            'id': currency.id, 'name': currency.name, 'symbol': currency.symbol,
            'position': currency.position, 'digits': currency.decimal_places,
        }

    @api.model
    def _overdue_receivables(self, company):
        today = fields.Date.context_today(self)
        lines = self.env['account.move.line'].search(
            self._open_items_domain(company, 'in') + [('date_maturity', '<', today)])
        total = sum(self._line_amount_company(line, company, today) for line in lines)
        lines = lines.filtered(lambda l: l.amount_residual > 0 or l.amount_residual_currency > 0)
        return {'amount': company.currency_id.round(total), 'count': len(lines.move_id), 'aml_ids': lines.ids}

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------
    @api.model
    def get_dashboard_data(self, filters=None):
        """Single JSON entry point for the dashboard.

        Lite: live actual position + the selected (or latest) saved forecast.
        Pro may compute live forecasts (see the Pro override)."""
        self._check_user()
        filters = dict(filters or {})
        company = self.env.company
        Forecast = self.env['elaf.cashflow.forecast']
        usage = self.env['elaf.cashflow.usage']._get_info(company)
        position = self._get_cash_position(company)
        forecast = False
        if filters.get('forecast_id'):
            forecast = Forecast.search([('id', '=', int(filters['forecast_id'])),
                                        ('company_id', '=', company.id)], limit=1)
        if not forecast and filters.get('mode') != 'live':
            forecast = Forecast.search([('company_id', '=', company.id), ('state', '=', 'done')],
                                       order='create_date desc, id desc', limit=1)
        saved = Forecast.search_read(
            [('company_id', '=', company.id), ('state', '=', 'done')],
            ['name', 'date_from', 'horizon', 'create_date'], order='create_date desc, id desc', limit=20)
        data = {
            'company': {'id': company.id, 'name': company.name},
            'currency': self._currency_info(company.currency_id),
            'today': fields.Date.to_string(fields.Date.context_today(self)),
            'threshold': company.elaf_cf_min_cash,
            'position': position,
            'usage': usage,
            'is_manager': self.env.user.has_group('elaf_cashflow_lite.group_cashflow_manager'),
            'saved_forecasts': [{
                'id': f['id'], 'name': f['name'], 'date_from': fields.Date.to_string(f['date_from']),
                'horizon': int(f['horizon']), 'create_date': fields.Datetime.to_string(f['create_date']),
            } for f in saved],
            'forecast': forecast._to_dashboard() if forecast else False,
            'forecast_id': forecast.id if forecast else False,
            'mode': 'saved',
            'allowed_horizons': self._allowed_horizons(),
            'overdue_receivables': self._overdue_receivables(company),
        }
        return data

    @api.model
    def get_edition_info(self):
        """Data for the Lite vs Pro comparison page (real values only)."""
        self._check_user()
        company = self.env.company
        Forecast = self.env['elaf.cashflow.forecast']
        forecasts = Forecast.search([('company_id', '=', company.id), ('state', '=', 'done')])
        short = forecasts.filtered('shortage')
        return {
            'usage': self.env['elaf.cashflow.usage']._get_info(company),
            'currency': self._currency_info(company.currency_id),
            'forecasts_count': len(forecasts),
            'shortage_forecasts': len(short),
            # distinct calendar days below the threshold across saved forecasts (no double counting)
            'shortage_days': len(set(short.day_ids.filtered('shortage').mapped('date'))),
            'max_gap': max(short.mapped('max_gap') or [0.0]),
            'overdue_receivables': self._overdue_receivables(company),
            'pro_url': 'https://apps.odoo.com/apps/modules/17.0/elaf_cashflow',
            'support': 'info@elaf-development.com',
        }
