# Part of Elaf Development. See LICENSE file for full copyright and licensing details.
{
    'name': 'Cash Flow Forecast Lite',
    'version': '17.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Actual cash position and a 7-day cash forecast with shortage alerts. '
               'Lite limits: one company, 7-day horizon, 5 forecast calculations in total.',
    'description': """
Cash Flow Forecast Lite
=======================
Know your cash position before tomorrow arrives.

* Actual cash position from posted bank and cash journal balances (outstanding
  receipts and payments shown separately).
* 7-day forecast in daily buckets from open customer invoices and vendor bills
  (overdue items are placed in the first bucket and flagged as overdue).
* Minimum cash threshold with liquidity-shortage detection (date and gap).
* Drill-down from every figure to the underlying journal items.
* "How this is calculated" panel: opening + inflows - outflows = closing.

Lite edition limits (disclosed)
-------------------------------
* One company.
* Forecast horizon: 7 days, daily buckets.
* Up to 5 forecast calculations in total (generating and saving a forecast
  counts as one; viewing saved forecasts is always free and never blocked).
* Manual forecasts only. No scenarios, collection prioritisation, payment
  recommendations, multi-company consolidation, automatic forecasts or exports.

A Pro edition is available (Cash Flow Forecast, module elaf_cashflow).
Support: info@elaf-development.com
""",
    'author': 'Elaf Development',
    'website': 'https://www.elaf-development.com/',
    'support': 'info@elaf-development.com',
    'license': 'LGPL-3',
    'depends': ['account', 'mail'],
    'data': [
        'security/elaf_cashflow_security.xml',
        'security/ir.model.access.csv',
        'views/elaf_cashflow_forecast_views.xml',
        'views/elaf_cashflow_usage_views.xml',
        'views/account_move_line_views.xml',
        'wizard/elaf_cashflow_settings_views.xml',
        'views/res_config_settings_views.xml',
        'views/elaf_cashflow_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'elaf_cashflow_lite/static/src/scss/elaf_cf_tokens.scss',
            'elaf_cashflow_lite/static/src/js/**/*',
            'elaf_cashflow_lite/static/src/xml/**/*',
        ],
    },
    'images': ['static/description/banner.png', 'static/description/main_screenshot.png'],
    'application': True,
    'installable': True,
}
