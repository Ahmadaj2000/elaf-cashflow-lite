# Part of Elaf Development. See LICENSE file for full copyright and licensing details.
from datetime import timedelta

from odoo import Command, fields
from odoo.tests import new_test_user

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class CashflowCommon(AccountTestInvoicingCommon):
    """Fresh company with a hand-checked data set.

    Bank opening 10,000 (posted, 10 days ago). Threshold 2,000. Horizon 7 days (d0 = today).
      Customer invoices: 1,000 due d-5 (overdue), 2,000 due d+2, 500 due d+10 (beyond 7 days)
      Vendor bills:      400 due d-3 (overdue), 3,000 due d+1, 8,000 due d+4
    Daily closing: d0 10,600 | d1 7,600 | d2 9,600 | d3 9,600 | d4 1,600 | d5 1,600 | d6 1,600
    Totals: in 3,000, out 11,400, closing 1,600. Lowest 1,600 on d+4, shortage from d+4, gap 400.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data['company']
        # the test runner user (accountman) is a Cash Flow manager
        cls.env.user.groups_id += cls.env.ref('elaf_cashflow_lite.group_cashflow_manager')
        cls.company.elaf_cf_min_cash = 2000.0
        cls.engine = cls.env['elaf.cashflow.engine']
        cls.today = fields.Date.context_today(cls.engine)
        cls.bank = cls.company_data['default_journal_bank']
        cls.partner_c = cls.env['res.partner'].create({'name': 'CF Customer', 'company_id': False})
        cls.partner_v = cls.env['res.partner'].create({'name': 'CF Vendor', 'company_id': False})

        cls.user_cf = new_test_user(
            cls.env, login='cf_user@example.com', groups='elaf_cashflow_lite.group_cashflow_user',
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        cls.manager_cf = new_test_user(
            cls.env, login='cf_manager@example.com', groups='elaf_cashflow_lite.group_cashflow_manager',
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])

        cls._post_cash(cls.company, cls.bank, 10000.0, cls.today - timedelta(days=10))
        cls.inv_overdue = cls._doc('out_invoice', 1000.0, cls.today - timedelta(days=5))
        cls.inv_soon = cls._doc('out_invoice', 2000.0, cls.today + timedelta(days=2))
        cls.inv_beyond = cls._doc('out_invoice', 500.0, cls.today + timedelta(days=10))
        cls.bill_overdue = cls._doc('in_invoice', 400.0, cls.today - timedelta(days=3))
        cls.bill_d1 = cls._doc('in_invoice', 3000.0, cls.today + timedelta(days=1))
        cls.bill_d4 = cls._doc('in_invoice', 8000.0, cls.today + timedelta(days=4))

    @classmethod
    def setup_other_company(cls, **kwargs):
        """Odoo 18+ API used by the tests; Odoo 17 has setup_company_data(name, ...)."""
        return cls.setup_company_data(kwargs.pop('name', 'company_2'), **kwargs)

    @classmethod
    def _post_cash(cls, company, journal, amount, date):
        counterpart = cls.env['account.account'].with_company(company).search([
            *cls.env['account.account']._check_company_domain(company),
            ('account_type', '=', 'equity')], limit=1)
        misc = cls.env['account.journal'].search([
            *cls.env['account.journal']._check_company_domain(company), ('type', '=', 'general')], limit=1)
        move = cls.env['account.move'].with_company(company).create({
            'move_type': 'entry',
            'date': date,
            'journal_id': misc.id,
            'line_ids': [
                Command.create({'name': 'Opening', 'account_id': journal.default_account_id.id,
                                'debit': amount, 'credit': 0.0}),
                Command.create({'name': 'Opening', 'account_id': counterpart.id,
                                'debit': 0.0, 'credit': amount}),
            ],
        })
        move.action_post()
        return move

    @classmethod
    def _doc(cls, move_type, amount, due, partner=None, currency=None, company=None, invoice_date=None):
        company = company or cls.company
        partner = partner or (cls.partner_c if move_type.startswith('out') else cls.partner_v)
        move = cls.env['account.move'].with_company(company).create({
            'move_type': move_type,
            'partner_id': partner.id,
            'invoice_date': invoice_date or cls.today - timedelta(days=30),
            'invoice_payment_term_id': False,
            'invoice_date_due': due,
            'currency_id': (currency or company.currency_id).id,
            'invoice_line_ids': [Command.create({
                'name': 'Line', 'quantity': 1.0, 'price_unit': amount, 'tax_ids': [Command.clear()]})],
        })
        move.action_post()
        return move

    force_lite = True

    def setUp(self):
        super().setUp()
        if not self.force_lite:
            return
        # These Lite tests describe the Lite edition: force the Lite behaviour even when the
        # Pro module is installed in the same database.
        engine_cls = type(self.env['elaf.cashflow.engine'])
        self.patch(engine_cls, '_elaf_cashflow_is_pro', lambda self: False)
        self.patch(engine_cls, '_allowed_horizons', lambda self: [7])

    def _params(self, **filters):
        return self.engine._prepare_params(self.company, filters)

    def _compute(self, **filters):
        return self.engine.compute(self._params(**filters))
