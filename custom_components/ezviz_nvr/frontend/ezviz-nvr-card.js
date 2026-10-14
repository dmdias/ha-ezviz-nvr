/*
 * EZVIZ NVR card for Home Assistant.
 * Author: Daniel Dias (@dmdias) - https://github.com/dmdias/ha-ezviz-nvr
 *
 * Served and loaded automatically by the EZVIZ NVR integration: a camera grid,
 * a live / playback player and a 24 h timeline of the recordings on the NVR disk.
 *
 *   type: custom:ezviz-nvr-card
 *   columns: 2          # optional, maximum camera grid columns (tiles wrap on narrow screens)
 *   cloud_cameras: [camera.doorbell]   # optional, EZVIZ cloud cameras to list (default: all but the NVR)
 *
 * With face recognition on (integration options), a "People" view lists who
 * the cloud cameras saw, with every occurrence, and lets you name each person.
 */

const CARD_VERSION = "0.4.0";
const DOMAIN = "ezviz_nvr";

const TEXT = {
  pt: {
    live: "Direto", playback: "Gravações", back: "Câmaras", offline: "Offline", disk: "Disco", events: "Eventos",
    no_events: "Sem eventos neste dia.", cloud: "Cloud", no_picture: "Sem imagem",
    no_recordings: "Sem gravações neste dia.", loading: "A carregar…", none: "Nenhuma câmara do gravador encontrada.",
    tap_timeline: "Toca na linha do tempo para ver a gravação a partir dessa hora.", playing_from: "A reproduzir desde",
    people: "Pessoas", person: "Pessoa", unknown: "Desconhecido", no_people: "Ainda não foram encontradas caras.", seen: "visitas",
    stats: "Estatísticas", day: "Dia", week: "Semana", month: "Mês", this_day: "Hoje", this_week: "Esta semana", this_month: "Este mês",
    in_range: "No período", average: "Média", per_day: "por dia", per_week: "por semana", per_month: "por mês", events_word: "eventos", no_stats: "Sem visitas neste período.",
    last_seen: "Última vez", scan: "Analisar agora", scanning: "A analisar…", last_scan: "Última análise", name_placeholder: "Nome da pessoa",
    save: "Guardar", delete_person: "Apagar pessoa", confirm_delete: "Apagar esta pessoa e todas as suas caras?", not_this: "Não é esta pessoa? Mover para:",
    move: "Mover", remove_face: "Remover cara", with: "com", merged: "Juntou-se à pessoa com o mesmo nome.", all: "Todas", named: "Com nome",
  },
  en: {
    live: "Live", playback: "Playback", back: "Cameras", offline: "Offline", disk: "Disk", events: "Events",
    no_events: "No events on this day.", cloud: "Cloud", no_picture: "No picture",
    no_recordings: "No recordings on this day.", loading: "Loading…", none: "No NVR cameras found.",
    tap_timeline: "Tap the timeline to play the recording from that time.", playing_from: "Playing from",
    people: "People", person: "Person", unknown: "Unknown", no_people: "No faces found yet.", seen: "visits",
    stats: "Statistics", day: "Day", week: "Week", month: "Month", this_day: "Today", this_week: "This week", this_month: "This month",
    in_range: "In range", average: "Average", per_day: "per day", per_week: "per week", per_month: "per month", events_word: "events", no_stats: "No visits in this range.",
    last_seen: "Last seen", scan: "Scan now", scanning: "Scanning…", last_scan: "Last scan", name_placeholder: "Person's name",
    save: "Save", delete_person: "Delete person", confirm_delete: "Delete this person and all their faces?", not_this: "Not this person? Move to:",
    move: "Move", remove_face: "Remove face", with: "with", merged: "Merged with the person of the same name.", all: "All", named: "Named",
  },
};

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const pad = (n) => String(n).padStart(2, "0");
const isoDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const hhmm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const hhmmss = (d) => `${hhmm(d)}:${pad(d.getSeconds())}`;
const when = (iso) => {
  const d = iso ? new Date(iso) : null;
  return d && !isNaN(d) ? `${isoDay(d)} ${hhmm(d)}` : "";
};

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
    if (sig !== this._signature && !this._isPeopleView()) {
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

  _isPeopleView() {
    return this._view === "people" || this._view === "stats" || this._view?.startsWith("person:");
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
    // Cameras of the EZVIZ cloud account that are not the NVR itself (doorbells, ...)
    const byDevice = {};
    for (const e of registry) if (e.device_id && !e.disabled_by) (byDevice[e.device_id] ??= []).push(e);
    const wanted = this._config.cloud_cameras;
    this._cloud = registry
      .filter((e) => e.platform === "ezviz" && e.entity_id.startsWith("camera.") && !e.disabled_by)
      .filter((e) => {
        if (Array.isArray(wanted)) return wanted.includes(e.entity_id);
        const dev = this._hass.devices?.[e.device_id];
        const model = String(dev?.model || "");
        return !/^HWN|NVR/i.test(dev?.name || "") && !/^(UNKNOWN|HWN)/i.test(model);
      })
      .map((e) => ({
        key: `cloud:${e.entity_id}`,
        entity: e.entity_id,
        image: (byDevice[e.device_id] || []).find((x) => x.entity_id.startsWith("image."))?.entity_id || null,
      }));
    this._disks = disks;
    this._signature = "";
    this._render();
    this._loadPeople(true);
  }

  _cloudName(c) {
    return this._hass.states[c.entity]?.attributes.friendly_name || c.entity;
  }

  _sig() {
    if (!this._channels || !this._hass) return String(this._channels);
    return this._channels
      .flatMap((c) => [c.live, c.playback])
      .concat((this._cloud || []).flatMap((c) => [c.entity, c.image]))
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
    else if (this._view === "people") body = this._peopleView();
    else if (this._view === "stats") body = this._statsView();
    else if (this._view?.startsWith("person:")) body = this._personView(this._view.slice(7));
    else body = this._view ? this._playerView() : this._gridView();
    // Keep what is being typed (and the cursor) across re-renders
    const active = this.shadowRoot.activeElement;
    const typing = active?.matches?.("input[data-field]") ? { field: active.dataset.field, start: active.selectionStart, end: active.selectionEnd } : null;
    this.shadowRoot.innerHTML = `<style>${STYLES}</style><ha-card>${body}</ha-card>`;
    this.shadowRoot.querySelectorAll("input[data-field]").forEach((input) => {
      const draft = this._drafts?.[input.dataset.field];
      if (draft != null) input.value = draft;
      if (typing && typing.field === input.dataset.field) {
        input.focus();
        try {
          input.setSelectionRange(typing.start, typing.end);
        } catch (err) {
          /* not a text input */
        }
      }
    });
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
    const cloudTiles = (this._cloud || [])
      .map((c) => {
        const st = this._hass.states[c.entity];
        const offline = !st || st.state === "unavailable";
        const pic = c.image ? this._hass.states[c.image]?.attributes.entity_picture : null;
        return `<button class="tile" data-open="${esc(c.key)}">
          ${pic ? `<img class="snap" src="${esc(pic)}" alt="">` : `<div class="snap off">${esc(this._t.no_picture)}</div>`}
          <span class="tag">${esc(this._t.cloud)}</span>
          <div class="tile-bar"><span class="dot ${offline ? "" : "on"}"></span>${esc(this._cloudName(c))}</div></button>`;
      })
      .join("");
    const people = this._people ? `<button class="back" data-people>👤 ${esc(this._t.people)}${this._people.people.length ? ` · ${this._people.people.length}` : ""}</button>` : "";
    return `<div class="head"><b>EZVIZ NVR</b>${people}${this._diskBar()}</div>
      <div class="grid" style="--cols:${cols}">${tiles}${cloudTiles}</div>`;
  }

  _playerView() {
    if (this._view.startsWith("cloud:")) return this._eventsView();
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

  _eventsView() {
    const t = this._t;
    const cam = (this._cloud || []).find((c) => c.key === this._view);
    if (!cam) return this._gridView();
    const key = `${cam.entity}:${isoDay(this._day)}`;
    const data = this._events?.[key];
    if (!data) this._loadEvents(cam);
    const today = isoDay(this._day) === isoDay(new Date());
    let body = `<div class="hint">${esc(t.loading)}</div>`;
    if (data?.error) body = `<div class="hint">${esc(data.error)}</div>`;
    else if (data && !data.events.length) body = `<div class="hint">${esc(t.no_events)}</div>`;
    else if (data)
      body = `<div class="events">${data.events
        .map((e, i) => {
          const when = e.time ? new Date(e.time) : null;
          return `<button class="event" data-event="${i}">
            ${e.signed ? `<img src="${esc(e.signed)}" alt="" loading="lazy">` : `<div class="snap off">${esc(t.no_picture)}</div>`}
            <div class="event-bar"><b>${when && !isNaN(when) ? hhmmss(when) : ""}</b><span>${esc(e.title || "")}</span></div></button>`;
        })
        .join("")}</div>`;
    const zoom = this._zoomEvent != null && data?.events?.[this._zoomEvent];
    return `
      <div class="head"><button class="back" data-back>‹ ${esc(t.back)}</button><b>${esc(this._cloudName(cam))}</b>
        <span class="tag inline">${esc(t.events)}</span></div>
      <div class="timeline-head"><button data-day="-1">◀</button><span>${esc(isoDay(this._day))}</span><button data-day="1" ${today ? "disabled" : ""}>▶</button>
        <span class="spacer"></span><span class="hint">${data?.events ? data.events.length : ""}</span></div>
      ${body}
      ${zoom && zoom.signed ? `<div class="overlay" data-close><img src="${esc(zoom.signed)}" alt=""><div>${esc(zoom.title || "")} · ${zoom.time ? hhmmss(new Date(zoom.time)) : ""}</div></div>` : ""}`;
  }

  /* ----- people (face recognition) ----- */

  _personLabel(p) {
    if (p.name) return p.name;
    return `${this._t.unknown} ${p.label}`;
  }

  _camName(entity) {
    return this._hass.states[entity]?.attributes.friendly_name || entity;
  }

  async _loadPeople(quiet) {
    try {
      this._people = await this._hass.callWS({ type: "ezviz_nvr/faces/people" });
    } catch (err) {
      this._people = null; // face recognition is off
      if (!quiet) this._peopleError = err.message || String(err);
    }
    this._render();
  }

  async _loadPerson(pid) {
    if (this._loadingPerson === pid) return;
    this._loadingPerson = pid;
    try {
      const res = await this._hass.callWS({ type: "ezviz_nvr/faces/person", person_id: pid });
      (this._person ??= {})[pid] = res.occurrences;
    } catch (err) {
      (this._person ??= {})[pid] = { error: err.message || String(err) };
    }
    this._loadingPerson = null;
    this._render();
  }

  _peopleView() {
    const t = this._t;
    const data = this._people;
    const st = data?.status;
    const filter = this._peopleFilter || "all";
    const list = (data?.people || []).filter((p) => filter === "all" || p.name);
    const body = !data
      ? `<div class="hint">${esc(this._peopleError || t.loading)}</div>`
      : !list.length
        ? `<div class="hint">${esc(t.no_people)}</div>`
        : `<div class="people">${list
            .map(
              (p) => `<button class="person ${p.name ? "named" : ""}" data-person="${esc(p.id)}">
                ${p.thumbnail ? `<img src="${p.thumbnail}" alt="">` : `<div class="snap off"></div>`}
                <div class="person-name">${esc(this._personLabel(p))}</div>
                <div class="person-meta">${p.count} ${esc(t.seen)} · ${esc(when(p.last))}</div></button>`
            )
            .join("")}</div>`;
    const seg = (f) => `<button data-filter="${f}" class="${filter === f ? "on" : ""}">${esc(t[f])}</button>`;
    return `
      <div class="head"><button class="back" data-back>‹ ${esc(t.back)}</button><b>${esc(t.people)}</b>
        <div class="seg">${seg("all")}${seg("named")}</div></div>
      <div class="timeline-head"><span class="hint">${st ? `${esc(t.last_scan)}: ${esc(when(st.last_scan))}${st.error ? ` · ⚠ ${esc(st.error)}` : ""}` : ""}</span>
        <span class="spacer"></span><button data-stats-view>📊 ${esc(t.stats)}</button><button data-scan ${this._scanning || st?.scanning ? "disabled" : ""}>${esc(this._scanning || st?.scanning ? t.scanning : t.scan)}</button></div>
      ${body}`;
  }

  _personView(pid) {
    const t = this._t;
    const person = this._people?.people.find((p) => p.id === pid);
    if (!person) return this._peopleView();
    const rows = this._person?.[pid];
    if (!rows) this._loadPerson(pid);
    let body = `<div class="hint">${esc(t.loading)}</div>`;
    if (rows?.error) body = `<div class="hint">${esc(rows.error)}</div>`;
    else if (rows)
      body = `<div class="events">${rows
        .map(
          (r, i) => `<button class="event occ" data-occ="${i}">
            ${r.thumbnail ? `<img src="${r.thumbnail}" alt="">` : `<div class="snap off"></div>`}
            <div class="event-bar"><b>${esc(when(r.time))}</b><span>${esc(this._camName(r.camera))}${r.others?.length ? ` · ${esc(t.with)} ${esc(r.others.join(", "))}` : ""}</span></div></button>`
        )
        .join("")}</div>`;
    const names = [...new Set((this._people?.people || []).map((p) => p.name).filter(Boolean))];
    const occ = this._zoomOcc != null && Array.isArray(rows) ? rows[this._zoomOcc] : null;
    const overlay = occ
      ? `<div class="overlay" data-close>
          <div class="pic" data-stop>${occ.signed ? `<img src="${esc(occ.signed)}" alt="" data-pic><i class="box" hidden></i>` : `<div class="hint">${esc(t.loading)}</div>`}</div>
          <div class="occ-info" data-stop>${esc(when(occ.time))} · ${esc(this._camName(occ.camera))}${occ.title ? ` · ${esc(occ.title)}` : ""}</div>
          <div class="occ-actions" data-stop>
            <span>${esc(t.not_this)}</span>
            <input list="ezviz-names" data-assign-name data-field="assign:${esc(occ.face_id)}" autocomplete="off" placeholder="${esc(t.name_placeholder)}">
            <button data-assign>${esc(t.move)}</button>
            <button class="danger" data-remove-face>${esc(t.remove_face)}</button>
          </div>
        </div>`
      : "";
    return `
      <div class="head"><button class="back" data-people>‹ ${esc(t.people)}</button>
        ${person.thumbnail ? `<img class="avatar" src="${person.thumbnail}" alt="">` : ""}
        <input class="name" data-name data-field="name:${esc(pid)}" autocomplete="off" value="${esc(person.name)}" placeholder="${esc(this._personLabel(person))}">
        <button class="save" data-save>${esc(t.save)}</button>
        <span class="spacer"></span>
        <button class="back danger" data-delete-person>${esc(t.delete_person)}</button></div>
      <div class="hint">${person.count} ${esc(t.seen)} · ${esc(t.last_seen)}: ${esc(when(person.last))}${this._notice ? ` · ${esc(this._notice)}` : ""}</div>
      ${this._personStats(pid)}
      ${body}
      <datalist id="ezviz-names">${names.map((n) => `<option value="${esc(n)}">`).join("")}</datalist>
      ${overlay}`;
  }

  /* ----- statistics ----- */

  get _period() {
    return this._statsPeriod || "day";
  }

  _periodCount(period) {
    return { day: 30, week: 12, month: 12 }[period];
  }

  async _loadStats(key, msg) {
    if (this._loadingStats?.[key]) return;
    (this._loadingStats ??= {})[key] = true;
    try {
      (this._stats ??= {})[key] = await this._hass.callWS({ type: "ezviz_nvr/faces/stats", ...msg });
    } catch (err) {
      (this._stats ??= {})[key] = { error: err.message || String(err) };
    }
    this._loadingStats[key] = false;
    this._render();
  }

  _stat(key, msg) {
    const data = this._stats?.[key];
    if (!data) this._loadStats(key, msg);
    return data;
  }

  _bucketLabel(iso, period, long) {
    const d = new Date(`${iso}T00:00:00`);
    const lang = this._hass?.locale?.language || "en";
    if (period === "month") return d.toLocaleDateString(lang, long ? { month: "long", year: "numeric" } : { month: "short" });
    return long ? d.toLocaleDateString(lang, { day: "numeric", month: "short", year: "numeric" }) : `${pad(d.getDate())}/${pad(d.getMonth() + 1)}`;
  }

  /** Bars of visits per bucket (inline SVG, scales with the card). */
  _bars(values, buckets, period, events, big) {
    const max = Math.max(1, ...values);
    const n = values.length;
    const w = 10;
    const h = big ? 60 : 22;
    const bars = values
      .map((v, i) => {
        const bh = v ? Math.max(2, (v / max) * (h - 2)) : 0;
        const title = `${this._bucketLabel(buckets[i], period, true)}: ${v} ${this._t.seen}${events ? ` · ${events[i]} ${this._t.events_word}` : ""}`;
        return `<g><title>${esc(title)}</title><rect x="${i * w}" y="0" width="${w}" height="${h}" fill="transparent"></rect>
          <rect x="${i * w + 1.5}" y="${h - bh}" width="${w - 3}" height="${bh}" rx="1.5" class="${i === n - 1 ? "bar now" : "bar"}"></rect></g>`;
      })
      .join("");
    return `<svg class="bars ${big ? "big" : ""}" viewBox="0 0 ${n * w} ${h}" preserveAspectRatio="none">${bars}</svg>`;
  }

  _periodSeg() {
    const t = this._t;
    const seg = (p) => `<button data-period="${p}" class="${this._period === p ? "on" : ""}">${esc(t[p])}</button>`;
    return `<div class="seg">${seg("day")}${seg("week")}${seg("month")}</div>`;
  }

  _personStats(pid) {
    const t = this._t;
    const period = this._period;
    const data = this._stat(`${pid}:${period}`, { period, count: this._periodCount(period), person_id: pid });
    const row = data?.people?.[0];
    if (!row) return data?.error ? `<div class="hint">${esc(data.error)}</div>` : "";
    const total = row.visits.reduce((a, b) => a + b, 0);
    const current = row.visits[row.visits.length - 1];
    const nowLabel = { day: t.this_day, week: t.this_week, month: t.this_month }[period];
    const per = { day: t.per_day, week: t.per_week, month: t.per_month }[period];
    const avg = (total / row.visits.length).toLocaleString(this._hass.locale?.language || "en", { maximumFractionDigits: 1 });
    const b = data.buckets;
    return `<div class="stats-box">
      <div class="stats-head"><span><b>${current}</b> ${esc(nowLabel)} · <b>${total}</b> ${esc(t.in_range.toLowerCase())} · ${esc(t.average)} <b>${avg}</b> ${esc(per)}</span>
        <span class="spacer"></span>${this._periodSeg()}</div>
      ${this._bars(row.visits, b, period, row.events, true)}
      <div class="axis"><span>${esc(this._bucketLabel(b[0], period))}</span><span>${esc(this._bucketLabel(b[Math.floor(b.length / 2)], period))}</span><span>${esc(this._bucketLabel(b[b.length - 1], period))}</span></div>
    </div>`;
  }

  _statsView() {
    const t = this._t;
    const period = this._period;
    const data = this._stat(`all:${period}`, { period, count: this._periodCount(period) });
    const thumbs = Object.fromEntries((this._people?.people || []).map((p) => [p.id, p]));
    const filter = this._peopleFilter || "all";
    const rows = (data?.people || []).filter((r) => r.visits.some(Boolean) && (filter === "all" || r.name));
    const nowLabel = { day: t.this_day, week: t.this_week, month: t.this_month }[period];
    let body = `<div class="hint">${esc(data?.error || t.loading)}</div>`;
    if (data?.people)
      body = rows.length
        ? `<div class="stats-table">
            <div class="stats-row th"><span></span><span></span><span>${esc(this._bucketLabel(data.buckets[0], period))} → ${esc(nowLabel)}</span><span>${esc(nowLabel)}</span><span>${esc(t.in_range)}</span></div>
            ${rows
              .map((r) => {
                const p = thumbs[r.id];
                const label = r.name || `${t.unknown} ${r.label}`;
                return `<button class="stats-row" data-person="${esc(r.id)}">
                  ${p?.thumbnail ? `<img src="${p.thumbnail}" alt="">` : `<span class="noimg"></span>`}
                  <span class="nm ${r.name ? "named" : ""}">${esc(label)}</span>
                  ${this._bars(r.visits, data.buckets, period, r.events, false)}
                  <b>${r.visits[r.visits.length - 1]}</b><b>${r.visits.reduce((a, b) => a + b, 0)}</b></button>`;
              })
              .join("")}</div>`
        : `<div class="hint">${esc(t.no_stats)}</div>`;
    const seg = (f) => `<button data-filter="${f}" class="${filter === f ? "on" : ""}">${esc(t[f])}</button>`;
    return `
      <div class="head"><button class="back" data-people>‹ ${esc(t.people)}</button><b>📊 ${esc(t.stats)}</b>
        <span class="spacer"></span>${this._periodSeg()}<div class="seg">${seg("all")}${seg("named")}</div></div>
      ${body}`;
  }

  async _openOcc(pid, i) {
    this._zoomOcc = i;
    const occ = this._person?.[pid]?.[i];
    this._render();
    if (occ && !occ.signed) {
      try {
        occ.signed = (await this._hass.callWS({ type: "auth/sign_path", path: occ.picture, expires: 3600 })).path;
      } catch (err) {
        occ.signed = null;
      }
      if (this._zoomOcc === i) this._render();
    }
  }

  async _peopleAction(msg, after) {
    try {
      const res = await this._hass.callWS(msg);
      this._drafts = {};
      this._person = {};
      this._stats = {};
      await this._loadPeople();
      if (after) after(res);
    } catch (err) {
      this._notice = err.message || String(err);
    }
    this._render();
  }

  _bindPeople() {
    const root = this.shadowRoot;
    const pid = this._view?.startsWith("person:") ? this._view.slice(7) : null;
    root.querySelectorAll("[data-people]").forEach((b) => (b.onclick = () => ((this._view = "people"), (this._zoomOcc = null), (this._notice = null), (this._stats = {}), this._loadPeople())));
    root.querySelectorAll("[data-person]").forEach((b) => (b.onclick = () => ((this._view = `person:${b.dataset.person}`), (this._zoomOcc = null), (this._notice = null), this._render())));
    root.querySelectorAll("[data-filter]").forEach((b) => (b.onclick = () => ((this._peopleFilter = b.dataset.filter), this._render())));
    root.querySelectorAll("[data-period]").forEach((b) => (b.onclick = () => ((this._statsPeriod = b.dataset.period), this._render())));
    const statsView = root.querySelector("[data-stats-view]");
    if (statsView) statsView.onclick = () => ((this._view = "stats"), (this._stats = {}), this._render());
    const scan = root.querySelector("[data-scan]");
    if (scan)
      scan.onclick = async () => {
        this._scanning = true;
        this._render();
        try {
          await this._hass.callWS({ type: "ezviz_nvr/faces/scan", days: 2 });
        } catch (err) {
          this._peopleError = err.message || String(err);
        }
        this._scanning = false;
        this._person = {};
        this._stats = {};
        this._loadPeople();
      };
    // Typing goes to the inputs only: Home Assistant's keyboard shortcuts must not see the keys
    root.querySelectorAll("input[data-field]").forEach((input) => {
      for (const type of ["keydown", "keyup", "keypress"]) input.addEventListener(type, (ev) => ev.stopPropagation());
      input.addEventListener("input", () => ((this._drafts ??= {})[input.dataset.field] = input.value));
      input.addEventListener("click", (ev) => ev.stopPropagation());
    });
    if (!pid) return;
    const name = root.querySelector("[data-name]");
    const save = () => {
      const before = this._people?.people.find((p) => p.id === pid)?.name || "";
      if (name.value.trim() === before) return;
      this._peopleAction({ type: "ezviz_nvr/faces/rename", person_id: pid, name: name.value }, (res) => {
        this._notice = res.person_id !== pid ? this._t.merged : null;
        this._view = `person:${res.person_id}`;
      });
    };
    root.querySelector("[data-save]").onclick = save;
    name.onkeydown = (ev) => {
      if (ev.key === "Enter") save();
    };
    root.querySelector("[data-delete-person]").onclick = () => {
      if (!confirm(this._t.confirm_delete)) return;
      this._peopleAction({ type: "ezviz_nvr/faces/delete", person_id: pid }, () => (this._view = "people"));
    };
    root.querySelectorAll("[data-occ]").forEach((b) => (b.onclick = () => this._openOcc(pid, Number(b.dataset.occ))));
    const overlay = root.querySelector("[data-close]");
    if (overlay) {
      overlay.onclick = (ev) => {
        if (ev.target.closest("[data-stop]")) return;
        this._zoomOcc = null;
        this._render();
      };
      const occ = this._person?.[pid]?.[this._zoomOcc];
      const img = root.querySelector("[data-pic]");
      const box = root.querySelector(".box");
      if (img && box && occ?.box) {
        const place = () => {
          const [x, y, w, h] = occ.box;
          const W = img.naturalWidth, H = img.naturalHeight;
          if (!W || !H) return;
          Object.assign(box.style, { left: `${(x / W) * 100}%`, top: `${(y / H) * 100}%`, width: `${(w / W) * 100}%`, height: `${(h / H) * 100}%` });
          box.hidden = false;
        };
        img.complete ? place() : (img.onload = place);
      }
      const input = root.querySelector("[data-assign-name]");
      const assign = () => {
        if (!input.value.trim()) return;
        this._peopleAction({ type: "ezviz_nvr/faces/assign", face_id: occ.face_id, name: input.value }, () => (this._zoomOcc = null));
      };
      root.querySelector("[data-assign]").onclick = assign;
      input.onkeydown = (ev) => {
        if (ev.key === "Enter") assign();
      };
      root.querySelector("[data-remove-face]").onclick = () =>
        this._peopleAction({ type: "ezviz_nvr/faces/delete", face_id: occ.face_id }, () => (this._zoomOcc = null));
    }
  }

  async _loadEvents(cam) {
    const key = `${cam.entity}:${isoDay(this._day)}`;
    if (this._loadingEvents === key) return;
    this._loadingEvents = key;
    this._events ??= {};
    try {
      const res = await this._hass.callService(DOMAIN, "get_events", { entity_id: cam.entity, date: isoDay(this._day), limit: 100 }, undefined, false, true);
      const events = res?.response?.events || [];
      // Pictures go through Home Assistant: sign their URLs so <img> can load them
      await Promise.all(
        events.map(async (e) => {
          if (!e.picture) return;
          try {
            e.signed = (await this._hass.callWS({ type: "auth/sign_path", path: e.picture, expires: 3600 })).path;
          } catch (err) {
            e.signed = null;
          }
        })
      );
      this._events[key] = { events };
    } catch (err) {
      this._events[key] = { events: [], error: err.message || String(err) };
    }
    this._loadingEvents = null;
    this._render();
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
    if (!slot || this._view?.startsWith("cloud:")) return;
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
    this._bindPeople();
    root.querySelectorAll("[data-open]").forEach(
      (b) => (b.onclick = () => ((this._view = b.dataset.open), (this._mode = "live"), (this._player = null), (this._day = new Date()), (this._zoomEvent = null), this._render()))
    );
    const back = root.querySelector("[data-back]");
    if (back) back.onclick = () => ((this._view = null), (this._player = null), (this._signature = ""), this._render());
    root.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => ((this._mode = b.dataset.mode), (this._day = new Date()), this._render())));
    root.querySelectorAll("[data-day]").forEach(
      (b) =>
        (b.onclick = () => {
          const d = new Date(this._day);
          d.setDate(d.getDate() + Number(b.dataset.day));
          this._day = d > new Date() ? new Date() : d;
          this._zoomEvent = null;
          this._render();
        })
    );
    root.querySelectorAll("[data-event]").forEach((b) => (b.onclick = () => ((this._zoomEvent = Number(b.dataset.event)), this._render())));
    const overlay = root.querySelector("[data-close]");
    if (overlay && !this._isPeopleView()) overlay.onclick = () => ((this._zoomEvent = null), this._render());
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
  /* Up to --cols columns, each at least ~260px wide: tiles wrap instead of shrinking */
  .grid { display:grid; gap:10px; grid-template-columns:repeat(auto-fill, minmax(max(260px, calc((100% - (var(--cols) - 1) * 10px) / var(--cols))), 1fr)); }
  @media (max-width: 600px) { .grid { grid-template-columns:1fr; } ha-card { padding:10px; } .head { gap:8px; } .disk { margin-left:0; width:100%; } }
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
  .tag { position:absolute; top:8px; right:8px; background:rgba(61,139,253,.85); color:#fff; font-size:11px; padding:2px 7px; border-radius:6px; }
  .tag.inline { position:static; margin-left:auto; }
  .events { display:grid; gap:8px; grid-template-columns:repeat(auto-fill, minmax(170px, 1fr)); margin-top:6px; }
  .event { position:relative; padding:0; border:none; border-radius:10px; overflow:hidden; background:var(--panel); cursor:pointer; color:var(--text); font-family:inherit; aspect-ratio:16/10; }
  .event img { width:100%; height:100%; object-fit:cover; display:block; }
  .event-bar { position:absolute; left:0; right:0; bottom:0; display:flex; gap:6px; align-items:baseline; padding:5px 8px; font-size:12px; text-align:left;
    background:linear-gradient(transparent, rgba(0,0,0,.8)); white-space:nowrap; overflow:hidden; }
  .event-bar span { color:#cfd5e2; overflow:hidden; text-overflow:ellipsis; }
  .overlay { position:fixed; inset:0; z-index:10; background:rgba(0,0,0,.88); display:flex; flex-direction:column; align-items:center; justify-content:center; gap:10px; cursor:zoom-out; color:#fff; font-size:14px; }
  .overlay img { max-width:96vw; max-height:84vh; border-radius:8px; }
  .people { display:grid; gap:10px; grid-template-columns:repeat(auto-fill, minmax(130px, 1fr)); margin-top:8px; }
  .person { padding:8px; border:none; border-radius:12px; background:var(--panel); color:var(--text); cursor:pointer; font-family:inherit; text-align:center; }
  .person img, .person .snap { width:100%; aspect-ratio:1; object-fit:cover; border-radius:10px; display:block; }
  .person.named { box-shadow:inset 0 0 0 2px var(--accent); }
  .person-name { margin-top:6px; font-size:14px; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .person-meta { font-size:11px; color:var(--muted); margin-top:2px; }
  .occ { aspect-ratio:1; }
  .avatar { width:40px; height:40px; border-radius:50%; object-fit:cover; }
  input.name, .occ-actions input { background:var(--panel2); color:var(--text); border:1px solid #3a4359; border-radius:10px; padding:7px 10px; font-family:inherit; font-size:15px; min-width:0; }
  input.name { flex:1 1 160px; max-width:260px; font-weight:600; }
  .save, .occ-actions button { background:var(--accent); color:#06281d; border:none; border-radius:10px; padding:7px 14px; cursor:pointer; font-family:inherit; font-size:14px; font-weight:600; }
  .danger { background:#5a2a2e !important; color:#ffd7d9 !important; }
  .pic { position:relative; display:inline-block; cursor:default; overflow:hidden; border-radius:8px; }
  .pic img { display:block; }
  .box { position:absolute; border:2px solid #ffcc4d; border-radius:6px; box-shadow:0 0 0 2000px rgba(0,0,0,.15); pointer-events:none; }
  .occ-info { color:#cfd5e2; }
  .stats-box { background:var(--panel); border-radius:12px; padding:10px 12px; margin:10px 0; }
  .stats-head { display:flex; align-items:center; gap:8px; flex-wrap:wrap; font-size:13px; color:var(--muted); margin-bottom:8px; }
  .stats-head b { color:var(--text); font-size:15px; }
  .bars { width:100%; display:block; height:22px; }
  .bars.big { height:90px; }
  .bar { fill:#3d8bfd; opacity:.75; } .bar.now { fill:var(--accent); opacity:1; }
  .axis { display:flex; justify-content:space-between; font-size:11px; color:var(--muted); margin-top:4px; }
  .stats-table { display:flex; flex-direction:column; gap:6px; margin-top:8px; }
  .stats-row { display:grid; grid-template-columns:36px minmax(90px,160px) 1fr 62px 72px; gap:10px; align-items:center; background:var(--panel); border:none;
    border-radius:10px; padding:6px 10px; color:var(--text); font-family:inherit; font-size:14px; text-align:left; cursor:pointer; }
  .stats-row.th { background:none; cursor:default; font-size:11px; color:var(--muted); padding-top:0; padding-bottom:0; }
  .stats-row img, .stats-row .noimg { width:36px; height:36px; border-radius:50%; object-fit:cover; background:var(--panel2); }
  .stats-row .nm { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; } .stats-row .nm.named { font-weight:600; }
  .stats-row b { text-align:right; }
  @media (max-width: 600px) { .stats-row { grid-template-columns:30px minmax(70px,1fr) 1fr 36px 40px; gap:6px; } .stats-row img, .stats-row .noimg { width:30px; height:30px; } }
  .occ-actions { display:flex; flex-wrap:wrap; gap:8px; align-items:center; justify-content:center; cursor:default; padding:0 10px; }
`;

customElements.define("ezviz-nvr-card", EzvizNvrCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "ezviz-nvr-card", name: "EZVIZ NVR", description: "Cameras, live view and recordings of an EZVIZ / Hikvision NVR", preview: false, documentationURL: "https://github.com/dmdias/ha-ezviz-nvr" });
console.info(`%c EZVIZ-NVR-CARD %c ${CARD_VERSION} `, "background:#3d8bfd;color:#fff", "background:#1b2130;color:#fff");
