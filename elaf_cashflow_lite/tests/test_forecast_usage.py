# Part of Elaf Development. See LICENSE file for full copyright and licensing details.
from datetime import timedelta

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from .common import CashflowCommon


@tagged('post_install', '-at_install')
class TestForecastAndUsage(CashflowCommon):

    def _usage(self):
        return self.env['elaf.cashflow.usage'].sudo().search([('key', '=', 'cashflow_forecasts')])

    def _generate(self, user=None, company=None):
        Forecast = self.env['elaf.cashflow.forecast'].with_user(user or self.user_cf)
        if company:
            Forecast = Forecast.with_company(company)
        return Forecast.browse(Forecast.action_generate({'horizon': 7}))

    # ------------------------------------------------------------------ forecast record
    def test_01_saved_forecast_matches_engine(self):
        fc = self._generate()
        self.assertEqual(fc.state, 'done')
        self.assertEqual(len(fc.line_ids), 7)
        self.assertAlmostEqual(fc.opening_balance, 10000.0)
        self.assertAlmostEqual(fc.total_inflow, 3000.0)
        self.assertAlmostEqual(fc.total_outflow, 11400.0)
        self.assertAlmostEqual(fc.closing_balance, 1600.0)
        self.assertTrue(fc.shortage)
        self.assertEqual(fc.shortage_date, self.today + timedelta(days=4))
        self.assertAlmostEqual(fc.shortage_gap, 400.0)
        self.assertEqual(fc.shortage_days, 3)
        self.assertAlmostEqual(fc.beyond_inflow, 500.0)
        first = fc.line_ids.sorted('sequence')[0]
        self.assertAlmostEqual(first.overdue_inflow, 1000.0)
        flows = self.env['elaf.cashflow.forecast.flow'].search(first.action_view_inflows()['domain'])
        self.assertEqual(flows.move_line_id.move_id, self.inv_overdue)
        self.assertTrue(flows.overdue)
        data = fc._to_dashboard()
        self.assertTrue(data['reconciles'])
        self.assertEqual([b['closing'] for b in data['buckets']],
                         [10600.0, 7600.0, 9600.0, 9600.0, 1600.0, 1600.0, 1600.0])

    def test_02_snapshot_protected(self):
        fc = self._generate()
        with self.assertRaises(UserError):
            fc.with_user(self.user_cf).write({'closing_balance': 99999.0})
        fc.with_user(self.user_cf).write({'note': 'ok'})
        with self.assertRaises(AccessError):
            fc.line_ids[0].with_user(self.user_cf).write({'inflow': 1.0})
        with self.assertRaises(UserError):
            fc.with_user(self.user_cf).unlink()

    def test_03_dashboard_data(self):
        dash = self.engine.with_user(self.user_cf).get_dashboard_data({'mode': 'saved'})
        self.assertFalse(dash['forecast'])
        self.assertAlmostEqual(dash['position']['cash'], 10000.0)
        self.assertEqual(dash['usage']['used'], 0)
        self.assertAlmostEqual(dash['overdue_receivables']['amount'], 1000.0)
        fc = self._generate()
        dash = self.engine.with_user(self.user_cf).get_dashboard_data({'mode': 'saved'})
        self.assertEqual(dash['forecast_id'], fc.id)
        self.assertEqual(dash['usage']['used'], 1)
        self.assertEqual(dash['usage']['remaining'], 4)
        info = self.engine.with_user(self.user_cf).get_edition_info()
        self.assertEqual(info['shortage_forecasts'], 1)
        self.assertEqual(info['shortage_days'], 3)
        self.assertAlmostEqual(info['overdue_receivables']['amount'], 1000.0)

    # ------------------------------------------------------------------ usage (FREEMIUM §A.7)
    def test_10_counting(self):
        self._generate()
        self.assertEqual(self._usage().used, 1)
        self.assertEqual(self._usage().bound_company_id, self.company)
        log = self.env['elaf.cashflow.usage.log'].sudo().search([('event', '=', 'consumed')])
        self.assertEqual(len(log), 1)

    def test_11_limit_reached_denied(self):
        for _i in range(5):
            self._generate()
        self.assertEqual(self._usage().used, 5)
        with self.assertRaises(UserError):
            self._generate()
        self.assertEqual(self._usage().used, 5)
        self.assertTrue(self.engine.with_user(self.user_cf).get_dashboard_data({'mode': 'saved'})['usage']['exhausted'])

    def test_12_failed_operation_does_not_consume(self):
        self._generate()
        Usage = type(self.env['elaf.cashflow.usage'])
        original = Usage._consume

        def consume_then_fail(this, company, record):
            original(this, company, record)
            raise ValueError('simulated failure after the counter step')

        self.patch(Usage, '_consume', consume_then_fail)
        with self.assertRaises(ValueError):
            with self.env.cr.savepoint():
                self._generate()
        self.env.invalidate_all()
        self.assertEqual(self._usage().used, 1)
        self.assertEqual(self.env['elaf.cashflow.forecast'].search_count([]), 1)

    def test_13_idempotent_no_double_consumption(self):
        fc = self._generate()
        fc.with_user(self.user_cf).action_compute()
        fc.with_user(self.user_cf).action_compute()
        self.assertEqual(self._usage().used, 1)

    def test_14_lock_path(self):
        Usage = type(self.env['elaf.cashflow.usage'])
        original = Usage._lock_row
        calls = []

        def spy(this, row_id):
            calls.append(row_id)
            return original(this, row_id)

        self.patch(Usage, '_lock_row', spy)
        self._generate()
        self.assertTrue(calls)
        self.assertEqual(set(calls), {self._usage().id})

    def test_15_usage_rows_protected(self):
        self._generate()
        row = self._usage()
        admin = new_test_user(self.env, login='cf_sysadmin@example.com',
                              groups='base.group_system,elaf_cashflow_lite.group_cashflow_manager',
                              company_id=self.company.id, company_ids=[Command.set(self.company.ids)])
        for user in (self.user_cf, self.manager_cf, admin):
            with self.assertRaises(AccessError):
                row.with_user(user).write({'used': 0})
            with self.assertRaises(AccessError):
                row.with_user(user).unlink()
            with self.assertRaises(AccessError):
                self.env['elaf.cashflow.usage'].with_user(user).create({'key': 'x', 'used': 0})
            log = self.env['elaf.cashflow.usage.log'].sudo().search([], limit=1)
            with self.assertRaises(AccessError):
                log.with_user(user).unlink()
        # even sudo without the internal flag is refused
        with self.assertRaises(AccessError):
            row.sudo().write({'used': 0})
        self.assertEqual(row.used, 1)

    def test_16_second_company_refused(self):
        self._generate()
        other = self.setup_other_company(name='CF Second Co')['company']
        self.user_cf.write({'company_ids': [Command.link(other.id)]})
        with self.assertRaises(UserError):
            self._generate(company=other)
        self.assertEqual(self._usage().used, 1)
        # viewing in the other company is never blocked
        dash = self.engine.with_user(self.user_cf).with_company(other).get_dashboard_data({'mode': 'saved'})
        self.assertTrue(dash['usage']['wrong_company'])

    def test_17_history_readable_after_exhaustion(self):
        forecasts = self.env['elaf.cashflow.forecast']
        for _i in range(5):
            forecasts |= self._generate()
        with self.assertRaises(UserError):
            self._generate()
        for fc in forecasts:
            data = self.engine.with_user(self.user_cf).get_dashboard_data({'forecast_id': fc.id, 'mode': 'saved'})
            self.assertEqual(data['forecast']['id'], fc.id)
            self.assertAlmostEqual(data['forecast']['closing'], 1600.0)
        self.assertEqual(len(self.engine.with_user(self.user_cf).get_dashboard_data({'mode': 'saved'})['saved_forecasts']), 5)

    def test_18_other_operations_after_exhaustion(self):
        """Settings changes and deletion keep working after exhaustion; deletion never refunds."""
        forecasts = self.env['elaf.cashflow.forecast']
        for _i in range(5):
            forecasts |= self._generate()
        wiz = self.env['elaf.cashflow.settings'].with_user(self.manager_cf).create({'min_cash': 500.0})
        wiz.action_save()
        self.assertAlmostEqual(self.company.elaf_cf_min_cash, 500.0)
        forecasts[0].with_user(self.manager_cf).unlink()
        self.assertEqual(self._usage().used, 5)
        with self.assertRaises(UserError):
            self._generate()

    def test_19_caps_switch(self):
        self.patch(type(self.env['elaf.cashflow.usage']), '_caps_enabled', lambda self: False)
        for _i in range(6):
            self._generate()
        self.assertEqual(self._usage().used, 6)

    # ------------------------------------------------------------------ security
    def test_20_settings_manager_only(self):
        with self.assertRaises(AccessError):
            self.env['elaf.cashflow.settings'].with_user(self.user_cf).create({'min_cash': 1.0})
        wiz = self.env['elaf.cashflow.settings'].with_user(self.manager_cf).create({'min_cash': 3000.0})
        wiz.action_save()
        self.assertAlmostEqual(self.company.elaf_cf_min_cash, 3000.0)

    def test_21_no_group_no_access(self):
        plain = new_test_user(self.env, login='cf_plain@example.com', groups='base.group_user',
                              company_id=self.company.id, company_ids=[Command.set(self.company.ids)])
        with self.assertRaises(AccessError):
            self.engine.with_user(plain).get_dashboard_data({'mode': 'saved'})
        with self.assertRaises(AccessError):
            self.env['elaf.cashflow.forecast'].with_user(plain).search([])

    def test_22_multi_company_isolation(self):
        other_data = self.setup_other_company(name='CF Isolated Co')
        other = other_data['company']
        self._post_cash(other, other_data['default_journal_bank'], 5000.0, self.today - timedelta(days=2))
        self._doc('out_invoice', 700.0, self.today + timedelta(days=1), company=other)
        # company 1 figures are unaffected by company 2 data
        res = self._compute()
        self.assertAlmostEqual(res['opening'], 10000.0)
        self.assertAlmostEqual(res['total_inflow'], 3000.0)
        # company 2 is computed on its own data only
        res2 = self.engine.with_company(other).compute(self.engine._prepare_params(other, {}))
        self.assertAlmostEqual(res2['opening'], 5000.0)
        self.assertAlmostEqual(res2['total_inflow'], 700.0)
        # a forecast of company 2 is invisible to a user of company 1 only
        fc2 = self.env['elaf.cashflow.forecast'].create({'company_id': other.id, 'horizon': '7'})
        visible = self.env['elaf.cashflow.forecast'].with_user(self.user_cf).search([('id', '=', fc2.id)])
        self.assertFalse(visible)
        with self.assertRaises(AccessError):
            fc2.with_user(self.user_cf).read(['name'])
