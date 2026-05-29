/**
 * garden-timer-card.js
 * Custom Lovelace card for the Tuya Garden Timers integration.
 *
 * Renders a week-grid view:
 *   - 7 day columns (Mon–Sun), navigable with < / >
 *   - Time axis on the left (default 04:00–22:00, configurable)
 *   - Each calendar entity gets its own colour; blocks are sized by duration
 *     and positioned by start time
 *   - Disabled schedules (⏸ prefix) shown as faded fill + dashed border
 *     in the same hue as the zone
 *   - Legend across the top
 *
 * Card config (all optional):
 *   type: custom:garden-timer-card
 *   entity_ids:            # list of calendar entity IDs; omit to auto-discover
 *     - calendar.front_garden_timer_zone_1
 *   start_hour: 4          # start of visible time range (default 4)
 *   end_hour: 22           # end of visible time range (default 22)
 *   title: "Garden"        # card title (default "Watering Schedule")
 */

const PALETTE = [
  '#1E88E5', // blue
  '#43A047', // green
  '#FB8C00', // orange
  '#E53935', // red
  '#8E24AA', // purple
  '#00ACC1', // cyan
  '#F4511E', // deep-orange
  '#6D4C41', // brown
  '#039BE5', // light-blue
  '#7CB342', // light-green
  '#FFB300', // amber
  '#D81B60', // pink
];

class GardenTimerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._weekOffset = 0;
    this._loading = false;
    this._events = {};
    this._entityIds = [];
    this._colors = {};
  }

  static getStubConfig() {
    return { start_hour: 4, end_hour: 22 };
  }

  setConfig(config) {
    this._config = {
      start_hour: 4,
      end_hour: 22,
      title: 'Watering Schedule',
      ...config,
    };
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._resolveEntities();
      this._refresh();
    }
  }

  // -------------------------------------------------------------------------
  // Entity discovery + colour assignment
  // -------------------------------------------------------------------------

  _resolveEntities() {
    if (this._config.entity_ids && this._config.entity_ids.length) {
      this._entityIds = this._config.entity_ids;
    } else {
      // Auto-discover: pick calendar.* entities whose friendly name contains
      // ' — ' (our "Device — Zone" naming pattern)
      this._entityIds = Object.entries(this._hass.states)
        .filter(([id, s]) =>
          id.startsWith('calendar.') &&
          (s.attributes.friendly_name || '').includes(' \u2014 ')
        )
        .map(([id]) => id)
        .sort();
    }
    this._entityIds.forEach((id, i) => {
      this._colors[id] = PALETTE[i % PALETTE.length];
    });
  }

  // -------------------------------------------------------------------------
  // Data fetching
  // -------------------------------------------------------------------------

  _weekBounds() {
    const now = new Date();
    // Monday-based week
    const mon = new Date(now);
    mon.setDate(now.getDate() - ((now.getDay() + 6) % 7) + this._weekOffset * 7);
    mon.setHours(0, 0, 0, 0);
    const sun = new Date(mon);
    sun.setDate(mon.getDate() + 7);
    return { start: mon, end: sun };
  }

  async _refresh() {
    if (this._loading) return;
    this._loading = true;
    this._renderSkeleton();
    const { start, end } = this._weekBounds();
    this._weekStart = start;
    this._weekEnd = end;

    const settled = await Promise.allSettled(
      this._entityIds.map(id =>
        this._hass
          .callWS({ type: 'calendar/event/list', entity_id: id, start: start.toISOString(), end: end.toISOString() })
          .then(r => ({ id, evts: r || [] }))
      )
    );
    this._events = {};
    settled.forEach(r => {
      if (r.status === 'fulfilled') this._events[r.value.id] = r.value.evts;
    });

    this._loading = false;
    this._render();
  }

  // -------------------------------------------------------------------------
  // Rendering
  // -------------------------------------------------------------------------

  _renderSkeleton() {
    this.shadowRoot.innerHTML = `
      <ha-card style="padding:32px;text-align:center;color:var(--secondary-text-color)">
        Loading schedule…
      </ha-card>`;
  }

  _render() {
    const S = this._config.start_hour;
    const E = this._config.end_hour;
    const TOTAL_MINS = (E - S) * 60;
    const GRID_H = 540;       // px — height of the timed grid area
    const HDR_H  = 44;        // px — day-column header height
    const TIME_W = 40;        // px — width of the time-label column

    const DAY_NAMES  = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    const todayStr = new Date().toDateString();

    // Build one Date per column (Mon … Sun)
    const weekDates = DAY_NAMES.map((_, i) => {
      const d = new Date(this._weekStart);
      d.setDate(d.getDate() + i);
      return d;
    });

    // ---- Bucket events into day columns ----------------------------------
    const byDay = Array.from({ length: 7 }, () => []);
    this._entityIds.forEach(id => {
      const color = this._colors[id];
      (this._events[id] || []).forEach(evt => {
        const evStart = new Date(evt.start);
        const evEnd   = new Date(evt.end);
        const di = weekDates.findIndex(d => d.toDateString() === evStart.toDateString());
        if (di === -1) return;
        const startMins = evStart.getHours() * 60 + evStart.getMinutes();
        const endMins   = Math.min(evEnd.getHours() * 60 + evEnd.getMinutes(), E * 60);
        if (endMins <= S * 60) return; // outside visible range
        byDay[di].push({
          color,
          disabled: evt.summary.startsWith('⏸'),
          startMins: Math.max(startMins, S * 60),
          endMins,
          summary: evt.summary,
          description: evt.description || '',
        });
      });
    });

    // ---- Hour grid lines (right-aligned labels inside time column) -------
    const hourLines = [];
    for (let h = S; h <= E; h++) {
      const pct = ((h - S) / (E - S)) * 100;
      const borderColor = h === S ? 'transparent' : '#e0e0e0';
      hourLines.push(`
        <div style="position:absolute;top:${pct}%;left:0;right:0;
                    border-top:1px solid ${borderColor};pointer-events:none">
          <span style="position:absolute;right:6px;transform:translateY(-50%);
                       font-size:10px;color:#9e9e9e;white-space:nowrap;line-height:1">
            ${String(h).padStart(2, '0')}:00
          </span>
        </div>`);
    }

    // ---- Event blocks ----------------------------------------------------
    const eventBlock = (ev) => {
      const top    = ((ev.startMins - S * 60) / TOTAL_MINS) * 100;
      const height = Math.max(0.8, ((ev.endMins - ev.startMins) / TOTAL_MINS) * 100);
      const dur    = ev.endMins - ev.startMins;
      const bg     = ev.disabled ? ev.color + '22' : ev.color + 'e0';
      const border = ev.disabled ? `1.5px dashed ${ev.color}99` : `1px solid ${ev.color}`;
      const text   = ev.disabled ? ev.color : '#fff';
      // Show time label only when block is tall enough
      const label  = height > 4
        ? `${ev.disabled ? '⏸ ' : ''}${_fmtTime(ev.startMins)}`
        : '';
      const sublabel = height > 8 ? `<div style="font-size:9px;opacity:.8">${dur}m</div>` : '';
      const tooltip = `${ev.summary}\n${ev.description}`.trim();
      return `
        <div title="${tooltip}"
             style="position:absolute;
                    top:calc(${top}% + 1px);
                    height:calc(${height}% - 2px);
                    left:1px;right:1px;
                    background:${bg};
                    border:${border};
                    border-radius:3px;
                    overflow:hidden;
                    box-sizing:border-box">
          <div style="padding:2px 3px;font-size:10px;font-weight:600;
                      color:${text};white-space:nowrap;overflow:hidden;
                      text-overflow:ellipsis;line-height:1.3">
            ${label}${sublabel}
          </div>
        </div>`;
    };

    // ---- Day columns -----------------------------------------------------
    const colHtml = weekDates.map((date, i) => {
      const isToday   = date.toDateString() === todayStr;
      const dateLabel = date.toLocaleDateString(undefined, { day: 'numeric', month: 'numeric' });
      const hdrColor  = isToday
        ? 'var(--primary-color,#03a9f4)'
        : 'var(--primary-text-color,#212121)';
      const bodyBg    = isToday ? 'rgba(var(--rgb-primary-color,3,169,244),.04)' : 'transparent';
      const leftBorder = isToday
        ? '1px solid var(--primary-color,#03a9f4)'
        : '1px solid #e8e8e8';
      return `
        <div style="flex:1;min-width:0;display:flex;flex-direction:column">
          <div style="height:${HDR_H}px;display:flex;flex-direction:column;
                      align-items:center;justify-content:center;
                      font-weight:${isToday ? 700 : 500};color:${hdrColor}">
            <div style="font-size:12px">${DAY_NAMES[i]}</div>
            <div style="font-size:10px;opacity:.65">${dateLabel}</div>
          </div>
          <div style="flex:1;position:relative;background:${bodyBg};border-left:${leftBorder}">
            ${byDay[i].map(eventBlock).join('')}
          </div>
        </div>`;
    }).join('');

    // ---- Legend ----------------------------------------------------------
    const legendHtml = this._entityIds.map(id => {
      const name  = (this._hass.states[id]?.attributes?.friendly_name || id)
        .replace(' \u2014 ', ' · ');  // "Device — Zone" → "Device · Zone"
      const color = this._colors[id];
      return `
        <div style="display:inline-flex;align-items:center;gap:5px;
                    margin:2px 12px 2px 0;font-size:11px;
                    color:var(--secondary-text-color)">
          <div style="width:10px;height:10px;border-radius:2px;
                      background:${color};flex-shrink:0"></div>
          <span>${name}</span>
        </div>`;
    }).join('');

    // ---- Week label ------------------------------------------------------
    const fmt = { day: 'numeric', month: 'short' };
    const endDay = new Date(+this._weekEnd - 1000);
    const weekLabel = `${this._weekStart.toLocaleDateString(undefined, fmt)} – ${endDay.toLocaleDateString(undefined, { ...fmt, year: 'numeric' })}`;

    // ---- Assemble --------------------------------------------------------
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block }
        .wrap  { padding:12px 16px 16px }
        .toolbar { display:flex;align-items:center;justify-content:space-between;margin-bottom:6px }
        .title  { font-size:14px;font-weight:600;color:var(--primary-text-color,#212121) }
        .nav    { cursor:pointer;background:none;
                  border:1px solid var(--divider-color,#e0e0e0);
                  border-radius:4px;padding:2px 11px;font-size:17px;line-height:1.4;
                  color:var(--primary-text-color,#212121) }
        .nav:hover { background:rgba(0,0,0,.07) }
        .legend { margin-bottom:8px;line-height:1.8 }
        .grid   { display:flex }
        .time-axis { width:${TIME_W}px;flex-shrink:0;padding-top:${HDR_H}px }
        .days   { flex:1;min-width:0;display:flex;gap:2px }
      </style>
      <ha-card>
        <div class="wrap">
          <div class="toolbar">
            <button class="nav" id="prev">&#8249;</button>
            <span class="title">🌿 ${this._config.title} &nbsp;·&nbsp; ${weekLabel}</span>
            <button class="nav" id="next">&#8250;</button>
          </div>
          <div class="legend">${legendHtml}</div>
          <div class="grid">
            <div class="time-axis">
              <div style="position:relative;height:${GRID_H}px">
                ${hourLines.join('')}
              </div>
            </div>
            <div class="days" style="height:${HDR_H + GRID_H}px">
              ${colHtml}
            </div>
          </div>
        </div>
      </ha-card>`;

    this.shadowRoot.getElementById('prev').addEventListener('click', () => {
      this._weekOffset--;
      this._refresh();
    });
    this.shadowRoot.getElementById('next').addEventListener('click', () => {
      this._weekOffset++;
      this._refresh();
    });
  }
}

// ---- Helpers ---------------------------------------------------------------

function _fmtTime(totalMins) {
  const h = Math.floor(totalMins / 60);
  const m = totalMins % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

// ---- Registration ----------------------------------------------------------

customElements.define('garden-timer-card', GardenTimerCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'garden-timer-card',
  name: 'Garden Timer Schedule',
  description: 'Week-view timetable card for Tuya Garden Timers',
  preview: false,
});
