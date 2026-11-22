# Part of Elaf Development. See LICENSE file for full copyright and licensing details.
"""R2 independent reconciliation (S5-C2, S5-C3, S5-C6): synthetic accounting scenarios computed
by hand (qa/cashflow/R2_RECONCILIATION.md). Each scenario runs on an empty company "R2 Recon"
(currency = the test company currency) with a posted bank opening of 5,000 dated D-3, a 7-day
horizon in daily buckets (D0..D6, D0 = today) and no threshold. Every test asserts the exact
position, buckets, projected balances and the reconciliation invariant
    forecast inflows (horizon + beyond) = posted open receivable residuals
    forecast outflows (horizon + beyond) = posted open payable residuals
"""
from datetime import timedelta

from odoo import Command
from odoo.tests import tagged

from .common import CashflowCommon

OPENING = 5000.0


@tagged('post_install', '-at_install')
class TestR2Reconciliation(CashflowCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        data = cls.setup_other_company(name='R2 Recon')
        cls.co = data['company']
        cls.co_bank = data['default_journal_bank']
        cls.co.elaf_cf_min_cash = 0.0
        cls.env.user.company_ids |= cls.co
        cls._post_cash(cls.co, cls.co_bank, OPENING, cls.today - timedelta(days=3))

    # ------------------------------------------------------------------ helpers
    def d(self, days):
        return self.today + timedelta(days=days)

    def _engine(self):
        return self.env['elaf.cashflow.engine'].with_company(self.co)

    def _run(self, **filters):
        engine = self._engine()
        return engine.compute(engine._prepare_params(self.co, dict(filters, horizon=7)))

    def _post(self, move_type, amount, due, currency=None, invoice_date=None, partner=None):
        return self._doc(move_type, amount, due, partner=partner, currency=currency, company=self.co,
                         invoice_date=invoice_date)

    def _draft(self, move_type, amount, due):
        partner = self.partner_c if move_type.startswith('out') else self.partner_v
        return self.env['account.move'].with_company(self.co).create({
            'move_type': move_type, 'partner_id': partner.id,
            'invoice_date': self.d(-1), 'invoice_payment_term_id': False, 'invoice_date_due': due,
            'invoice_line_ids': [Command.create({'name': 'Draft', 'quantity': 1.0, 'price_unit': amount,
                                                 'tax_ids': [Command.clear()]})],
        })

    def _pay(self, invoice, amount, date):
        return self.env['account.payment.register'].with_company(self.co).with_context(
            active_model='account.move', active_ids=invoice.ids).create({
                'journal_id': self.co_bank.id, 'amount': amount, 'payment_date': date})._create_payments()

    def _assert(self, res, opening, inflows, outflows, closings, beyond_in=0.0, beyond_out=0.0,
                recon_in=None, recon_out=None):
        self.assertEqual(res['granularity'], 'day')
        self.assertEqual(res['opening'], opening)
        self.assertEqual([b['inflow'] for b in res['buckets']], inflows)
        self.assertEqual([b['outflow'] for b in res['buckets']], outflows)
        self.assertEqual([b['closing'] for b in res['buckets']], closings)
        self.assertEqual(res['total_inflow'], sum(inflows))
        self.assertEqual(res['total_outflow'], sum(outflows))
        self.assertEqual(res['closing'], closings[-1])
        self.assertEqual(res['beyond']['inflow'], beyond_in)
        self.assertEqual(res['beyond']['outflow'], beyond_out)
        self.assertTrue(res['reconciles'])
        rec = res['reconciliation']
        self.assertTrue(rec['ok'], rec)
        expected_in = sum(inflows) + beyond_in if recon_in is None else recon_in
        expected_out = sum(outflows) + beyond_out if recon_out is None else recon_out
        self.assertEqual(rec['engine_inflow'], expected_in)
        self.assertEqual(rec['expected_inflow'], expected_in)
        self.assertEqual(rec['engine_outflow'], expected_out)
        self.assertEqual(rec['expected_outflow'], expected_out)
        self.assertEqual(rec['unexplained'], 0.0)

    # ------------------------------------------------------------------ scenarios
    def test_r01_opening_balance(self):
        """R-01: only the bank opening. Position 5,000, no flows, every day closes at 5,000."""
        res = self._run()
        self.assertEqual(res['position']['cash'], OPENING)
        self.assertEqual(res['position']['outstanding_net'], 0.0)
        self._assert(res, OPENING, [0.0] * 7, [0.0] * 7, [OPENING] * 7)

    def test_r02_invoices_due_across_buckets(self):
        """R-02: invoices 1,000 (D+1), 2,000 (D+5), 4,000 (D+20, beyond); bill 1,500 (D+3)."""
        self._post('out_invoice', 1000.0, self.d(1))
        self._post('out_invoice', 2000.0, self.d(5))
        self._post('out_invoice', 4000.0, self.d(20))
        self._post('in_invoice', 1500.0, self.d(3))
        res = self._run()
        self._assert(res, OPENING,
                     [0.0, 1000.0, 0.0, 0.0, 0.0, 2000.0, 0.0],
                     [0.0, 0.0, 0.0, 1500.0, 0.0, 0.0, 0.0],
                     [5000.0, 6000.0, 6000.0, 4500.0, 4500.0, 6500.0, 6500.0],
                     beyond_in=4000.0)

    def test_r03_overdue_invoice(self):
        """R-03: invoice 800 due D-10 is overdue: first bucket, flagged, 10 days overdue."""
        inv = self._post('out_invoice', 800.0, self.d(-10))
        res = self._run()
        self._assert(res, OPENING, [800.0] + [0.0] * 6, [0.0] * 7, [5800.0] * 7)
        flow = [f for f in res['flows'] if f['res_id'] == inv.id][0]
        self.assertTrue(flow['overdue'])
        self.assertEqual(flow['days_overdue'], 10)
        self.assertEqual(res['overdue_inflow'], 800.0)

    def test_r04_partial_payment(self):
        """R-04: invoice 1,000 due D+2, 400 paid on D-1 (registered, not yet on a statement):
        residual 600 on D+2; the 400 sits in outstanding receipts, added to the opening
        (setting on) -> opening 5,400, closing 6,000 = 5,000 + 1,000 counted once."""
        inv = self._post('out_invoice', 1000.0, self.d(2))
        self._pay(inv, 400.0, self.d(-1))
        self.assertEqual(inv.amount_residual, 600.0)
        res = self._run()
        self.assertEqual(res['position']['outstanding_receipts'], 400.0)
        self._assert(res, 5400.0, [0.0, 0.0, 600.0, 0.0, 0.0, 0.0, 0.0], [0.0] * 7,
                     [5400.0, 5400.0, 6000.0, 6000.0, 6000.0, 6000.0, 6000.0])
        # setting off: the 400 in transit is shown separately and not projected
        self.co.elaf_cf_include_outstanding = False
        res = self._run()
        self._assert(res, 5000.0, [0.0, 0.0, 600.0, 0.0, 0.0, 0.0, 0.0], [0.0] * 7,
                     [5000.0, 5000.0, 5600.0, 5600.0, 5600.0, 5600.0, 5600.0])

    def test_r05_credit_note(self):
        """R-05: invoice 1,000 and credit note 250, both due D+3: inflow 750 on D+3, before and
        after the credit note is matched with the invoice."""
        inv = self._post('out_invoice', 1000.0, self.d(3))
        refund = self._post('out_refund', 250.0, self.d(3))
        expected = ([0.0, 0.0, 0.0, 750.0, 0.0, 0.0, 0.0], [0.0] * 7,
                    [5000.0, 5000.0, 5000.0, 5750.0, 5750.0, 5750.0, 5750.0])
        self._assert(self._run(), OPENING, *expected)
        (inv | refund).line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable').reconcile()
        self.assertEqual(inv.amount_residual, 750.0)
        self.assertEqual(refund.amount_residual, 0.0)
        self._assert(self._run(), OPENING, *expected)

    def test_r06_foreign_currency_invoice(self):
        """R-06: invoice 1,000 in a foreign currency, booked at rate 4 (D-30), due D+4, today's
        rate 2: the residual is converted once, at today's rate: 500 (not the booked 250)."""
        code = 'EUR' if self.co.currency_id.name != 'EUR' else 'USD'
        foreign = self.env['res.currency'].with_context(active_test=False).search([('name', '=', code)])
        foreign.active = True
        Rate = self.env['res.currency.rate']
        Rate.create({'currency_id': foreign.id, 'company_id': self.co.id, 'name': self.d(-30), 'rate': 4.0})
        Rate.create({'currency_id': foreign.id, 'company_id': self.co.id, 'name': self.today, 'rate': 2.0})
        inv = self._post('out_invoice', 1000.0, self.d(4), currency=foreign, invoice_date=self.d(-30))
        self.assertEqual(inv.amount_total_signed, 250.0)
        res = self._run()
        self._assert(res, OPENING, [0.0, 0.0, 0.0, 0.0, 500.0, 0.0, 0.0], [0.0] * 7,
                     [5000.0, 5000.0, 5000.0, 5000.0, 5500.0, 5500.0, 5500.0])
        flow = [f for f in res['flows'] if f['res_id'] == inv.id][0]
        self.assertEqual(flow['currency'], code)
        self.assertEqual(flow['amount_currency'], 1000.0)

    def test_r07_draft_documents(self):
        """R-07: a draft invoice (900, D+2) and a draft bill (300, D+1) are neither cash nor
        receivables / payables: nothing is forecast."""
        self._draft('out_invoice', 900.0, self.d(2))
        self._draft('in_invoice', 300.0, self.d(1))
        self._assert(self._run(), OPENING, [0.0] * 7, [0.0] * 7, [OPENING] * 7)

    def test_r08_cancelled_invoice(self):
        """R-08: an invoice of 700 posted, then reset to draft and cancelled, is ignored; the
        other invoice of 100 (D+1) is kept."""
        cancelled = self._post('out_invoice', 700.0, self.d(2))
        cancelled.button_draft()
        cancelled.button_cancel()
        self.assertEqual(cancelled.state, 'cancel')
        self._post('out_invoice', 100.0, self.d(1))
        self._assert(self._run(), OPENING, [0.0, 100.0, 0.0, 0.0, 0.0, 0.0, 0.0], [0.0] * 7,
                     [5000.0, 5100.0, 5100.0, 5100.0, 5100.0, 5100.0, 5100.0])

    def test_r09_outstanding_receipt_not_matched(self):
        """R-09: invoice 1,000 due D+2 and a customer payment of 300 received on D-1 but not
        matched with the invoice: the 300 is in outstanding receipts (opening 5,300) and is an
        open credit on the receivable account (-300, overdue, first bucket). Closing 6,000:
        the 1,000 is counted once (300 already received + 700 still to collect)."""
        self._post('out_invoice', 1000.0, self.d(2))
        payment = self.env['account.payment'].with_company(self.co).create({
            'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': self.partner_c.id,
            'amount': 300.0, 'date': self.d(-1), 'journal_id': self.co_bank.id})
        payment.action_post()
        res = self._run()
        self.assertEqual(res['position']['outstanding_receipts'], 300.0)
        self._assert(res, 5300.0, [-300.0, 0.0, 1000.0, 0.0, 0.0, 0.0, 0.0], [0.0] * 7,
                     [5000.0, 5000.0, 6000.0, 6000.0, 6000.0, 6000.0, 6000.0])
        self.assertEqual([f['doc_type'] for f in res['flows'] if f['amount'] < 0], ['payment'])

    def test_r10_vendor_side_mirror(self):
        """R-10: bill 2,000 due D+1 partly paid 500 (outstanding payment), vendor credit note
        200 due D+1, overdue bill 300 (D-2): outflows 300 on D0 and 2,000 - 500 - 200 = 1,300 on D+1;
        opening 5,000 - 500 = 4,500; closing 2,900."""
        bill = self._post('in_invoice', 2000.0, self.d(1))
        self._pay(bill, 500.0, self.d(-1))
        self._post('in_refund', 200.0, self.d(1))
        self._post('in_invoice', 300.0, self.d(-2))
        res = self._run()
        self.assertEqual(res['position']['outstanding_payments'], -500.0)
        self._assert(res, 4500.0, [0.0] * 7, [300.0, 1300.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                     [4200.0, 2900.0, 2900.0, 2900.0, 2900.0, 2900.0, 2900.0])
