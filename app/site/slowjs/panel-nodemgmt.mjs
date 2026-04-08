// panel-nodemgmt.mjs //
// Node Management panels: NodeStatePanel and NodeAlertPanel //


export { NodeStatePanel, NodeAlertPanel };


import { JG as $, JGDateTime } from './jagaimo/jagaimo.mjs';
import { JGIndicatorWidget } from './jagaimo/jagawidgets.mjs';
import { Panel } from './panel.mjs';


// ---------------------------------------------------------------------------
// Colour mapping for state values

const STATE_COLOURS = {
    RUNNING:  { bg: '#006020', fg: '#ffffff' },
    READY:    { bg: '#004080', fg: '#ffffff' },
    WARNING:  { bg: '#806000', fg: '#ffffff' },
    STARTING: { bg: '#404080', fg: '#ffffff' },
    TRIPPED:  { bg: '#802000', fg: '#ffffff' },
    FAULT:    { bg: '#800000', fg: '#ffffff' },
    DISABLED: { bg: '#505050', fg: '#cccccc' },
    OFFLINE:  { bg: '#303030', fg: '#888888' },
};

const ALERT_LEVEL_COLOURS = {
    info:     { bg: '#004080', fg: '#ffffff' },
    warning:  { bg: '#806000', fg: '#ffffff' },
    error:    { bg: '#802000', fg: '#ffffff' },
    critical: { bg: '#800000', fg: '#ffffff' },
};

function stateColour(state) {
    return STATE_COLOURS[state] ?? { bg: '#303030', fg: '#888888' };
}

function alertColour(level) {
    return ALERT_LEVEL_COLOURS[level] ?? { bg: '#404040', fg: '#cccccc' };
}

function formatLastSeen(ts_str) {
    if (!ts_str) return '—';
    const dt = new JGDateTime(ts_str);
    return dt.asString('%m/%d %H:%M:%S');
}


// ---------------------------------------------------------------------------
// NodeStatePanel

class NodeStatePanel extends Panel {
    static describe() {
        return { type: 'nodestatus', label: 'Node Status' };
    }

    static buildConstructRows(table, on_done = config => {}) {
        on_done({ type: 'nodestatus' });
    }

    constructor(div, style = {}) {
        super(div, style);

        this.frameDiv = $('<div>').appendTo(div);
        this.titleDiv = $('<div>').appendTo(this.frameDiv);
        this.tableDiv = $('<div>').appendTo(this.frameDiv);
        this.table    = $('<table>').appendTo(this.tableDiv);

        this.frameDiv.css({
            width: 'calc(100% - 44px)',
            height: 'calc(100% - 44px)',
            margin: '10px',
            padding: '10px',
            border: 'thin solid',
            'border-radius': '5px',
            overflow: 'auto',
        });
        this.titleDiv.css({
            'font-family': 'sans-serif',
            'font-size': '18px',
            'font-weight': 'normal',
            'margin-bottom': '8px',
            'white-space': 'nowrap',
        }).html('Node Status');
        this.tableDiv.css({
            width: '100%',
            height: 'calc(100% - 35px)',
            overflow: 'auto',
        });
        this.table.addClass('sd-data-table').css({ width: '100%', margin: 0, padding: 0, border: 'none' });

        this._loading = false;
    }

    async configure(config, options = {}, callbacks = {}) {
        await super.configure(config, options, callbacks);
        if (!this._loading) {
            this._loading = true;
            this._poll();
        }
    }

    async _poll() {
        try {
            const resp = await fetch('api/node_management/status');
            if (resp.ok) {
                const data = await resp.json();
                this._render(data);
            }
        } catch (e) {
            console.log('NodeStatePanel: fetch error', e);
        }
        setTimeout(() => this._poll(), 5000);
    }

