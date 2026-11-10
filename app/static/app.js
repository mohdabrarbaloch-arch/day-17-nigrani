/* Nigrani dashboard — vanilla JS, no build step.
   Everything the UI shows comes from the API. Nothing is computed here that
   the server already decided, so the screen and the engine can never disagree. */

(() => {
  "use strict";

  const TOKEN_KEY = "nigrani.token";
  const state = { token: null, fleet: null, alerts: [], selected: null };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  /* ── tiny helpers ─────────────────────────────────────────── */

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    for (const child of [].concat(children)) {
      if (child === null || child === undefined) continue;
      node.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function toast(message) {
    const node = $("#toast");
    node.textContent = message;
    node.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { node.hidden = true; }, 2600);
  }

  function sevOf(health) {
    if (health < 30) return "emergency";
    if (health < 50) return "critical";
    if (health < 70) return "warning";
    return "healthy";
  }

  function fmt(n, digits = 1) {
    if (n === null || n === undefined) return "—";
    return Number(n).toFixed(digits);
  }

  function hoursLabel(h) {
    if (h === null || h === undefined) return "unknown";
    if (h <= 0) return "now";
    if (h < 48) return `${fmt(h, 1)} h`;
    return `${fmt(h / 24, 1)} days`;
  }

  function timeAgo(iso) {
    const then = new Date(iso).getTime();
    const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.round(hrs / 24)}d ago`;
  }

  /* ── API ──────────────────────────────────────────────────── */

  async function api(path, options = {}) {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (state.token) headers.Authorization = `Bearer ${state.token}`;

    const res = await fetch(path, { ...options, headers });
    if (res.status === 204) return null;

    const text = await res.text();
    let body = null;
    try { body = text ? JSON.parse(text) : null; } catch { body = { detail: text }; }

    if (!res.ok) {
      if (res.status === 401 && state.token) {
        signOut();
        throw new Error("Session expired — sign in again.");
      }
      const detail = body && body.detail;
      throw new Error(typeof detail === "string" ? detail : `Request failed (${res.status})`);
    }
    return body;
  }

  /* ── auth ─────────────────────────────────────────────────── */

  function setToken(token) {
    state.token = token;
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  }

  function signOut() {
    setToken(null);
    state.fleet = null;
    state.alerts = [];
    state.selected = null;
    $("#app").classList.add("is-hidden");
    $("#gate").classList.remove("is-hidden");
  }

  function showGateError(message) {
    const node = $("#gate-error");
    node.textContent = message;
    node.hidden = !message;
  }

  async function enterApp() {
    $("#gate").classList.add("is-hidden");
    $("#app").classList.remove("is-hidden");
    try {
      const me = await api("/api/auth/me");
      $("#whoami").textContent = `${me.display_name} · ${me.email}`;
    } catch {
      return;
    }
    await refresh();
  }

  /* ── rendering ────────────────────────────────────────────── */

  function renderKpis(fleet) {
    const root = $("#kpis");
    root.replaceChildren();

    const cards = [
      { label: "Machines", value: fleet.total, sub: "monitored", tone: "violet" },
      { label: "Healthy", value: fleet.healthy, sub: "no action", tone: "teal" },
      { label: "Warning", value: fleet.warning, sub: "plan this week", tone: "amber" },
      { label: "Critical", value: fleet.critical + fleet.emergency, sub: "act now", tone: "red" },
      { label: "Open alerts", value: fleet.open_alerts, sub: "unresolved", tone: fleet.open_alerts ? "red" : "teal" },
    ];

    for (const c of cards) {
      root.append(
        el("div", { class: "kpi", "data-tone": c.tone }, [
          el("div", { class: "kpi-label", text: c.label }),
          el("div", { class: "kpi-value", text: String(c.value) }),
          el("div", { class: "kpi-sub", text: c.sub }),
        ])
      );
    }
  }

  function renderFleet(fleet) {
    const root = $("#fleet");
    root.replaceChildren();

    if (!fleet.machines.length) {
      root.append(el("p", { class: "empty", text: "No machines yet. Add one below to start monitoring." }));
      return;
    }

    for (const m of fleet.machines) {
      const sev = m.severity || sevOf(m.health);
      const card = el("button", {
        class: "mcard",
        type: "button",
        "data-sev": sev,
        onclick: () => openDetail(m.machine_id),
      }, [
        el("div", { class: "mcard-top" }, [
          el("div", {}, [
            el("div", { class: "mcard-tag", text: m.asset_tag }),
            el("div", { class: "mcard-name", text: m.name }),
            el("div", { class: "mcard-line", text: m.line }),
          ]),
          el("span", { class: "pill", "data-sev": sev, text: sev }),
        ]),
        el("div", { class: "health-row" }, [
          el("span", { class: "health-num", text: fmt(m.health, 0) }),
          el("span", { class: "health-unit", text: "/ 100 health" }),
        ]),
        el("div", { class: "bar" }, [
          el("div", { class: "bar-fill", "data-sev": sev, style: `width:${Math.max(2, m.health)}%` }),
        ]),
        el("div", { class: "mcard-meta" }, [
          el("span", {}, ["RUL ", el("b", { text: hoursLabel(m.rul_hours) })]),
          el("span", {}, ["worst ", el("b", { text: m.worst_channel })]),
        ]),
      ]);
      root.append(card);
    }
  }

  function renderAlerts(alerts) {
    const root = $("#alerts");
    root.replaceChildren();

    if (!alerts.length) {
      root.append(el("p", { class: "empty", text: "No open alerts. Everything is inside its envelope." }));
      return;
    }

    for (const a of alerts) {
      root.append(
        el("div", { class: "alert", "data-sev": a.severity }, [
          el("div", { class: "alert-body" }, [
            el("div", { class: "alert-title", text: `${a.severity.toUpperCase()} · machine #${a.machine_id}` }),
            el("div", { class: "alert-reason", text: a.reason }),
            el("div", {
              class: "alert-meta",
              text: `health ${fmt(a.health_at_alert, 1)} · RUL ${hoursLabel(a.rul_hours)} · raised ${timeAgo(a.raised_at)}`,
            }),
          ]),
          el("button", {
            class: "btn btn-ghost btn-sm",
            type: "button",
            onclick: () => resolveAlert(a.id),
          }, "Resolve"),
        ])
      );
    }
  }

  /* ── detail view ──────────────────────────────────────────── */

  async function openDetail(machineId) {
    state.selected = machineId;
    const panel = $("#detail-panel");
    panel.hidden = false;
    $("#detail-title").textContent = "Loading…";
    $("#detail").replaceChildren();

    try {
      const [health, readings, forecast, machine] = await Promise.all([
        api(`/api/machines/${machineId}/health`),
        api(`/api/machines/${machineId}/readings?limit=60`),
        api(`/api/machines/${machineId}/forecast?hours=72`),
        api(`/api/machines/${machineId}`),
      ]);
      renderDetail(machine, health, readings, forecast);
      panel.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      $("#detail-title").textContent = "Machine";
      $("#detail").replaceChildren(el("p", { class: "error", text: err.message }));
    }
  }

  function renderDetail(machine, health, readings, forecast) {
    $("#detail-title").textContent = `${machine.asset_tag} — ${machine.name}`;
    const root = $("#detail");
    root.replaceChildren();

    const sev = health.severity || sevOf(health.health);

    /* headline stats */
    root.append(
      el("div", { class: "detail-grid" }, [
        stat("Health", fmt(health.health, 1), sev, "weighted, worst-channel pulled"),
        stat("Degradation", health.degradation_per_hour === null ? "—" : `${fmt(health.degradation_per_hour, 2)}/h`, null, "points lost per hour"),
        stat("RUL", hoursLabel(health.rul_hours), null, "to the 30-point floor"),
        stat("Worst channel", health.worst_channel, null, "lowest sub-score"),
        stat("Readings", String(health.reading_count), null, "in the trend window"),
        stat("Line", machine.line, null, machine.machine_type),
      ])
    );

    /* channel breakdown */
    if (health.channels.length) {
      const channels = el("div", { class: "channels" });
      for (const c of health.channels) {
        const pct = Math.round(c.score * 100);
        const tone = pct < 30 ? "emergency" : pct < 50 ? "critical" : pct < 70 ? "warning" : "healthy";
        channels.append(
          el("div", { class: "channel" }, [
            el("span", { class: "channel-name", text: c.name }),
            el("div", { class: "bar" }, [
              el("div", { class: "bar-fill", "data-sev": tone, style: `width:${Math.max(2, pct)}%` }),
            ]),
            el("span", { class: "channel-val", text: `${fmt(c.value, 1)} → ${pct}%` }),
          ])
        );
      }
      root.append(el("div", {}, [
        el("div", { class: "stat-label", text: "Channel breakdown", style: "margin-bottom:.45rem" }),
        channels,
      ]));
    }

    /* forecast chart */
    if (forecast.points && forecast.points.length) {
      root.append(chartBlock(forecast));
    }

    /* add reading */
    const form = el("form", { class: "reading-form" }, [
      el("label", {}, ["Vibration (mm/s)", el("input", { name: "vibration_rms", type: "number", step: "0.01", min: "0", required: "required", value: "2.0" })]),
      el("label", {}, ["Temp (°C)", el("input", { name: "temperature_c", type: "number", step: "0.1", required: "required", value: "45" })]),
      el("label", {}, ["Current (A)", el("input", { name: "current_a", type: "number", step: "0.01", min: "0", required: "required", value: "12" })]),
      el("button", { class: "btn btn-primary", type: "submit" }, "Add reading"),
    ]);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const data = new FormData(form);
      const payload = {
        vibration_rms: Number(data.get("vibration_rms")),
        temperature_c: Number(data.get("temperature_c")),
        current_a: Number(data.get("current_a")),
      };
      try {
        await api(`/api/machines/${machine.id}/readings`, { method: "POST", body: JSON.stringify(payload) });
        toast("Reading recorded");
        await refresh();
        await openDetail(machine.id);
      } catch (err) {
        toast(err.message);
      }
    });
    root.append(el("div", {}, [
      el("div", { class: "stat-label", text: "Record a reading", style: "margin-bottom:.45rem" }),
      form,
    ]));

    /* recent readings table */
    if (readings.length) {
      const rows = readings.slice(-8).reverse().map((r) =>
        el("tr", {}, [
          el("td", { text: new Date(r.recorded_at).toLocaleString() }),
          el("td", { text: fmt(r.vibration_rms, 2) }),
          el("td", { text: fmt(r.temperature_c, 1) }),
          el("td", { text: fmt(r.current_a, 2) }),
          el("td", { text: fmt(r.health, 1) }),
        ])
      );
      root.append(el("div", {}, [
        el("div", { class: "stat-label", text: "Last 8 readings", style: "margin-bottom:.45rem" }),
        el("div", { style: "overflow-x:auto" }, [
          el("table", { style: "width:100%;border-collapse:collapse;font-size:.78rem" }, [
            el("thead", {}, [el("tr", {}, ["When", "Vib", "Temp", "Amp", "Health"].map((h) =>
              el("th", { text: h, style: "text-align:left;padding:.35rem .5rem;color:var(--ink-3);font-weight:600;border-bottom:1px solid var(--line)" })
            ))]),
            el("tbody", {}, rows.map((tr) => {
              Array.from(tr.children).forEach((td) => {
                td.style.padding = ".35rem .5rem";
                td.style.borderBottom = "1px solid var(--line)";
                td.style.fontVariantNumeric = "tabular-nums";
              });
              return tr;
            })),
          ]),
        ]),
      ]));
    }
  }

  function stat(label, value, sev, note) {
    const valueNode = el("div", { class: "stat-value", text: value });
    if (sev) valueNode.style.color = `var(--${sev === "healthy" ? "teal" : sev === "warning" ? "amber" : "red"})`;
    return el("div", { class: "stat" }, [
      el("div", { class: "stat-label", text: label }),
      valueNode,
      el("div", { class: "stat-note", text: note }),
    ]);
  }

  function chartBlock(forecast) {
    const W = 640, H = 150, PAD = 26;
    const pts = forecast.points;
    const maxH = Math.max(...pts.map((p) => p.hours_ahead)) || 1;

    const x = (h) => PAD + (h / maxH) * (W - PAD * 2);
    const y = (v) => H - PAD + 6 - (v / 100) * (H - PAD * 2);

    const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.hours_ahead).toFixed(1)},${y(p.health).toFixed(1)}`).join(" ");
    const area = `${line} L${x(maxH).toFixed(1)},${(H - PAD + 6).toFixed(1)} L${PAD},${(H - PAD + 6).toFixed(1)} Z`;

    const svgNS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("class", "chart");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `Projected health over the next ${maxH} hours`);

    const defs = document.createElementNS(svgNS, "defs");
    defs.innerHTML = `<linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#2dd4bf" stop-opacity=".28"/>
      <stop offset="100%" stop-color="#2dd4bf" stop-opacity="0"/>
    </linearGradient>`;
    svg.append(defs);

    // failure threshold line
    const thr = document.createElementNS(svgNS, "line");
    thr.setAttribute("x1", PAD); thr.setAttribute("x2", W - PAD);
    thr.setAttribute("y1", y(30)); thr.setAttribute("y2", y(30));
    thr.setAttribute("stroke", "#f2555a"); thr.setAttribute("stroke-dasharray", "4 4");
    thr.setAttribute("stroke-opacity", ".55");
    svg.append(thr);

    const path = document.createElementNS(svgNS, "path");
    path.setAttribute("d", area); path.setAttribute("fill", "url(#g)");
    svg.append(path);

    const stroke = document.createElementNS(svgNS, "path");
    stroke.setAttribute("d", line);
    stroke.setAttribute("fill", "none");
    stroke.setAttribute("stroke", "#2dd4bf");
    stroke.setAttribute("stroke-width", "2");
    stroke.setAttribute("stroke-linejoin", "round");
    svg.append(stroke);

    return el("div", { class: "chart-wrap" }, [
      el("div", { class: "stat-label", text: "Projected health — next 72 hours" }),
      svg,
      el("div", { class: "chart-note", text: forecast.note }),
    ]);
  }

  /* ── actions ──────────────────────────────────────────────── */

  async function refresh() {
    try {
      const [fleet, alerts] = await Promise.all([api("/api/fleet"), api("/api/alerts?open_only=true")]);
      state.fleet = fleet;
      state.alerts = alerts;
      renderKpis(fleet);
      renderFleet(fleet);
      renderAlerts(alerts);
    } catch (err) {
      toast(err.message);
    }
  }

  async function resolveAlert(id) {
    const note = window.prompt("What did you do about it?", "Inspected and re-torqued the bearing housing.");
    if (!note) return;
    try {
      await api(`/api/alerts/${id}/resolve`, { method: "POST", body: JSON.stringify({ note }) });
      toast("Alert resolved");
      await refresh();
    } catch (err) {
      toast(err.message);
    }
  }

  async function simulate() {
    const btn = $("#seed-btn");
    btn.disabled = true;
    btn.textContent = "Simulating…";
    try {
      const result = await api("/api/simulate", { method: "POST", body: JSON.stringify({ hours: 24 }) });
      toast(`Added ${result.readings_created} readings across ${result.machines} machines`);
      await refresh();
      if (state.selected) await openDetail(state.selected);
    } catch (err) {
      toast(err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "Simulate 24h";
    }
  }

  /* ── wiring ───────────────────────────────────────────────── */

  function wire() {
    $$(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        $$(".tab").forEach((t) => {
          t.classList.toggle("is-active", t === tab);
          t.setAttribute("aria-selected", String(t === tab));
        });
        const which = tab.dataset.tab;
        $("#login-form").classList.toggle("is-hidden", which !== "login");
        $("#register-form").classList.toggle("is-hidden", which !== "register");
        showGateError("");
      });
    });

    $("#login-form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      showGateError("");
      const data = new FormData(ev.target);
      try {
        const res = await api("/api/auth/login", {
          method: "POST",
          body: JSON.stringify({ email: data.get("email"), password: data.get("password") }),
        });
        setToken(res.access_token);
        await enterApp();
      } catch (err) {
        showGateError(err.message);
      }
    });

    $("#register-form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      showGateError("");
      const data = new FormData(ev.target);
      try {
        const res = await api("/api/auth/register", {
          method: "POST",
          body: JSON.stringify({
            email: data.get("email"),
            password: data.get("password"),
            display_name: data.get("display_name"),
          }),
        });
        setToken(res.access_token);
        await enterApp();
        // First-run convenience: seed a demo fleet so the dashboard is not empty.
        try {
          await api("/api/seed-demo", { method: "POST", body: JSON.stringify({}) });
          await refresh();
        } catch { /* seeding is best-effort */ }
      } catch (err) {
        showGateError(err.message);
      }
    });

    $("#logout-btn").addEventListener("click", signOut);
    $("#seed-btn").addEventListener("click", simulate);
    $("#close-detail").addEventListener("click", () => {
      $("#detail-panel").hidden = true;
      state.selected = null;
    });

    $("#machine-form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const errNode = $("#machine-error");
      errNode.hidden = true;
      const data = new FormData(ev.target);
      const payload = {};
      for (const [k, v] of data.entries()) {
        payload[k] = ["nominal_vibration", "critical_vibration", "nominal_temperature",
          "critical_temperature", "nominal_current", "critical_current"].includes(k)
          ? Number(v) : v;
      }
      try {
        await api("/api/machines", { method: "POST", body: JSON.stringify(payload) });
        ev.target.reset();
        toast("Machine added");
        await refresh();
      } catch (err) {
        errNode.textContent = err.message;
        errNode.hidden = false;
      }
    });
  }

  async function boot() {
    wire();
    const saved = localStorage.getItem(TOKEN_KEY);
    if (saved) {
      state.token = saved;
      await enterApp();
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
