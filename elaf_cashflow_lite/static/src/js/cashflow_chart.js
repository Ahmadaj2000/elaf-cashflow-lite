/** @odoo-module **/
// Inline SVG chart: actual balance (solid bar) vs forecast projected balance
// (hatched bars + dashed line) with the minimum cash threshold (red dashed line).
import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

export class CashflowChart extends Component {
    static template = "elaf_cashflow_lite.Chart";
    static props = {
        forecast: Object,
        threshold: Number,
        format: Function,
        formatShort: Function,
        onSelect: { type: Function, optional: true },
        selected: { optional: true },
        extraLines: { type: Array, optional: true },
    };

    get W() {
        return 860;
    }
    get H() {
        return 300;
    }
    get pad() {
        return { l: 84, r: 16, t: 18, b: 46 };
    }

    get model() {
        const f = this.props.forecast;
        const buckets = f.buckets || [];
        const pad = this.pad;
        const values = [f.opening, this.props.threshold, 0, ...buckets.map((b) => b.closing), ...buckets.map((b) => b.lowest)];
        for (const line of this.props.extraLines || []) {
            values.push(...line.values);
        }
        let min = Math.min(...values);
        let max = Math.max(...values);
        if (max === min) {
            max = min + 1;
        }
        const span = max - min;
        min -= span * 0.08;
        max += span * 0.08;
        const n = buckets.length + 1;
        const plotW = this.W - pad.l - pad.r;
        const plotH = this.H - pad.t - pad.b;
        const slot = plotW / n;
        const y = (v) => pad.t + plotH - ((v - min) / (max - min)) * plotH;
        const x = (i) => pad.l + slot * i + slot / 2;
        const barW = Math.max(6, Math.min(48, slot * 0.55));
        const zeroY = y(0);
        const bar = (i, v) => {
            const top = Math.min(y(v), zeroY);
            return { x: x(i) - barW / 2, y: top, w: barW, h: Math.max(1, Math.abs(zeroY - y(v))) };
        };
        const actual = { ...bar(0, f.opening), value: f.opening, label: _t("Today") };
        const fbars = buckets.map((b, k) => ({
            ...bar(k + 1, b.closing),
            index: b.index,
            value: b.closing,
            shortage: b.shortage,
            label: this.shortLabel(b),
            cx: x(k + 1),
            cy: y(b.closing),
        }));
        const linePts = [[x(0), y(f.opening)], ...fbars.map((b) => [b.cx, b.cy])];
        const ticks = [];
        for (let t = 0; t <= 4; t++) {
            const v = min + ((max - min) * t) / 4;
            ticks.push({ y: y(v), label: this.props.formatShort(v) });
        }
        const extra = (this.props.extraLines || []).map((line) => ({
            ...line,
            points: [[x(0), y(f.opening)], ...line.values.map((v, k) => [x(k + 1), y(v)])]
                .map((p) => p.join(","))
                .join(" "),
        }));
        const labelEvery = Math.max(1, Math.ceil(n / 14));
        return {
            actual,
            fbars,
            line: linePts.map((p) => p.join(",")).join(" "),
            thresholdY: y(this.props.threshold),
            zeroY,
            ticks,
            extra,
            labelEvery,
            plotRight: this.W - pad.r,
        };
    }

    shortLabel(b) {
        const [, m, d] = b.date_end.split("-");
        if (b.date_start === b.date_end) {
            return `${d}/${m}`;
        }
        const [, m0, d0] = b.date_start.split("-");
        return `${d0}/${m0}`;
    }

    select(index) {
        if (this.props.onSelect) {
            this.props.onSelect(index);
        }
    }
}
