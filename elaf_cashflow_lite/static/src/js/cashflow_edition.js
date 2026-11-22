/** @odoo-module **/
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { formatCurrency } from "@web/core/currency";

export class CashflowEdition extends Component {
    static template = "elaf_cashflow_lite.Edition";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ loading: true, error: null, info: null });
        onWillStart(async () => {
            try {
                this.state.info = await this.orm.call("elaf.cashflow.engine", "get_edition_info", []);
            } catch (e) {
                this.state.error = (e && e.data && e.data.message) || (e && e.message) || String(e);
            } finally {
                this.state.loading = false;
            }
        });
    }

    fmt(amount) {
        return formatCurrency(amount || 0, this.state.info.currency.id);
    }

    get rows() {
        const u = this.state.info.usage;
        return [
            [_t("Actual cash position (bank, cash, outstanding payments)"), "✓", "✓"],
            [_t("Expected inflows and outflows from open invoices and bills"), "✓", "✓"],
            [_t("Minimum cash threshold and shortage detection"), "✓", "✓"],
            [_t("Drill-down to journal items and documents"), "✓", "✓"],
            [_t("Forecast horizon"), _t("7 days, daily buckets"), _t("7, 30, 60 or 90 days (weekly buckets)")],
            [_t("Forecast calculations"), u.caps_enabled ? _t("%s in total", u.limit) : _t("Unlimited"), _t("Unlimited")],
            [_t("Companies"), _t("One company"), _t("Multi-company, consolidated view")],
            [_t("Sales and purchase pipeline (orders not yet invoiced)"), "—", "✓"],
            [_t("Recurring and manual items (payroll, rent, taxes, loans)"), "—", "✓"],
            [_t("Scenarios (payment delays, collection rate, deferrals) and comparison"), "—", "✓"],
            [_t("Customer payment behaviour"), "—", "✓"],
            [_t("Collection prioritisation with follow-up activities"), "—", "✓"],
            [_t("Working capital: DSO, DPO, overdue ratio"), "—", "✓"],
            [_t("Explainable payment and collection recommendations"), "—", "✓"],
            [_t("Live forecasts without using an allowance"), "—", "✓"],
        ];
    }

    openPro() {
        window.open(this.state.info.pro_url, "_blank", "noopener");
    }

    openDashboard() {
        this.action.doAction("elaf_cashflow_lite.action_dashboard");
    }
}

registry.category("actions").add("elaf_cashflow_edition", CashflowEdition);
