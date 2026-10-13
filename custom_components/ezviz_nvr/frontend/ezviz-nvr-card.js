/*
 * EZVIZ NVR card for Home Assistant.
 * Author: Daniel Dias (@dmdias) - https://github.com/dmdias/ha-ezviz-nvr
 *
 * Served and loaded automatically by the EZVIZ NVR integration: a camera grid,
 * a live / playback player and a 24 h timeline of the recordings on the NVR disk.
 *
 *   type: custom:ezviz-nvr-card
 *   columns: 2          # optional, camera grid columns
 */

const CARD_VERSION = "0.1.0";
const DOMAIN = "ezviz_nvr";

const TEXT = {
  pt: {
    live: "Direto", playback: "Gravações", back: "Câmaras", offline: "Offline", disk: "Disco",
    no_recordings: "Sem gravações neste dia.", loading: "A carregar…", none: "Nenhuma câmara do gravador encontrada.",
    tap_timeline: "Toca na linha do tempo para ver a gravação a partir dessa hora.", playing_from: "A reproduzir desde",
  },
  en: {
    live: "Live", playback: "Playback", back: "Cameras", offline: "Offline", disk: "Disk",
    no_recordings: "No recordings on this day.", loading: "Loading…", none: "No NVR cameras found.",
    tap_timeline: "Tap the timeline to play the recording from that time.", playing_from: "Playing from",
  },
};

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const pad = (n) => String(n).padStart(2, "0");
const isoDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const hhmm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const hhmmss = (d) => `${hhmm(d)}:${pad(d.getSeconds())}`;

class EzvizNvrCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._view = null; // selected channel key, or null for the grid
    this._mode = "live";
    this._day = new Date();
    this._recordings = {};
    this._tick = 0;
  }

  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  getCardSize() {
    return 8;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._discover();
    if (this._player) this._player.hass = hass;
    const sig = this._sig();
    if (sig !== this._signature) {
      this._signature = sig;
      this._render();
    }
  }

  connectedCallback() {
    // Refresh the grid snapshots (every 30 s, they come from the video) and the playback position
    this._timer = setInterval(() => {
      this._tick++;
      if (!this._view) this._render();
      else this._updatePosition();
    }, 30000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  get _t() {
    const lang = (this._hass?.locale?.language || this._hass?.language || "en").slice(0, 2);
    return TEXT[lang] || TEXT.en;
  }

  /* ----- discovery ----- */

  async _discover() {
    let registry = [];
    try {
      registry = await this._hass.callWS({ type: "config/entity_registry/list" });
    } catch (err) {
      registry = [];
    }
    const channels = {};
    const disks = [];
    for (const e of registry) {
      if (e.platform !== DOMAIN || e.disabled_by) continue;
      const m = /^(.+)_(\d+)_(live|playback)$/.exec(e.unique_id || "");
      if (m && e.entity_id.startsWith("camera.")) {
        const key = `${m[1]}_${m[2]}`;
        (channels[key] ??= { key, channel: Number(m[2]) })[m[3]] = e.entity_id;
      } else if (/_disk\w+_free$/.test(e.unique_id || "")) {
        disks.push({ free: e.entity_id, used: e.entity_id.replace(/_free$/, "_used") });
      } else if (/_disk\w+_used_percent$/.test(e.unique_id || "")) {
        const last = disks[disks.length - 1];
        if (last) last.used = e.entity_id;
      }
    }
    this._channels = Object.values(channels).sort((a, b) => a.channel - b.channel);
    this._disks = disks;
    this._signature = "";
    this._render();
  }

  _sig() {
    if (!this._channels || !this._hass) return String(this._channels);
    return this._channels
      .flatMap((c) => [c.live, c.playback])
      .map((id) => {
        const s = this._hass.states[id];
        return s ? `${s.state}:${s.attributes.playback_start || ""}` : "";
      })
      .join("|");
  }

  _name(ch) {
    return this._hass.states[ch.live]?.attributes.friendly_name || `Camera ${ch.channel}`;
  }

  _picture(id) {
    const pic = this._hass.states[id]?.attributes.entity_picture;
    return pic ? `${pic}${pic.includes("?") ? "&" : "?"}t=${this._tick}` : "";
  }

  /* ----- rendering ----- */

  _render() {
    if (!this._config) return;
    let body;
    if (!this._hass || this._channels === undefined) body = `<div class="empty">${esc(this._t.loading)}</div>`;
    else if (!this._channels.length) body = `<div class="empty">${esc(this._t.none)}</div>`;
    else body = this._view ? this._playerView() : this._gridView();
    this.shadowRoot.innerHTML = `<style>${STYLES}</style><ha-card>${body}</ha-card>`;
    this._bind();
    this._mountPlayer();
  }

  _diskBar() {
    const d = this._disks?.[0];
    if (!d) return "";
    const used = Number(this._hass.states[d.used]?.state);
    const free = Number(this._hass.states[d.free]?.state);
    if (!Number.isFinite(used)) return "";
    return `<div class="disk"><span>${esc(this._t.disk)}</span><div class="bar"><i style="width:${used}%"></i></div>
      <span>${Math.round(used)}%${Number.isFinite(free) ? ` · ${Math.round(free)} GB` : ""}</span></div>`;
  }

  _gridView() {
    const cols = Number(this._config.columns) || 2;
    const tiles = this._channels
      .map((ch) => {
        const st = this._hass.states[ch.live];
        const offline = !st || st.state === "unavailable";
        return `<button class="tile" data-open="${esc(ch.key)}">
          ${offline ? `<div class="snap off">${esc(this._t.offline)}</div>` : `<img class="snap" src="${esc(this._picture(ch.live))}" alt="">`}
          <div class="tile-bar"><span class="dot ${offline ? "" : "on"}"></span>${esc(this._name(ch))}</div></button>`;
      })
      .join("");
    return `<div class="head"><b>EZVIZ NVR</b>${this._diskBar()}</div>
      <div class="grid" style="grid-template-columns:repeat(${cols},1fr)">${tiles}</div>`;
  }

  _playerView() {
    const ch = this._channels.find((c) => c.key === this._view);
    if (!ch) return this._gridView();
    const t = this._t;
    const seg = (m) => `<button data-mode="${m}" class="${this._mode === m ? "on" : ""}">${esc(t[m])}</button>`;
    return `
      <div class="head">
        <button class="back" data-back>‹ ${esc(t.back)}</button>
        <b>${esc(this._name(ch))}</b>
        <div class="seg">${seg("live")}${seg("playback")}</div>
      </div>
      <div class="player"><div class="player-slot"></div>
        ${this._mode === "live" ? `<span class="badge live">● LIVE</span>` : `<span class="badge rec" data-pos></span>`}</div>
      ${this._mode === "playback" ? this._timelineView(ch) : ""}`;
  }

  _timelineView(ch) {
    const t = this._t;
    const key = `${ch.key}:${isoDay(this._day)}`;
    const data = this._recordings[key];
    if (!data) this._loadRecordings(ch);
    const dayStart = new Date(this._day.getFullYear(), this._day.getMonth(), this._day.getDate());
    const span = 86400000;
    const pct = (d) => Math.max(0, Math.min(100, ((d - dayStart) / span) * 100));
    let bars = "";
    if (data?.segments) {
      bars = data.segments
        .map((s) => {
          const a = pct(new Date(s.start));
          const b = pct(new Date(s.end));
          return `<i class="rec-seg" style="left:${a}%;width:${Math.max(0.2, b - a)}%"></i>`;
        })
        .join("");
    }
    const ticks = Array.from({ length: 9 }, (_, i) => `<span style="left:${(i * 3 * 100) / 24}%">${pad(i * 3)}:00</span>`).join("");
    const start = this._hass.states[ch.playback]?.attributes.playback_start;
    const marker = start && isoDay(new Date(start)) === isoDay(this._day) ? `<i class="marker" style="left:${pct(new Date(start))}%"></i>` : "";
    const today = isoDay(this._day) === isoDay(new Date());
    return `
      <div class="timeline-head">
        <button data-day="-1">◀</button><span>${esc(isoDay(this._day))}</span><button data-day="1" ${today ? "disabled" : ""}>▶</button>
        <span class="spacer"></span>
        <button data-nudge="-60">−1 min</button><button data-nudge="60">+1 min</button>
      </div>
      <div class="timeline" data-timeline>${bars}${marker}<div class="hover" hidden></div></div>
      <div class="ticks">${ticks}</div>
      <div class="hint">${data && !data.segments?.length ? esc(t.no_recordings) : data?.error ? esc(data.error) : esc(t.tap_timeline)}</div>`;
  }

  async _loadRecordings(ch) {
    const key = `${ch.key}:${isoDay(this._day)}`;
    if (this._loading === key) return;
    this._loading = key;
    try {
      const res = await this._hass.callService(DOMAIN, "get_recordings", { date: isoDay(this._day) }, { entity_id: ch.live }, false, true);
      const response = res?.response?.[ch.live] || res?.response || {};
      this._recordings[key] = { segments: response.segments || [] };
    } catch (err) {
      this._recordings[key] = { segments: [], error: err.message || String(err) };
    }
    this._loading = null;
    this._render();
  }

  /** The player is a stock picture-entity card in live view, kept across renders. */
  async _mountPlayer() {
    const slot = this.shadowRoot.querySelector(".player-slot");
    if (!slot) return;
    const ch = this._channels.find((c) => c.key === this._view);
    const entity = this._mode === "live" ? ch.live : ch.playback;
    const start = this._hass.states[ch.playback]?.attributes.playback_start;
    const want = `${entity}|${this._mode === "playback" ? start : ""}`;
    if (!this._player || this._playerKey !== want) {
      this._playerKey = want;
      const helpers = await window.loadCardHelpers();
      const el = await helpers.createCardElement({ type: "picture-entity", entity, camera_view: "live", show_name: false, show_state: false, tap_action: { action: "none" } });
      el.hass = this._hass;
      this._player = el;
      this._playStartedAt = Date.now();
    }
    const current = this.shadowRoot.querySelector(".player-slot");
    if (current && this._player.parentNode !== current) current.appendChild(this._player);
    this._updatePosition();
  }

  _updatePosition() {
    const pos = this.shadowRoot.querySelector("[data-pos]");
    const ch = this._channels?.find((c) => c.key === this._view);
    if (!pos || !ch) return;
    const start = this._hass.states[ch.playback]?.attributes.playback_start;
    if (!start) return;
    const now = new Date(new Date(start).getTime() + (Date.now() - (this._playStartedAt || Date.now())));
    pos.textContent = `▶ ${this._t.playing_from} ${hhmmss(new Date(start))} · ${hhmmss(now)}`;
  }

  async _play(ch, when) {
    await this._hass.callService(DOMAIN, "play", { start: when.toISOString() }, { entity_id: ch.playback });
    this._playerKey = null; // reconnect the player to the new position
  }

  /* ----- events ----- */

  _bind() {
    const root = this.shadowRoot;
    root.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => ((this._view = b.dataset.open), (this._mode = "live"), (this._player = null), this._render())));
    const back = root.querySelector("[data-back]");
    if (back) back.onclick = () => ((this._view = null), (this._player = null), this._render());
    root.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => ((this._mode = b.dataset.mode), (this._day = new Date()), this._render())));
    root.querySelectorAll("[data-day]").forEach(
      (b) =>
        (b.onclick = () => {
          const d = new Date(this._day);
          d.setDate(d.getDate() + Number(b.dataset.day));
          this._day = d > new Date() ? new Date() : d;
          this._render();
        })
    );
    const ch = this._channels?.find((c) => c.key === this._view);
    root.querySelectorAll("[data-nudge]").forEach(
      (b) =>
        (b.onclick = () => {
          const start = this._hass.states[ch.playback]?.attributes.playback_start;
          if (!start) return;
          const elapsed = Date.now() - (this._playStartedAt || Date.now());
          this._play(ch, new Date(new Date(start).getTime() + elapsed + Number(b.dataset.nudge) * 1000));
        })
    );
    const line = root.querySelector("[data-timeline]");
    if (line && ch) {
      const at = (ev) => {
        const r = line.getBoundingClientRect();
        const f = Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width));
        const dayStart = new Date(this._day.getFullYear(), this._day.getMonth(), this._day.getDate());
        return new Date(dayStart.getTime() + f * 86400000);
      };
      const hover = line.querySelector(".hover");
      line.onpointermove = (ev) => {
        const r = line.getBoundingClientRect();
        hover.hidden = false;
        hover.style.left = `${ev.clientX - r.left}px`;
        hover.textContent = hhmm(at(ev));
      };
      line.onpointerleave = () => (hover.hidden = true);
      line.onclick = (ev) => {
        const when = at(ev);
        if (when > new Date()) return;
        this._play(ch, when);
      };
    }
  }
}

