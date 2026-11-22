# Part of Elaf Development. See LICENSE file for full copyright and licensing details.
"""Independent security review tests (Cash Flow Lite).

Tests whose docstring says "VULNERABILITY" fail on purpose until the issue is fixed;
the other tests verify controls that are in place."""
from datetime import timedelta

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged

from .common import CashflowCommon

REFUSED = (AccessError, UserError, ValidationError, ValueError)


@tagged('post_install', '-at_install')
class TestSecurityReviewCashflowLite(CashflowCommon):

    def _usage(self):
        return self.env['elaf.cashflow.usage'].sudo().search([('key', '=', 'cashflow_forecasts')])

    def _used(self):
        row = self._usage()
        row.invalidate_recordset()
        return row.used if row else 0

    def _generate(self, user=None):
        Forecast = self.env['elaf.cashflow.forecast'].with_user(user or self.user_cf)
        return Forecast.browse(Forecast.action_generate({'horizon': 7}))

    # ------------------------------------------------------------------ snapshot immutability
    def test_cf01_snapshot_writable_with_rpc_context_flag(self):
        """SECURITY-REVIEW: CF-01 VULNERABILITY. A Cash Flow user can rewrite the figures of a
        saved (computed) forecast by passing the internal context key ``elaf_cf_store`` (any RPC
        caller controls the context). Saved forecasts are claimed to be immutable snapshots."""
        fc = self._generate()
        self.assertEqual(fc.state, 'done')
        forged = False
        try:
            fc.with_user(self.user_cf).with_context(elaf_cf_store=True).write(
                {'closing_balance': 999999.0, 'shortage': False, 'cash_actual': 999999.0})
            forged = True
        except REFUSED:
            pass
        self.assertFalse(forged, 'CF-01: a user forged the figures of a computed forecast via the '
                                 'elaf_cf_store context key (closing now %s)' % fc.closing_balance)

    def test_cf02_forecast_created_directly_as_done(self):
        """SECURITY-REVIEW: CF-02 VULNERABILITY. A Cash Flow user can create a forecast directly
        in state 'done' with arbitrary figures (``readonly=True`` is UI-only). It then shows up as
        a saved forecast in the dashboard without any computation."""
        try:
            fake = self.env['elaf.cashflow.forecast'].with_user(self.user_cf).create({
                'name': 'Forged', 'horizon': '7', 'state': 'done',
                'closing_balance': 123456.0, 'opening_balance': 1.0})
        except REFUSED:
            fake = False
        if fake:
            dash = self.engine.with_user(self.user_cf).get_dashboard_data({'mode': 'saved'})
            self.assertNotEqual(dash['forecast_id'], fake.id,
                                'CF-02: forged forecast is served as the latest saved forecast')
        self.assertFalse(fake, 'CF-02: a user created a "computed" forecast with forged figures')

    def test_cf03_user_can_inject_buckets_into_saved_forecast(self):
        """SECURITY-REVIEW: CF-03 VULNERABILITY. Cash Flow users have create access on
        forecast lines / flows / days (ir.model.access.csv), so they can append buckets and flows
        to a computed (saved) forecast, altering the snapshot shown in the dashboard."""
        fc = self._generate()
        before = len(fc.line_ids)
        try:
            self.env['elaf.cashflow.forecast.flow'].with_user(self.user_cf).create({
                'forecast_id': fc.id, 'source': 'receivable', 'direction': 'in',
                'amount': 500000.0, 'date': self.today, 'name': 'Injected'})
            self.env['elaf.cashflow.forecast.line'].with_user(self.user_cf).create({
                'forecast_id': fc.id, 'sequence': 99, 'inflow': 500000.0})
            injected = True
        except REFUSED:
            injected = False
        fc.invalidate_recordset()
        self.assertFalse(injected, 'CF-03: user injected rows into a saved forecast '
                                   '(buckets %s -> %s)' % (before, len(fc.line_ids)))

    def test_cf04_manager_can_rewrite_saved_buckets(self):
        """SECURITY-REVIEW: CF-04 VULNERABILITY (low). Cash Flow managers have write access on
        forecast lines / flows / days, so the buckets of a saved snapshot can be edited although
        the forecast header itself is protected."""
        fc = self._generate()
        line = fc.line_ids.sorted('sequence')[0]
        try:
            line.with_user(self.manager_cf).write({'closing_balance': 777777.0})
            changed = True
        except REFUSED:
            changed = False
        self.assertFalse(changed, 'CF-04: a manager rewrote a bucket of a saved forecast')

    # ------------------------------------------------------------------ usage limit controls
    def test_cf10_internal_context_flag_does_not_unlock_counter(self):
        """SECURITY-REVIEW: CF-10 control. Passing the internal context key
        ``elaf_cf_usage_internal`` (as an RPC caller could) does not let a user, manager or
        Settings admin create / write / unlink usage counters or logs: env.su is also required."""
        self._generate()
        row = self._usage()
        admin = new_test_user(self.env, login='cf_sr_admin@example.com',
                              groups='base.group_system,elaf_cashflow_lite.group_cashflow_manager',
                              company_id=self.company.id, company_ids=[Command.set(self.company.ids)])
        log = self.env['elaf.cashflow.usage.log'].sudo().search([], limit=1)
        for user in (self.user_cf, self.manager_cf, admin):
            ctx = {'elaf_cf_usage_internal': True}
            with self.assertRaises(AccessError):
                row.with_user(user).with_context(**ctx).write({'used': 0})
            with self.assertRaises(AccessError):
                row.with_user(user).with_context(**ctx).unlink()
            with self.assertRaises(AccessError):
                self.env['elaf.cashflow.usage'].with_user(user).with_context(**ctx).create(
                    {'key': 'cashflow_forecasts_2', 'used': 0})
            with self.assertRaises(AccessError):
                self.env['elaf.cashflow.usage.log'].with_user(user).with_context(**ctx).create(
                    {'key': 'cashflow_forecasts', 'event': 'consumed'})
            with self.assertRaises(AccessError):
                log.with_user(user).with_context(**ctx).write({'note': 'x'})
        self.assertEqual(self._used(), 1)

    def test_cf11_horizon_cannot_exceed_seven_days(self):
        """SECURITY-REVIEW: CF-11 control. In Lite a horizon above 7 days is refused through
        action_generate, create and write, and nothing is consumed."""
        Forecast = self.env['elaf.cashflow.forecast'].with_user(self.user_cf)

        def refused(fn):
            try:
                with self.env.cr.savepoint():
                    fn()
                    self.env.flush_all()
            except REFUSED:
                return True
            return False

        for horizon in (8, 30, 90):
            self.assertTrue(refused(lambda: Forecast.action_generate({'horizon': horizon})))
        draft = Forecast.create({'name': 'draft', 'horizon': '7'})
        for value in ('30', '90'):
            self.assertTrue(refused(lambda: draft.write({'horizon': value})))
            self.assertTrue(refused(lambda: Forecast.create({'name': 'x', 'horizon': value})))
        self.assertEqual(self._used(), 0)

    def test_cf12_recompute_and_copy_are_not_free_calculations(self):
        """SECURITY-REVIEW: CF-12 control. Re-running action_compute on a saved forecast does
        not refresh it with new data; copy() does not produce a new computation; a copy reset to
        draft and computed consumes one calculation; after 5 calculations every path is blocked."""
        fc = self._generate()
        computed_at, closing = fc.computed_at, fc.closing_balance
        # new data after the snapshot
        self._doc('out_invoice', 50000.0, self.today + timedelta(days=1))
        fc.with_user(self.user_cf).action_compute()
        fc.invalidate_recordset()
        self.assertEqual(fc.computed_at, computed_at)
        self.assertEqual(fc.closing_balance, closing)
        self.assertEqual(self._used(), 1)
        dup = fc.with_user(self.user_cf).copy()
        self.assertFalse(dup.line_ids)
        self.assertEqual(self._used(), 1)
        dup2 = fc.with_user(self.user_cf).copy({'state': 'draft'})
        dup2.action_compute()
        self.assertEqual(self._used(), 2)
        for _i in range(3):
            self._generate()
        self.assertEqual(self._used(), 5)
        dup3 = fc.with_user(self.user_cf).copy({'state': 'draft'})
        with self.assertRaises(UserError):
            dup3.action_compute()
        with self.assertRaises(UserError):
            self._generate(self.manager_cf)
        self.assertEqual(self._used(), 5)

    def test_cf13_other_company_user_cannot_read_forecasts(self):
        """SECURITY-REVIEW: CF-13 control. A Cash Flow user of company B cannot read company A
        forecasts, buckets or flows (search_read, read_group, dashboard forecast_id, drill-down)."""
        fc = self._generate()
        other = self.setup_other_company(name='CF SR Other Co')['company']
        user_b = new_test_user(self.env, login='cf_sr_user_b@example.com',
                               groups='elaf_cashflow_lite.group_cashflow_user',
                               company_id=other.id, company_ids=[Command.set(other.ids)])
        Forecast = self.env['elaf.cashflow.forecast'].with_user(user_b)
        self.assertFalse(Forecast.search_read([('id', '=', fc.id)], ['closing_balance']))
        self.assertFalse(Forecast.formatted_read_group([('id', '=', fc.id)], ['company_id'], ['closing_balance:sum']))
        self.assertFalse(self.env['elaf.cashflow.forecast.flow'].with_user(user_b).search(
            [('forecast_id', '=', fc.id)]))
        self.assertFalse(self.env['elaf.cashflow.forecast.line'].with_user(user_b).search(
            [('forecast_id', '=', fc.id)]))
        with self.assertRaises(AccessError):
            fc.with_user(user_b).read(['closing_balance'])
        dash = self.engine.with_user(user_b).get_dashboard_data({'forecast_id': fc.id, 'mode': 'saved'})
        self.assertNotEqual(dash['forecast_id'], fc.id)
        self.assertNotIn(fc.id, [f['id'] for f in dash['saved_forecasts']])
        with self.assertRaises(AccessError):
            self.engine.with_user(user_b).action_open_document('account.move', self.inv_soon.id)
        # company-A user cannot switch into company B through the context
        with self.assertRaises(AccessError):
            self.engine.with_user(self.user_cf).with_context(
                allowed_company_ids=[other.id]).get_dashboard_data({'mode': 'saved'})
        # the drill-down refuses models outside the whitelist
        with self.assertRaises(AccessError):
            self.engine.with_user(self.user_cf).action_open_document('res.users', self.env.uid)

    def test_cf14_accounting_figures_need_cashflow_group(self):
        """SECURITY-REVIEW: CF-14 control / info. Users without the Cash Flow group (plain
        employee, Billing user) cannot call the dashboard, edition or generate entry points.
        Info: the Cash Flow / User group implies account.group_account_readonly, i.e. granting it
        also grants read access to all journal entries of the user's companies."""
        plain = new_test_user(self.env, login='cf_sr_plain@example.com', groups='base.group_user',
                              company_id=self.company.id, company_ids=[Command.set(self.company.ids)])
        billing = new_test_user(self.env, login='cf_sr_billing@example.com',
                                groups='account.group_account_invoice',
                                company_id=self.company.id, company_ids=[Command.set(self.company.ids)])
        for user in (plain, billing):
            with self.assertRaises(AccessError):
                self.engine.with_user(user).get_dashboard_data({'mode': 'saved'})
            with self.assertRaises(AccessError):
                self.engine.with_user(user).get_edition_info()
            with self.assertRaises(AccessError):
                self.env['elaf.cashflow.forecast'].with_user(user).action_generate({'horizon': 7})
            with self.assertRaises(AccessError):
                self.engine.with_user(user).action_open_move_lines(self.inv_soon.line_ids.ids)
        self.assertEqual(self._used(), 0)
        self.assertTrue(self.user_cf.has_group('account.group_account_readonly'))

    # ------------------------------------------------------------------ business spot checks
    def test_cf20_business_opening_position_and_daily_buckets(self):
        """SECURITY-REVIEW: CF-20 business spot check (hand-calculated, isolated company).

        Bank 2,500 (d-3) + cash 500 (d-1) + bank 999 dated d+2 (future: excluded) -> actual 3,000.
        Invoices 1,200 due d-4 (overdue -> d0), 800 due d0, 300 due d+3.
        Bills 500 due d+3, 4,000 due d+8 (beyond horizon). Threshold 4,900.
        d0: 3,000 + 2,000 = 5,000 | d1-d2: 5,000 | d3: 5,000 + 300 - 500 = 4,800 | d4-d6: 4,800.
        Totals: in 2,300, out 500, closing 4,800; overdue inflow 1,200; beyond out 4,000;
        first shortage d3 with gap 100."""
        data = self.setup_other_company(name='CF SR Spot Co')
        co = data['company']
        co.elaf_cf_min_cash = 4900.0
        d = self.today
        self._post_cash(co, data['default_journal_bank'], 2500.0, d - timedelta(days=3))
        self._post_cash(co, data['default_journal_cash'], 500.0, d - timedelta(days=1))
        self._post_cash(co, data['default_journal_bank'], 999.0, d + timedelta(days=2))
        self._doc('out_invoice', 1200.0, d - timedelta(days=4), company=co)
        self._doc('out_invoice', 800.0, d, company=co)
        self._doc('out_invoice', 300.0, d + timedelta(days=3), company=co)
        self._doc('in_invoice', 500.0, d + timedelta(days=3), company=co)
        self._doc('in_invoice', 4000.0, d + timedelta(days=8), company=co)
        engine = self.engine.with_company(co)
        res = engine.compute(engine._prepare_params(co, {'horizon': 7}))
        self.assertAlmostEqual(res['position']['cash'], 3000.0)
        self.assertAlmostEqual(res['opening'], 3000.0)
        closings = [x['closing'] for x in res['daily']]
        self.assertEqual(closings, [5000.0, 5000.0, 5000.0, 4800.0, 4800.0, 4800.0, 4800.0])
        b0 = res['buckets'][0]
        self.assertAlmostEqual(b0['inflow'], 2000.0)
        self.assertAlmostEqual(b0['overdue_inflow'], 1200.0)
        self.assertAlmostEqual(res['buckets'][3]['outflow'], 500.0)
        self.assertAlmostEqual(res['total_inflow'], 2300.0)
        self.assertAlmostEqual(res['total_outflow'], 500.0)
        self.assertAlmostEqual(res['closing'], 4800.0)
        self.assertAlmostEqual(res['beyond']['outflow'], 4000.0)
        self.assertTrue(res['shortage'])
        self.assertEqual(res['shortage_date'], d + timedelta(days=3))
        self.assertAlmostEqual(res['shortage_gap'], 100.0)
        self.assertTrue(res['reconciles'])
