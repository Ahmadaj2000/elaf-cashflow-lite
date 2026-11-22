/** @odoo-module **/
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { formatCurrency } from "@web/core/currency";
import { CashflowChart } from "./cashflow_chart";

// Extension points used by the Pro edition (registries keep Lite free of Pro code):
//  - "elaf_cashflow_filters": components rendered in the header filter bar
//  - "elaf_cashflow_sections": components rendered below the Lite analysis
export const filterRegistry = registry.category("elaf_cashflow_filters");
export const sectionRegistry = registry.category("elaf_cashflow_sections");

const PRO_URL = "https://apps.odoo.com/apps/modules/17.0/elaf_cashflow";
const SEEN_KEY = "elaf_cf_milestones";

export class CashflowDashboard extends Component {
    static template = "elaf_cashflow_lite.Dashboard";
    static components = { CashflowChart };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        const params = (this.props.action && this.props.action.params) || {};
        this.state = useState({
            loading: true,
            error: null,
            data: null,
            filters: { forecast_id: params.forecast_id || false },
            selected: null, // {bucket, direction}
            showHow: false,
            busy: false,
        });
        this.filterComponents = filterRegistry.getEntries();
        this.sectionComponents = sectionRegistry.getEntries();
        onWillStart(() => this.load());
    }

    // ------------------------------------------------------------------ data
    async load() {
        this.state.loading = !this.state.data;
        this.state.error = null;
        try {
            const data = await this.orm.call("elaf.cashflow.engine", "get_dashboard_data", [this.state.filters]);
            this.state.data = data;
            if (this.state.selected && (!data.forecast || !data.forecast.buckets[this.state.selected.bucket])) {
                this.state.selected = null;
            }
        } catch (e) {
            this.state.error = (e && e.data && e.data.message) || (e && e.message) || String(e);
        } finally {
            this.state.loading = false;
        }
    }

    async updateFilters(values) {
        Object.assign(this.state.filters, values);
        this.state.selected = null;
        await this.load();
    }

    // ------------------------------------------------------------------ formatting
    get currencyId() {
        return this.state.data && this.state.data.currency.id;
    }
    fmt(amount) {
        return formatCurrency(amount || 0, this.currencyId);
    }
    fmtShort(amount) {
        const abs = Math.abs(amount);
        const sign = amount < 0 ? "-" : "";
        if (abs >= 1e6) {
            return `${sign}${(abs / 1e6).toFixed(1)}M`;
        }
        if (abs >= 1e3) {
            return `${sign}${(abs / 1e3).toFixed(1)}k`;
        }
        return `${sign}${abs.toFixed(0)}`;
    }
    fmtDate(value) {
        if (!value) {
            return "";
        }
        const [y, m, d] = value.split("-");
        return `${d}/${m}/${y}`;
    }
    bucketLabel(b) {
        if (b.date_start === b.date_end) {
            return this.fmtDate(b.date_start);
        }
        return `${this.fmtDate(b.date_start)} – ${this.fmtDate(b.date_end)}`;
    }
    docTypeLabel(flow) {
        const labels = {
            invoice: _t("Invoice"),
            bill: _t("Bill"),
            credit_note: _t("Credit note"),
            payment: _t("Unmatched payment"),
            entry: _t("Journal entry"),
            sale: _t("Sales order (pipeline)"),
            purchase: _t("Purchase order (pipeline)"),
            item: _t("Recurring/manual item"),
        };
        return labels[flow.doc_type] || labels[flow.source] || flow.source;
    }

    // ------------------------------------------------------------------ getters
    get data() {
        return this.state.data;
    }
    get forecast() {
        return this.state.data && this.state.data.forecast;
    }
    get usage() {
        return this.state.data && this.state.data.usage;
    }
    get isPro() {
        return this.usage && this.usage.is_pro;
    }
    get shortageBuckets() {
        return this.forecast ? this.forecast.buckets.filter((b) => b.shortage) : [];
    }
    get inflows() {
        return this.forecast ? this.forecast.flows.filter((f) => f.direction === "in") : [];
    }
    get outflows() {
        return this.forecast ? this.forecast.flows.filter((f) => f.direction === "out") : [];
    }
    topFlows(direction) {
        const flows = direction === "in" ? this.inflows : this.outflows;
        return [...flows].sort((a, b) => (a.bucket_date || "").localeCompare(b.bucket_date || "") || b.amount - a.amount);
    }
    get selectedBucket() {
        if (!this.state.selected || !this.forecast) {
            return null;
        }
        return this.forecast.buckets[this.state.selected.bucket];
    }
    get selectedFlows() {
        const sel = this.state.selected;
        if (!sel || !this.forecast) {
            return [];
        }
        return this.forecast.flows.filter(
            (f) => f.bucket === sel.bucket && (!sel.direction || f.direction === sel.direction)
        );
    }
    get selectedDays() {
        const b = this.selectedBucket;
        if (!b || !this.forecast.daily) {
            return [];
        }
        return this.forecast.daily.filter((d) => d.date >= b.date_start && d.date <= b.date_end);
    }
    get usageLevel() {
        const u = this.usage;
        if (!u || u.is_pro) {
            return "";
        }
        if (u.percent >= 100) {
            return "bad";
        }
        return u.percent >= 80 ? "warn" : "ok";
    }
    get lockedFeatures() {
        return [
            _t("30/60/90-day horizons (weekly buckets)"),
            _t("Scenarios and side-by-side comparison"),
            _t("Sales and purchase pipeline"),
            _t("Recurring items (payroll, rent, taxes, loans)"),
            _t("Collection prioritisation and follow-ups"),
            _t("Payment recommendations"),
            _t("Multi-company consolidation"),
            _t("DSO / DPO working capital"),
        ];
    }

    // ------------------------------------------------------------------ actions
    selectBucket(index, direction = null) {
        const sel = this.state.selected;
        if (sel && sel.bucket === index && sel.direction === direction) {
            this.state.selected = null;
        } else {
            this.state.selected = { bucket: index, direction };
        }
    }

    async openMoveLines(ids, title) {
        const clean = ids.filter(Boolean);
        if (!clean.length) {
            this.notification.add(_t("No journal items behind this figure."), { type: "info" });
            return;
        }
        const action = await this.orm.call("elaf.cashflow.engine", "action_open_move_lines", [clean, title]);
        this.action.doAction(action);
    }

    openFlowsLines(flows, title) {
        return this.openMoveLines(flows.map((f) => f.aml_id), title);
    }

    openInflowLines() {
        return this.openFlowsLines(this.inflows, _t("Expected inflows"));
    }
    openOutflowLines() {
        return this.openFlowsLines(this.outflows, _t("Expected outflows"));
    }
    openOverdueLines() {
        return this.openMoveLines(this.data.overdue_receivables.aml_ids, _t("Overdue receivables"));
    }
    selectLowest() {
        const f = this.forecast;
        const b = f.buckets.find((x) => x.date_start <= f.lowest_date && x.date_end >= f.lowest_date) || f.buckets[0];
        this.selectBucket(b.index);
    }
    async openAccountLines(accountId, title) {
        const action = await this.orm.call("elaf.cashflow.engine", "action_open_liquidity_lines", [[accountId], title]);
        this.action.doAction(action);
    }

    async openCashLines() {
        const pos = this.data.position;
        const action = await this.orm.call("elaf.cashflow.engine", "action_open_liquidity_lines", [
            pos.liquidity_account_ids,
            _t("Actual bank and cash journal items"),
        ]);
        this.action.doAction(action);
    }

    async openOutstandingLines() {
        const pos = this.data.position;
        const action = await this.orm.call("elaf.cashflow.engine", "action_open_liquidity_lines", [
            pos.outstanding_account_ids,
            _t("Outstanding receipts and payments"),
        ]);
        this.action.doAction(action);
    }

    async openDocument(flow) {
        if (!flow.res_model || !flow.res_id) {
            return;
        }
        const action = await this.orm.call("elaf.cashflow.engine", "action_open_document", [
            flow.res_model,
            flow.res_id,
        ]);
        this.action.doAction(action);
    }

    openSavedForecast() {
        if (this.forecast && this.forecast.saved) {
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "elaf.cashflow.forecast",
                res_id: this.forecast.id,
                views: [[false, "form"]],
            });
        }
    }

    openForecastList() {
        this.action.doAction("elaf_cashflow_lite.action_forecasts");
    }

    openSettings() {
        this.action.doAction("elaf_cashflow_lite.action_settings", {
            onClose: () => this.load(),
        });
    }

    openEdition() {
        this.action.doAction("elaf_cashflow_lite.action_edition");
    }

    openProPage() {
        window.open(PRO_URL, "_blank", "noopener");
    }

    onSelectForecast(ev) {
        const id = parseInt(ev.target.value) || false;
        this.updateFilters({ forecast_id: id });
    }

    _seenMilestones() {
        try {
            return JSON.parse(window.localStorage.getItem(SEEN_KEY) || "{}");
        } catch {
            return {};
        }
    }
    _markMilestone(key) {
        try {
            const seen = this._seenMilestones();
            seen[key] = true;
            window.localStorage.setItem(SEEN_KEY, JSON.stringify(seen));
        } catch {
            // storage unavailable: prompts may show again, nothing else depends on it
        }
    }

    async generate() {
        const u = this.usage;
        if (u && !u.can_generate) {
            // never a dead button: explain and open the comparison page
            this.openEdition();
            return;
        }
        this.state.busy = true;
        try {
            const id = await this.orm.call("elaf.cashflow.forecast", "action_generate", [
                { horizon: this.state.filters.horizon || 7, ...this.generateExtra() },
            ]);
            this.state.filters.forecast_id = id;
            this.state.filters.mode = "saved";
            await this.load();
            this.notification.add(_t("Forecast generated and saved."), { type: "success" });
            this.milestones();
        } catch (e) {
            const msg = (e && e.data && e.data.message) || (e && e.message) || String(e);
            this.notification.add(msg, { type: "danger", sticky: true });
        } finally {
            this.state.busy = false;
        }
    }

    generateExtra() {
        // Pro passes its scenario/source filters through this hook.
        return {};
    }

    milestones() {
        const u = this.usage;
        if (!u || u.is_pro || !u.caps_enabled) {
            return;
        }
        const seen = this._seenMilestones();
        if (u.used === 1 && !seen.first) {
            this._markMilestone("first");
            this.notification.add(
                _t("Your first forecast is saved. Open it any time from Saved Forecasts — viewing is always free."),
                { type: "info", title: _t("Cash Flow Lite") }
            );
        } else if (u.percent >= 80 && u.percent < 100 && !seen.eighty) {
            this._markMilestone("eighty");
            this.notification.add(
                _t("You have used %(used)s of %(limit)s Lite forecast calculations. The Pro edition removes this limit.", {
                    used: u.used,
                    limit: u.limit,
                }),
                { type: "warning", title: _t("Cash Flow Lite") }
            );
        }
    }

    toggleHow() {
        this.state.showHow = !this.state.showHow;
    }
}

registry.category("actions").add("elaf_cashflow_dashboard", CashflowDashboard);