const STYLES = `
  :host { --bg:#0d111a; --panel:#1b2130; --panel2:#2a3142; --text:#e8ebf2; --muted:#9aa3b5; --accent:#5fd3a8; --rec:#3d8bfd; display:block; }
  ha-card { display:block; background:var(--bg); color:var(--text); border-radius:16px; padding:14px; border:none; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  .empty { padding:30px; text-align:center; color:var(--muted); }
  .head { display:flex; align-items:center; gap:12px; margin-bottom:12px; flex-wrap:wrap; }
  .head b { font-size:17px; }
  .disk { margin-left:auto; display:flex; align-items:center; gap:8px; font-size:13px; color:var(--muted); }
  .disk .bar { width:120px; height:7px; background:#3a4254; border-radius:4px; overflow:hidden; }
  .disk .bar i { display:block; height:100%; background:linear-gradient(90deg,#5fd3a8,#3d8bfd); }
  .grid { display:grid; gap:10px; }
  .tile { position:relative; padding:0; border:none; border-radius:12px; overflow:hidden; background:var(--panel); cursor:pointer; color:var(--text); font-family:inherit; aspect-ratio:16/9; }
  .snap { width:100%; height:100%; object-fit:cover; display:block; }
  .snap.off { display:flex; align-items:center; justify-content:center; color:var(--muted); font-size:14px; }
  .tile-bar { position:absolute; left:0; right:0; bottom:0; display:flex; align-items:center; gap:7px; padding:8px 10px; font-size:14px; text-align:left;
    background:linear-gradient(transparent, rgba(0,0,0,.75)); }
  .dot { width:8px; height:8px; border-radius:50%; background:#7b8494; } .dot.on { background:var(--accent); box-shadow:0 0 6px var(--accent); }
  .back { background:var(--panel2); color:var(--text); border:none; border-radius:10px; padding:7px 12px; cursor:pointer; font-family:inherit; font-size:14px; }
  .seg { margin-left:auto; display:inline-flex; background:var(--panel2); border-radius:10px; padding:3px; }
  .seg button { background:none; border:1px solid transparent; color:var(--muted); padding:6px 14px; border-radius:8px; cursor:pointer; font-family:inherit; font-size:14px; }
  .seg button.on { color:var(--text); background:#3a4359; border-color:#6fd8c0; }
  .player { position:relative; border-radius:12px; overflow:hidden; background:#000; min-height:180px; }
  .player-slot > * { display:block; }
  .badge { position:absolute; top:10px; left:10px; padding:3px 9px; border-radius:6px; font-size:12px; font-weight:600; pointer-events:none; }
  .badge.live { background:#e5484d; color:#fff; } .badge.rec { background:rgba(0,0,0,.65); color:#fff; font-weight:500; }
  .timeline-head { display:flex; align-items:center; gap:8px; margin:12px 0 8px; font-size:14px; }
  .timeline-head button { background:var(--panel2); color:var(--text); border:none; border-radius:8px; padding:6px 10px; cursor:pointer; font-family:inherit; }
  .timeline-head button[disabled] { opacity:.35; cursor:default; }
  .spacer { flex:1; }
  .timeline { position:relative; height:34px; background:var(--panel2); border-radius:8px; cursor:pointer; overflow:hidden; touch-action:manipulation; }
  .rec-seg { position:absolute; top:6px; bottom:6px; background:var(--rec); border-radius:3px; opacity:.85; }
  .marker { position:absolute; top:0; bottom:0; width:2px; background:#ffcc4d; box-shadow:0 0 6px #ffcc4d; }
  .hover { position:absolute; top:-1px; transform:translateX(-50%); background:#000c; color:#fff; font-size:11px; padding:1px 5px; border-radius:4px; pointer-events:none; }
  .ticks { position:relative; height:18px; font-size:11px; color:var(--muted); }
  .ticks span { position:absolute; transform:translateX(-50%); top:3px; }
  .ticks span:first-child { transform:none; }
  .hint { color:var(--muted); font-size:12px; margin-top:4px; }
`;

customElements.define("ezviz-nvr-card", EzvizNvrCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "ezviz-nvr-card", name: "EZVIZ NVR", description: "Cameras, live view and recordings of an EZVIZ / Hikvision NVR", preview: false, documentationURL: "https://github.com/dmdias/ha-ezviz-nvr" });
console.info(`%c EZVIZ-NVR-CARD %c ${CARD_VERSION} `, "background:#3d8bfd;color:#fff", "background:#1b2130;color:#fff");
