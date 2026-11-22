# Part of Elaf Development. See LICENSE file for full copyright and licensing details.
"""Saved forecasts are snapshots written by the engine only (fixes of CF-01..CF-04)."""
from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from .common import CashflowCommon


@tagged('post_install', '-at_install')
class TestSnapshotHardening(CashflowCommon):

    def _generate(self, user=None):
        Forecast = self.env['elaf.cashflow.forecast'].with_user(user or self.user_cf)
        return Forecast.browse(Forecast.action_generate({'horizon': 7}))

    def test_01_draft_cannot_be_turned_into_a_computed_forecast(self):
        draft = self.env['elaf.cashflow.forecast'].with_user(self.user_cf).create({'name': 'D', 'horizon': '7'})
        for vals in ({'state': 'done'}, {'closing_balance': 1.0}, {'shortage': True}):
            with self.assertRaises(UserError):
                draft.write(vals)
        # the internal flag without env.su is not honoured
        with self.assertRaises(UserError):
            draft.with_context(elaf_cf_store=True).write({'state': 'done'})
        draft.write({'name': 'Renamed', 'note': 'ok'})
        self.assertEqual(draft.state, 'draft')

    def test_02_defaults_from_context_cannot_forge_figures(self):
        Forecast = self.env['elaf.cashflow.forecast'].with_user(self.user_cf)
        with self.assertRaises(UserError):
            Forecast.with_context(default_state='done').create({'name': 'X', 'horizon': '7'})
        with self.assertRaises(UserError):
            Forecast.with_context(default_closing_balance=5.0).create({'name': 'X', 'horizon': '7'})
        ok = Forecast.with_context(default_state='draft').create({'name': 'X', 'horizon': '7'})
        self.assertEqual(ok.state, 'draft')

    def test_03_copy_is_a_draft_without_figures(self):
        fc = self._generate()
        dup = fc.with_user(self.user_cf).copy()
        self.assertEqual(dup.state, 'draft')
        self.assertFalse(dup.computed_at)
        self.assertFalse(dup.closing_balance)
        self.assertFalse(dup.line_ids or dup.flow_ids or dup.day_ids)

    def test_04_buckets_read_only_for_everyone_but_the_engine(self):
        fc = self._generate()
        self.assertTrue(fc.line_ids and fc.flow_ids and fc.day_ids)
        for user in (self.user_cf, self.manager_cf):
            for rec in (fc.line_ids[0], fc.flow_ids[0], fc.day_ids[0]):
                with self.assertRaises(AccessError):
                    rec.with_user(user).write({'forecast_id': fc.id})
                with self.assertRaises(AccessError):
                    rec.with_user(user).unlink()
        # even with sudo, the engine flag is required (defence in depth)
        with self.assertRaises(AccessError):
            fc.line_ids[0].sudo().write({'closing_balance': 1.0})
        with self.assertRaises(AccessError):
            self.env['elaf.cashflow.forecast.day'].sudo().create({'forecast_id': fc.id})
        # the forecast itself can still be renamed by a user and deleted by a manager
        fc.with_user(self.user_cf).write({'name': 'Renamed'})
        fc.with_user(self.manager_cf).unlink()
        self.assertFalse(fc.exists())

    def test_05_compute_requires_cashflow_group(self):
        plain = new_test_user(self.env, login='cf_hard_plain@example.com', groups='base.group_user',
                              company_id=self.company.id, company_ids=[Command.set(self.company.ids)])
        with self.assertRaises(AccessError):
            self.engine.with_user(plain).compute(self._params())

    def test_06_lite_menus_visible_without_pro(self):
        if self.env['ir.module.module'].search_count([('name', '=', 'elaf_cashflow'), ('state', '=', 'installed')]):
            self.skipTest('Pro installed: the Lite-only menus are hidden (tested in Pro)')
        visible = self.env['ir.ui.menu'].with_user(self.manager_cf)._visible_menu_ids()
        for xmlid in ('elaf_cashflow_lite.menu_usage', 'elaf_cashflow_lite.menu_usage_log',
                      'elaf_cashflow_lite.menu_edition'):
            self.assertIn(self.env.ref(xmlid).id, visible)
