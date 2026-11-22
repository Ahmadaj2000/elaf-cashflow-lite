# Part of Elaf Development. See LICENSE file for full copyright and licensing details.
from datetime import timedelta

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import CashflowCommon


@tagged('post_install', '-at_install')
class TestCashflowEngine(CashflowCommon):

    def test_01_data_setup(self):
        """Sanity: due dates are on the receivable / payable lines as designed."""
        line = self.inv_soon.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
        self.assertEqual(line.date_maturity, self.today + timedelta(days=2))
        self.assertAlmostEqual(line.amount_residual, 2000.0)

    def test_02_cash_position(self):
        pos = self.engine._get_cash_position(self.company)
        self.assertAlmostEqual(pos['cash'], 10000.0)
        bank_row = [r for r in pos['journals'] if r['journal_id'] == self.bank.id][0]
        self.assertAlmostEqual(bank_row['balance'], 10000.0)
        self.assertAlmostEqual(pos['outstanding_net'], 0.0)
        self.assertAlmostEqual(pos['opening'], 10000.0)

    def test_03_bucket_allocation_and_overdue(self):
        res = self._compute()
        self.assertEqual(res['granularity'], 'day')
        self.assertEqual(len(res['buckets']), 7)
        b = res['buckets']
        # d0: overdue 1,000 in and 400 out, flagged overdue
        self.assertAlmostEqual(b[0]['inflow'], 1000.0)
        self.assertAlmostEqual(b[0]['overdue_inflow'], 1000.0)
        self.assertAlmostEqual(b[0]['outflow'], 400.0)
        self.assertAlmostEqual(b[0]['overdue_outflow'], 400.0)
        self.assertAlmostEqual(b[1]['outflow'], 3000.0)
        self.assertAlmostEqual(b[2]['inflow'], 2000.0)
        self.assertAlmostEqual(b[4]['outflow'], 8000.0)
        overdue = [f for f in res['flows'] if f['overdue']]
        self.assertEqual(sorted(f['amount'] for f in overdue), [400.0, 1000.0])
        inv_flow = [f for f in res['flows'] if f['res_id'] == self.inv_overdue.id][0]
        self.assertEqual(inv_flow['days_overdue'], 5)
        self.assertEqual(inv_flow['bucket'], 0)
        # beyond horizon: 500 due d+10 is excluded and reported
        self.assertAlmostEqual(res['beyond']['inflow'], 500.0)
        self.assertFalse([f for f in res['flows'] if f['res_id'] == self.inv_beyond.id])

    def test_04_projected_balances_reconcile(self):
        res = self._compute()
        closings = [b['closing'] for b in res['buckets']]
        self.assertEqual(closings, [10600.0, 7600.0, 9600.0, 9600.0, 1600.0, 1600.0, 1600.0])
        self.assertAlmostEqual(res['total_inflow'], 3000.0)
        self.assertAlmostEqual(res['total_outflow'], 11400.0)
        self.assertAlmostEqual(res['closing'], 1600.0)
        self.assertAlmostEqual(res['opening'] + res['total_inflow'] - res['total_outflow'], res['closing'])
        self.assertTrue(res['reconciles'])
        for prev, cur in zip(res['buckets'], res['buckets'][1:]):
            self.assertAlmostEqual(prev['closing'], cur['opening'])
            self.assertAlmostEqual(cur['opening'] + cur['inflow'] - cur['outflow'], cur['closing'])

    def test_05_shortage_detection(self):
        res = self._compute()
        self.assertTrue(res['shortage'])
        self.assertEqual(res['shortage_date'], self.today + timedelta(days=4))
        self.assertAlmostEqual(res['shortage_gap'], 400.0)
        self.assertAlmostEqual(res['lowest'], 1600.0)
        self.assertEqual(res['lowest_date'], self.today + timedelta(days=4))
        flags = [b['shortage'] for b in res['buckets']]
        self.assertEqual(flags, [False, False, False, False, True, True, True])
        # lower threshold: no shortage
        res2 = self._compute(threshold=1000.0)
        self.assertFalse(res2['shortage'])
        self.assertFalse(any(b['shortage'] for b in res2['buckets']))

    def test_06_foreign_currency_converted_at_today_rate(self):
        code = 'EUR' if self.company.currency_id.name != 'EUR' else 'USD'
        foreign = self.setup_other_currency(code, rates=[
            (str(self.today - timedelta(days=60)), 2.0), (str(self.today), 4.0)])
        inv = self._doc('out_invoice', 1000.0, self.today + timedelta(days=3), currency=foreign)
        line = inv.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
        self.assertAlmostEqual(line.amount_residual, 500.0)  # booked at the invoice-date rate 2.0
        res = self._compute()
        flow = [f for f in res['flows'] if f['res_id'] == inv.id][0]
        self.assertAlmostEqual(flow['amount'], 250.0)  # 1,000 / today's rate 4.0
        self.assertEqual(flow['currency'], code)
        self.assertAlmostEqual(res['buckets'][3]['inflow'], 250.0)

    def test_07_outstanding_payments(self):
        """A registered customer payment not yet on a statement moves the money from the
        receivables to the outstanding receipts: total cash stays consistent."""
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=self.inv_soon.ids).create({
                'journal_id': self.bank.id, 'amount': 2000.0,
                'payment_date': self.today - timedelta(days=1)})._create_payments()
        pos = self.engine._get_cash_position(self.company)
        self.assertAlmostEqual(pos['cash'], 10000.0)
        self.assertAlmostEqual(pos['outstanding_receipts'], 2000.0)
        self.assertAlmostEqual(pos['opening'], 12000.0)
        res = self._compute()
        self.assertFalse([f for f in res['flows'] if f['res_id'] == self.inv_soon.id])
        self.assertAlmostEqual(res['closing'], 1600.0)  # same end result, money already received
        self.company.elaf_cf_include_outstanding = False
        pos2 = self.engine._get_cash_position(self.company)
        self.assertAlmostEqual(pos2['opening'], 10000.0)

    def test_08_credit_note_reduces_inflows(self):
        refund = self._doc('out_refund', 300.0, self.today + timedelta(days=2))
        res = self._compute()
        self.assertAlmostEqual(res['buckets'][2]['inflow'], 1700.0)
        flow = [f for f in res['flows'] if f['res_id'] == refund.id][0]
        self.assertAlmostEqual(flow['amount'], -300.0)
        self.assertEqual(flow['doc_type'], 'credit_note')

    def test_09_paid_and_draft_documents_ignored(self):
        draft = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner_c.id,
            'invoice_date': self.today, 'invoice_date_due': self.today,
            'invoice_line_ids': [Command.create({'name': 'x', 'quantity': 1, 'price_unit': 999.0,
                                                 'tax_ids': [Command.clear()]})]})
        res = self._compute()
        self.assertFalse([f for f in res['flows'] if f['res_id'] == draft.id])
        self.assertAlmostEqual(res['total_inflow'], 3000.0)

    def test_10_lite_horizon_limit(self):
        with self.assertRaises(UserError):
            self._params(horizon=30)

    def test_11_empty_company(self):
        other = self.setup_other_company(name='Empty CF Co')['company']
        res = self.engine.with_company(other).compute(self.engine._prepare_params(other, {}))
        self.assertAlmostEqual(res['opening'], 0.0)
        self.assertAlmostEqual(res['closing'], 0.0)
        self.assertFalse(res['flows'])
        self.assertTrue(res['reconciles'])

    def test_12_drilldown_actions(self):
        res = self._compute()
        ids = [f['aml_id'] for f in res['flows'] if f['bucket'] == 0]
        action = self.engine.with_user(self.user_cf).action_open_move_lines(ids)
        self.assertEqual(action['res_model'], 'account.move.line')
        lines = self.env['account.move.line'].search(action['domain'])
        self.assertEqual(sorted(lines.mapped('amount_residual')), [-400.0, 1000.0])
        action = self.engine.with_user(self.user_cf).action_open_liquidity_lines(
            self.engine._get_cash_position(self.company)['liquidity_account_ids'])
        cash_lines = self.env['account.move.line'].search(action['domain'])
        self.assertAlmostEqual(sum(cash_lines.mapped('balance')), 10000.0)
        doc = self.engine.with_user(self.user_cf).action_open_document('account.move', self.inv_soon.id)
        self.assertEqual(doc['res_id'], self.inv_soon.id)
