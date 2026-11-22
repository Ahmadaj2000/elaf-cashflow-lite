=======================
Cash Flow Forecast Lite
=======================

See your actual cash position and a 7-day cash forecast built from open customer invoices and
vendor bills. Shortages below your minimum cash are flagged, and every figure drills down to its
journal items.

.. contents:: Contents
   :local:
   :depth: 1

Overview
========

* **Actual cash position**: the posted balance, dated today or earlier, of the default account
  of each bank and cash journal, per journal. Outstanding receipts and payments (the journals'
  payment-method accounts) are shown separately. A setting decides whether they are included in
  the opening balance.
* **7-day forecast** in daily buckets:

  * Inflows and outflows come from posted, unreconciled receivable and payable items with an
    open residual, dated by their due date. Credit notes and unmatched payments are included
    with their sign.
  * Foreign-currency residuals are converted to the company currency.
  * Draft and cancelled documents and sales or purchase orders are not used: a draft is not yet
    a receivable or payable. Partial payments and credit notes reduce the open residual, so each
    amount is counted once. Payments registered but not yet on a bank statement are in the
    outstanding receipts / payments (opening balance when the setting is on).
* **Overdue items** are placed in the first day and flagged with their days overdue. Items due
  after the horizon are reported as *beyond horizon* totals.
* **Liquidity shortage**: a day whose projected balance is below the minimum cash threshold is
  flagged. The dashboard shows the first shortage date and its gap, the largest gap, and the
  lowest projected balance with its date.
* **How this is calculated**: shows opening + inflows - outflows = closing, a *Reconciles* check,
  the beyond-horizon totals and the journal balances used. On every calculation the engine also
  checks that the inflows (in the horizon and beyond) equal an independent database sum of the
  posted open receivable residuals, and the outflows the posted open payable residuals.
* **Actual and forecast are kept separate**. Labels and styles differ, and the chart shows an
  actual bar, a forecast line, the threshold line and a legend.
* **Saved forecasts**: each calculation is stored with its days and flows. Results cannot be
  edited, apart from the name and notes.

Installation
============

1. Install **Cash Flow Forecast Lite**. It needs Invoicing (``account``) and Discuss.
2. Odoo Online is not supported, because it does not run third-party Python modules.

Compatibility: Odoo 18.0 Community Edition (tested). Odoo 17.0 and 18.0 are distributed as
separate packages.

Configuration
=============

* **Users**: in *Settings > Users*, set the *Cash Flow Forecast* access to **User** or
  **Manager**. The User group includes read-only accounting access.
* **Settings**: managers open *Cash Flow > Configuration > Settings*. The same settings are in the
  general Settings app. Both settings are per company:

  * *Minimum Cash Threshold* (default 0).
  * *Include Outstanding Payments in Opening Balance* (default on).
* Bank and cash journals need their default accounts. Due dates come from the payment terms of
  your invoices and bills.

Usage
=====

1. Open *Cash Flow > Dashboard* and click **Generate forecast**. A 7-day forecast is calculated
   and saved.
2. Read the shortage alert (or *No liquidity shortage forecast*), the KPI cards, the chart and the
   *Forecast by day* table.
3. Click a KPI or a day to see its items, then open the journal items or the source document.
4. Open *How this is calculated* to check that opening + inflows - outflows = closing.
5. Earlier calculations are listed under *Cash Flow > Saved Forecasts*.

Lite edition limits
===================

* One company per database: the first company that saves a forecast. In another company the
  dashboard still shows the actual position, but generating is refused with a clear message.
* Forecast horizon of 7 days, in daily buckets.
* Up to 5 forecast calculations in total.

  * Generating and saving a forecast counts as one.
  * Viewing saved forecasts is always available.
  * A failed calculation does not count.
  * Deleting forecasts does not give calculations back.
* Manual calculation only. Open invoices and bills are the only sources.

After 5 calculations, saved forecasts remain viewable, and the **Generate forecast** button opens
the Lite vs Pro page.

The usage counter is shown on the dashboard and under *Cash Flow > Configuration > Lite Usage*.
It is a local record in your database. There is no activation key and no remote check.

The **Discover Pro** button on *Cash Flow > Lite vs Pro* opens the Pro listing on the Odoo Apps
store in your browser. It works once the Pro listing is published. No data is sent.

Security and permissions
========================

* **User**: view the dashboard and saved forecasts, and generate forecasts.

  .. important::
     The *Cash Flow Forecast: User* group implies the accounting group *Read-only*
     (``account.group_account_readonly``). Granting it gives **read access to every journal entry,
     invoice, bill and payment** of the user's companies (no create, write or delete). Grant it
     only to people allowed to see the company's accounting.
* **Manager**: also change settings, delete forecasts and view the usage log.
* The module reads posted journal items. It never creates or changes accounting entries.
* Multi-company record rules apply to forecasts and their lines.
* A computed forecast is a saved snapshot: its figures, buckets, daily detail and flows are
  written by the calculation only. Users and managers cannot create, change or add them; only the
  name and note stay editable, and a copy is a new draft.
* All data stays in your Odoo database. The module makes no external network calls and sends no
  e-mail. Your browser stores only which tips you have already seen.

FAQ
===

**Why does my bank balance differ from the cash position?**
  Only posted entries dated today or earlier count. Outstanding receipts and payments are shown
  separately, and the setting decides whether they are included.

**Does the forecast include sales orders?**
  Not in Lite.

**Is the forecast certain?**
  No. It is a projection from due dates. Forecast figures are always labelled as forecast.

Known limitations
=================

* Draft documents are ignored.
* Items due after 7 days are shown only as beyond-horizon totals.
* Not available on Odoo Online.

Support
=======

Community support, best effort: info@elaf-development.com
