/**
 * garden-timer-card.js  v5
 *
 *
 * Renders a week-grid view:
 *   - 7 day columns (Mon–Sun), navigable with < / >
 *   - Time axis on the left (default 04:00–22:00, configurable)
 *   - Each calendar entity gets its own colour; blocks are sized by duration
 *     and positioned by start time
 *   - Disabled schedules (⏸ prefix) shown as faded fill + dashed border
 *     in the same hue as the zone
 *   - Overlapping runs are laid out side by side; overlaps between enabled
 *     schedules are outlined in red and listed under the grid as clashes
 *   - Legend across the top, grouped by device
 *   - Refreshes itself every `refresh_minutes`, and immediately when the
 *     integration reports new schedule data; footer shows data age
 *
 * Card config (all optional):
 *   type: custom:garden-timer-card
 *   entity_ids:            # list of calendar entity IDs; omit to auto-discover
 *     - calendar.front_garden_timer_zone_1
 *   start_hour: 4          # start of visible time range (default 4)
 *   end_hour: 22           # end of visible time range (default 22)
 *   title: "Garden"        # card title (default "Watering Schedule")
 *   refresh_minutes: 5     # how often to re-fetch events (default 5)
 */
const CARD_VERSION = 'v5';

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

const CLASH_COLOR = '#D50000';
const SEP = ' — '; // "Device — Zone" separator used in friendly names

class GardenTimerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._weekOffset = 0;
    this._loading = false;
    this._events = {};
    this._entityIds = [];
    this._colors = {};
    this._signature = null;
    this._fetchedAt = null;
    this._ticker = null;
  }

  static getStubConfig() {
    return { start_hour: 4, end_hour: 22 };
  }

  setConfig(config) {
    this._config = {
      start_hour: 4,
      end_hour: 22,
      title: 'Watering Schedule',
      refresh_minutes: 5,
      ...config,
    };
  }

  set hass(hass) {
    this._hass = hass;
    // Re-fetch whenever the set of calendars, their availability, or the
    // integration's schedule timestamp changes (e.g. after HA restarts, or
    // when a schedule edited in the Tuya app is picked up).
    this._resolveEntities();
    const sig = this._entityIds
      .map(id => {
        const s = hass.states[id];
        return `${id}:${s ? s.state === 'unavailable' : 'x'}:${s?.attributes?.schedule_updated || ''}`;
      })
      .join('|');
    if (sig !== this._signature) {
      this._signature = sig;
      this._refresh();
    }
  }

  connectedCallback() {
    // One-minute ticker: keeps the "updated … ago" label current and triggers
    // the periodic re-fetch.
    this._ticker = setInterval(() => {
      const due = this._config.refresh_minutes * 60000;
      if (!this._fetchedAt || Date.now() - this._fetchedAt >= due) {
        this._refresh();
      } else {
        this._updateStatus();
      }
    }, 60000);
  }

  disconnectedCallback() {
    clearInterval(this._ticker);
    this._ticker = null;
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
          (s.attributes.friendly_name || '').includes(SEP)
        )
        .map(([id]) => id);
    }
    // Order by device, then zone, so legend groups and colours are stable
    this._entityIds = [...this._entityIds].sort((a, b) => {
      const na = this._names(a), nb = this._names(b);
      return na.device.localeCompare(nb.device) || na.zone.localeCompare(nb.zone);
    });
    this._colors = {};
    this._entityIds.forEach((id, i) => {
      this._colors[id] = PALETTE[i % PALETTE.length];
    });
  }

  /** {device, zone} for an entity, from attributes or the friendly name. */
  _names(id) {
    const a = this._hass.states[id]?.attributes || {};
    if (a.device_name && a.zone_name) return { device: a.device_name, zone: a.zone_name };
    const full  = a.friendly_name || id;
    const parts = full.split(SEP);
    return parts.length >= 2
      ? { device: parts[0], zone: parts.slice(1).join(SEP) }
      : { device: '', zone: full };
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
    if (!this._hass || !this._config) return;
    if (this._loading) { this._pending = true; return; }
    this._loading = true;
    this._pending = false;
    // Only show the skeleton on first load; later refreshes swap in place
    if (!this._fetchedAt) this._renderSkeleton();
    const { start, end } = this._weekBounds();

    const ids = this._entityIds;
    const settled = await Promise.allSettled(
      ids.map(id => {
        const path = `calendars/${id}?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`;
        return this._hass
          .callApi('GET', path)
          .then(r => ({ id, evts: Array.isArray(r) ? r : [] }));
      })
    );
    this._weekStart = start;
    this._weekEnd = end;
    this._events = {};
    this._errors = [];
    settled.forEach((r, i) => {
      if (r.status === 'fulfilled') {
        this._events[r.value.id] = r.value.evts;
      } else {
        const reason = r.reason;
        const msg = reason?.message || reason?.error || (reason?.status_code ? `HTTP ${reason.status_code}` : String(reason));
        this._errors.push(`${this._names(ids[i]).zone}: ${msg}`);
      }
    });
    if (this._errors.length) {
      console.error('garden-timer-card fetch errors:', this._errors);
    }
    this._fetchedAt = Date.now();
    this._loading = false;
    this._render();
    if (this._pending) this._refresh();
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

  /** Text for the footer: when Tuya data was fetched + when the card refreshed. */
  _statusText() {
    const stamps = this._entityIds
      .map(id => this._hass.states[id]?.attributes?.schedule_updated)
      .filter(Boolean)
      .map(s => Date.parse(s));
    const parts = [];
    let stale = false;
    if (stamps.length) {
      const newest = Math.max(...stamps);
      const mins = (Date.now() - newest) / 60000;
      stale = mins > Math.max(30, this._config.refresh_minutes * 3);
      parts.push(`Tuya data ${_ago(newest)} (${_clock(new Date(newest))})`);
    }
    if (this._fetchedAt) parts.push(`card refreshed ${_clock(new Date(this._fetchedAt))}`);
    return { text: parts.join(' · '), stale };
  }

  _updateStatus() {
    const el = this.shadowRoot.getElementById('status');
    if (!el) return;
    const { text, stale } = this._statusText();
    el.textContent = (stale ? '⚠ ' : '') + text;
    el.style.color = stale ? 'var(--warning-color,#FB8C00)' : '';
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
    // Raw (unclipped) minutes are kept for clash detection; clipped ones for
    // drawing inside the visible range.
    const byDay = Array.from({ length: 7 }, () => []);
    this._entityIds.forEach(id => {
      const color = this._colors[id];
      const names = this._names(id);
      (this._events[id] || []).forEach(evt => {
        // HA calendar API wraps times as {dateTime:"..."} or {date:"YYYY-MM-DD"}
        const evStart = new Date(evt.start.dateTime || evt.start.date || evt.start);
        const evEnd   = new Date(evt.end.dateTime   || evt.end.date   || evt.end);
        const di = weekDates.findIndex(d => d.toDateString() === evStart.toDateString());
        if (di === -1) return;
        const rawStart = evStart.getHours() * 60 + evStart.getMinutes();
        const rawEnd   = rawStart + Math.round((evEnd - evStart) / 60000);
        byDay[di].push({
          id,
          color,
          names,
          disabled: evt.summary.startsWith('⏸'),
          rawStart,
          rawEnd,
          summary: evt.summary,
          description: evt.description || '',
          clashWith: [],
        });
      });
    });

    // ---- Overlaps: lanes for layout, clashes between enabled runs ---------
    const clashes = new Map(); // key → {a, b, start, end, days:Set}
    byDay.forEach((evs, di) => {
      evs.sort((a, b) => a.rawStart - b.rawStart || a.rawEnd - b.rawEnd);
      for (let i = 0; i < evs.length; i++) {
        for (let j = i + 1; j < evs.length && evs[j].rawStart < evs[i].rawEnd; j++) {
          const a = evs[i], b = evs[j];
          if (a.disabled || b.disabled || a.id === b.id) continue;
          a.clashWith.push(b);
          b.clashWith.push(a);
          const key = `${a.id}@${a.rawStart}|${b.id}@${b.rawStart}`;
          if (!clashes.has(key)) {
            clashes.set(key, {
              a, b,
              start: Math.max(a.rawStart, b.rawStart),
              end: Math.min(a.rawEnd, b.rawEnd),
              days: [],
            });
          }
          clashes.get(key).days.push(di);
        }
      }
      _assignLanes(evs);
    });

    // ---- Hour grid lines (right-aligned labels inside time column) -------
    const hourLines = [];
    for (let h = S; h <= E; h++) {
      const pct = ((h - S) / (E - S)) * 100;
      const borderColor = h === S ? 'transparent' : 'var(--divider-color,#e0e0e0)';
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
      const startMins = Math.max(ev.rawStart, S * 60);
      const endMins   = Math.min(ev.rawEnd, E * 60);
      if (endMins <= startMins) return ''; // outside visible range
      const top    = ((startMins - S * 60) / TOTAL_MINS) * 100;
      const height = Math.max(0.8, ((endMins - startMins) / TOTAL_MINS) * 100);
      const dur    = ev.rawEnd - ev.rawStart;
      const clash  = ev.clashWith.length > 0;
      const bg     = ev.disabled ? ev.color + '22' : ev.color + 'e0';
      const border = ev.disabled ? `1.5px dashed ${ev.color}99` : `1px solid ${ev.color}`;
      const ring   = clash ? `box-shadow:0 0 0 2px ${CLASH_COLOR};z-index:2;` : '';
      const text   = ev.disabled ? ev.color : '#fff';
      const width  = 100 / ev.lanes;
      // Show time label only when block is tall enough
      const label  = height > 4
        ? `${clash ? '⚠ ' : ''}${ev.disabled ? '⏸ ' : ''}${_fmtTime(ev.rawStart)}`
        : '';
      const sublabel = height > 8 ? `<div style="font-size:9px;opacity:.8">${dur}m</div>` : '';
      const tooltip = [
        `${ev.disabled ? '⏸ ' : ''}${ev.names.device}${SEP}${ev.names.zone}`,
        `${_fmtTime(ev.rawStart)}–${_fmtTime(ev.rawEnd)} · ${ev.description}`,
        ...ev.clashWith.map(o =>
          `⚠ overlaps ${o.names.device}${SEP}${o.names.zone} (${_fmtTime(o.rawStart)}–${_fmtTime(o.rawEnd)})`),
      ].join('\n');
      return `
        <div title="${_esc(tooltip)}"
             style="position:absolute;
                    top:calc(${top}% + 1px);
                    height:calc(${height}% - 2px);
                    left:calc(${ev.lane * width}% + 1px);
                    width:calc(${width}% - 2px);
                    background:${bg};
                    border:${border};
                    ${ring}
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
        : '1px solid var(--divider-color,#e8e8e8)';
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

    // ---- Legend, grouped by device ----------------------------------------
    const groups = new Map();
    this._entityIds.forEach(id => {
      const { device, zone } = this._names(id);
      if (!groups.has(device)) groups.set(device, []);
      groups.get(device).push({ id, zone });
    });
    const legendHtml = [...groups].map(([device, zones]) => `
      <div class="lg-group">
        <div class="lg-device">${_esc(device || 'Other')}</div>
        ${zones.map(({ id, zone }) => `
          <div class="lg-zone">
            <div class="swatch" style="background:${this._colors[id]}"></div>
            <span>${_esc(zone)}</span>
          </div>`).join('')}
      </div>`).join('');

    // ---- Clash list --------------------------------------------------------
    const clashList = [...clashes.values()]
      .sort((x, y) => x.days[0] - y.days[0] || x.start - y.start);
    const clashHtml = clashList.length ? `
      <div class="clashes">
        <div class="clash-title">⚠ ${clashList.length} overlapping run${clashList.length > 1 ? 's' : ''} this week</div>
        ${clashList.map(c => {
          const days = c.days.length === 7 ? 'every day' : c.days.map(d => DAY_NAMES[d]).join(', ');
          const who = (e) => `<span class="swatch" style="background:${e.color}"></span>${_esc(e.names.device)}${SEP}${_esc(e.names.zone)} ${_fmtTime(e.rawStart)}–${_fmtTime(e.rawEnd)}`;
          return `<div class="clash-row">
              <b>${days}</b> · ${_fmtTime(c.start)}–${_fmtTime(c.end)}:
              ${who(c.a)} &nbsp;↔&nbsp; ${who(c.b)}
            </div>`;
        }).join('')}
      </div>` : '';

    const errHtml = this._errors && this._errors.length
      ? `<div style="color:var(--error-color,red);font-size:11px;margin-bottom:6px">⚠ Could not load: ${_esc(this._errors.join('; '))}</div>`
      : '';

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
        .title  { font-size:14px;font-weight:600;color:var(--primary-text-color,#212121);text-align:center }
        .nav    { cursor:pointer;background:none;
                  border:1px solid var(--divider-color,#e0e0e0);
                  border-radius:4px;padding:2px 11px;font-size:17px;line-height:1.4;
                  color:var(--primary-text-color,#212121) }
        .nav:hover { background:rgba(127,127,127,.12) }
        .legend { display:flex;flex-wrap:wrap;gap:6px 18px;margin-bottom:10px }
        .lg-group { display:flex;flex-direction:column;gap:2px }
        .lg-device { font-size:11px;font-weight:600;color:var(--primary-text-color,#212121) }
        .lg-zone { display:flex;align-items:center;gap:5px;font-size:11px;
                   color:var(--secondary-text-color) }
        .swatch { display:inline-block;width:10px;height:10px;border-radius:2px;flex-shrink:0;
                  vertical-align:-1px;margin-right:4px }
        .lg-zone .swatch { margin-right:0 }
        .grid   { display:flex }
        .time-axis { width:${TIME_W}px;flex-shrink:0;padding-top:${HDR_H}px }
        .days   { flex:1;min-width:0;display:flex;gap:2px }
        .clashes { margin-top:10px;padding:8px 10px;border-radius:6px;
                   border:1px solid ${CLASH_COLOR}55;background:${CLASH_COLOR}0d;
                   font-size:11px;color:var(--primary-text-color,#212121) }
        .clash-title { font-weight:600;color:${CLASH_COLOR};margin-bottom:4px }
        .clash-row { line-height:1.7 }
        .status { margin-top:8px;font-size:10px;color:var(--secondary-text-color);text-align:right }
      </style>
      <ha-card>
        <div class="wrap">
          <div class="toolbar">
            <button class="nav" id="prev">&#8249;</button>
            <span class="title">🌿 ${_esc(this._config.title)} &nbsp;·&nbsp; ${weekLabel} <span style="font-size:10px;opacity:.4">${CARD_VERSION}</span></span>
            <button class="nav" id="next">&#8250;</button>
          </div>
          ${errHtml}
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
          ${clashHtml}
          <div class="status" id="status"></div>
        </div>
      </ha-card>`;

    this._updateStatus();
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
  const h = Math.floor(totalMins / 60) % 24;
  const m = totalMins % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

function _clock(d) {
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

function _ago(ms) {
  const mins = Math.round((Date.now() - ms) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  return hrs < 48 ? `${hrs} h ago` : `${Math.round(hrs / 24)} days ago`;
}

function _esc(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/**
 * Give each event (sorted by start) a `lane` and the lane count of its
 * overlap cluster (`lanes`), so overlapping blocks sit side by side.
 */
function _assignLanes(evs) {
  let cluster = [], laneEnds = [], clusterEnd = -1;
  const close = () => cluster.forEach(e => { e.lanes = laneEnds.length; });
  evs.forEach(ev => {
    if (ev.rawStart >= clusterEnd) {
      close();
      cluster = []; laneEnds = [];
    }
    let lane = laneEnds.findIndex(end => end <= ev.rawStart);
    if (lane === -1) { lane = laneEnds.length; laneEnds.push(0); }
    laneEnds[lane] = ev.rawEnd;
    ev.lane = lane;
    cluster.push(ev);
    clusterEnd = Math.max(clusterEnd, ev.rawEnd);
  });
  close();
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