    _render(nodes) {
        this.table.empty();

        // Header
        const hdr = $('<tr>');
        for (const h of ['Node', 'Serial', 'State', 'Last Seen', 'Alerts']) {
            $('<th>').text(h).appendTo(hdr);
        }
        hdr.appendTo(this.table);
        const bg = window.getComputedStyle(hdr.get()).getPropertyValue('background-color');
        hdr.find('th').css({ position: 'sticky', top: 0, background: bg });

        for (const node of (nodes ?? [])) {
            if (node.serials) {
                // Multi-serial node — one row per serial
                let first = true;
                const entries = Object.entries(node.serials);
                for (const [sid, info] of entries) {
                    this._appendRow(node, sid, info, first ? entries.length : 0);
                    first = false;
                }
            } else {
                // Node-level (no serials)
                this._appendRow(node, null, node, 1);
            }
        }
    }

    _appendRow(node, serial_id, info, nodeRowspan) {
        const tr = $('<tr>');

        // Node label cell (only on first serial row)
        if (nodeRowspan > 0) {
            const nodeLabel = node.label ?? node.id;
            $('<td>')
                .attr('rowspan', nodeRowspan)
                .css({ 'vertical-align': 'top', 'font-weight': 'bold', 'white-space': 'nowrap', 'padding-right': '8px' })
                .text(nodeLabel)
                .appendTo(tr);
        }

        // Serial
        $('<td>').css({ 'white-space': 'nowrap', color: '#888', 'padding-right': '6px' })
            .text(serial_id ?? '—')
            .appendTo(tr);

        // State badge
        const state = info.state ?? 'OFFLINE';
        const col = stateColour(state);
        const raw = info.raw_state ? ` (${info.raw_state})` : '';
        $('<td>').css({ 'white-space': 'nowrap' })
            .append(
                $('<span>').css({
                    background: col.bg,
                    color: col.fg,
                    padding: '1px 6px',
                    'border-radius': '3px',
                    'font-size': '90%',
                    'font-family': 'monospace',
                }).text(state)
            )
            .append($('<span>').css({ 'font-size': '80%', color: '#888', 'margin-left': '4px' }).text(raw))
            .appendTo(tr);

        // Last seen
        $('<td>').css({ 'white-space': 'nowrap', 'font-size': '85%', color: '#aaa' })
            .text(formatLastSeen(info.last_seen))
            .appendTo(tr);

        // Alert count
        const count = info.open_alert_count ?? 0;
        const topAlert = info.top_alert;
        const alertCell = $('<td>');
        if (count > 0 && topAlert) {
            const ac = alertColour(topAlert.level);
            alertCell.append(
                $('<span>').css({
                    background: ac.bg,
                    color: ac.fg,
                    padding: '1px 5px',
                    'border-radius': '3px',
                    'font-size': '85%',
                }).text(`${topAlert.level} ×${count}`)
            );
            if (topAlert.msg) {
                alertCell.append(
                    $('<span>').css({ 'font-size': '80%', color: '#aaa', 'margin-left': '4px' })
                        .text(topAlert.msg.length > 40 ? topAlert.msg.slice(0, 40) + '…' : topAlert.msg)
                );
            }
        } else {
            alertCell.css({ color: '#555', 'font-size': '85%' }).text('—');
        }
        alertCell.appendTo(tr);

        tr.appendTo(this.table);
    }
}


// ---------------------------------------------------------------------------
// NodeAlertPanel

class NodeAlertPanel extends Panel {
    static describe() {
        return { type: 'nodealerts', label: 'Node Alerts' };
    }

    static buildConstructRows(table, on_done = config => {}) {
        on_done({ type: 'nodealerts' });
    }

    constructor(div, style = {}) {
        super(div, style);

        this.frameDiv  = $('<div>').appendTo(div);
        this.titleDiv  = $('<div>').appendTo(this.frameDiv);
        this.tableDiv  = $('<div>').appendTo(this.frameDiv);
        this.table     = $('<table>').appendTo(this.tableDiv);
        this.indicator = new JGIndicatorWidget($('<div>').appendTo(div));

        this.frameDiv.css({
            width: 'calc(100% - 44px)',
            height: 'calc(100% - 44px)',
            margin: '10px',
            padding: '10px',
            border: 'thin solid',
            'border-radius': '5px',
            overflow: 'auto',
        });
        this.titleDiv.css({
            'font-family': 'sans-serif',
            'font-size': '18px',
            'font-weight': 'normal',
            'margin-bottom': '8px',
            'white-space': 'nowrap',
        }).html('Open Alerts');
        this.tableDiv.css({
            width: '100%',
            height: 'calc(100% - 35px)',
            overflow: 'auto',
        });
        this.table.addClass('sd-data-table').css({ width: '100%', margin: 0, padding: 0, border: 'none' });

        this._loading = false;
    }

    async configure(config, options = {}, callbacks = {}) {
        await super.configure(config, options, callbacks);
        if (!this._loading) {
            this._loading = true;
            this._poll();
        }
    }

    async _poll() {
        try {
            const resp = await fetch('api/alert');
            if (resp.ok) {
                const data = await resp.json();
                this._render(data);
            }
        } catch (e) {
            console.log('NodeAlertPanel: fetch error', e);
        }
        setTimeout(() => this._poll(), 5000);
    }

    _render(alerts) {
        this.table.empty();

        if (!alerts || alerts.length === 0) {
            $('<tr>').append(
                $('<td>').attr('colspan', 6).css({ color: '#555', 'text-align': 'center', padding: '12px' }).text('No open alerts')
            ).appendTo(this.table);
            return;
        }

        // Header
        const hdr = $('<tr>');
        for (const h of ['Level', 'Node', 'Serial', 'Message', 'Time', 'Actions']) {
            $('<th>').text(h).appendTo(hdr);
        }
        hdr.appendTo(this.table);
        const bg = window.getComputedStyle(hdr.get()).getPropertyValue('background-color');
        hdr.find('th').css({ position: 'sticky', top: 0, background: bg });

        for (const alert of alerts) {
            this._appendAlertRow(alert);
        }
    }

    _appendAlertRow(alert) {
        const tr = $('<tr>');

        // Level badge
        const col = alertColour(alert.level);
        $('<td>').css({ 'white-space': 'nowrap' })
            .append(
                $('<span>').css({
                    background: col.bg,
                    color: col.fg,
                    padding: '1px 6px',
                    'border-radius': '3px',
                    'font-size': '85%',
                    'font-family': 'monospace',
                }).text(alert.level)
            ).appendTo(tr);

        // Node id
        $('<td>').css({ 'white-space': 'nowrap' }).text(alert.id ?? '—').appendTo(tr);

        // Serial
        $('<td>').css({ 'white-space': 'nowrap', color: '#888' }).text(alert.serial_id ?? '—').appendTo(tr);

        // Message
        const msg = alert.msg ?? '';
        $('<td>').css({ 'font-size': '90%' })
            .attr('title', msg)
            .text(msg.length > 60 ? msg.slice(0, 60) + '…' : msg)
            .appendTo(tr);

        // Timestamp
        $('<td>').css({ 'white-space': 'nowrap', 'font-size': '85%', color: '#aaa' })
            .text(formatLastSeen(alert.ts))
            .appendTo(tr);

        // Ack / Close buttons (status-aware)
        const status = alert.status ?? 'open';
        const actionCell = $('<td>').css({ 'white-space': 'nowrap' });
        const alertId = alert.alert_id;

        if (status === 'open') {
            $('<button>').text('Ack').css({ 'margin-right': '4px' })
                .bind('click', e => this._lifecycle(alertId, 'ack', e))
                .appendTo(actionCell);
        }
        if (status === 'open' || status === 'ack') {
            $('<button>').text('Close')
                .bind('click', e => this._lifecycle(alertId, 'close', e))
                .appendTo(actionCell);
        }
        actionCell.appendTo(tr);

        tr.appendTo(this.table);
    }

    async _lifecycle(alert_id, action, event = null) {
        const url = `api/alert/${alert_id}/${action}`;
        try {
            this.indicator.open(`${action}…`, '&#x23f3;', event?.clientX ?? null, event?.clientY ?? null);
            const resp = await fetch(url, { method: 'POST' });
            if (!resp.ok) {
                throw new Error(`${resp.status} ${resp.statusText}`);
            }
            this.indicator.close('ok', '&#x2705;', 1000);
            // Refresh immediately
            const alertResp = await fetch('api/alert');
            if (alertResp.ok) this._render(await alertResp.json());
        } catch (e) {
            console.log(e);
            this.indicator.close('Error: ' + e.message, '&#x274c;', 5000);
        }
    }
}
