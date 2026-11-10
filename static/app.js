/* ───────────────────────────────────────────────────────────────────────────
   Nigrani SPA — vanilla JS, no build step.
   ─────────────────────────────────────────────────────────────────────────── */
"use strict";

const API = "";
const TOKEN_KEY = "nigrani_token";
const USER_KEY = "nigrani_user";

const state = {
  token: localStorage.getItem(TOKEN_KEY) || null,
  user: JSON.parse(localStorage.getItem(USER_KEY) || "null"),
  machines: [],
  plants: [],
  selectedMachine: null,
};

/* ── Helpers ──────────────────────────────────────────────────────────── */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function esc(str) {
  return String(str ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function toast(msg, isError = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("error", isError);
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), 3200);
}

function timeAgo(iso) {
  const then = new Date(iso).getTime();
  const diff = Math.max(0, Date.now() - then);
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function healthClass(h) {
  if (h === null || h === undefined) return "badge-muted";
  if (h < 40) return "badge-crit";
  if (h < 65) return "badge-warn";
  return "badge-ok";
}

function healthColor(h) {
  if (h === null || h === undefined) return "#5c6b80";
  if (h < 40) return "#f87171";
  if (h < 65) return "#fbbf24";
  return "#34d399";
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;

  const res = await fetch(API + path, { ...options, headers });
  if (res.status === 401) {
    logout();
    throw new Error("Session expired — please sign in again.");
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d) => d.msg).join(", ");
    } catch { /* keep default */ }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

/* ── Auth ─────────────────────────────────────────────────────────────── */
function showApp() {
  $("#auth-screen").classList.add("is-hidden");
  $("#app").classList.remove("is-hidden");
  $("#user-name").textContent = state.user ? state.user.full_name : "";
  loadAll();
}

function logout() {
  state.token = null;
  state.user = null;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  $("#app").classList.add("is-hidden");
  $("#auth-screen").classList.remove("is-hidden");
}

function persistAuth(data) {
  state.token = data.access_token;
  state.user = data.user;
  localStorage.setItem(TOKEN_KEY, data.access_token);
  localStorage.setItem(USER_KEY, JSON.stringify(data.user));
}

$$(".auth-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".auth-tab").forEach((t) => t.classList.remove("is-active"));
    tab.classList.add("is-active");
    const isLogin = tab.dataset.tab === "login";
    $("#login-form").classList.toggle("is-hidden", !isLogin);
    $("#register-form").classList.toggle("is-hidden", isLogin);
    $("#auth-error").textContent = "";
  });
});

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  $("#auth-error").textContent = "";
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: fd.get("email"), password: fd.get("password") }),
    });
    persistAuth(data);
    showApp();
  } catch (err) {
    $("#auth-error").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});

$("#register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  $("#auth-error").textContent = "";
  try {
    const data = await api("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({
        email: fd.get("email"),
        full_name: fd.get("full_name"),
        password: fd.get("password"),
      }),
    });
    persistAuth(data);
    showApp();
  } catch (err) {
    $("#auth-error").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});

$("#logout-btn").addEventListener("click", logout);

/* ── Tabs ─────────────────────────────────────────────────────────────── */
$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.remove("is-active"));
    tab.classList.add("is-active");
    $$(".view").forEach((v) => v.classList.add("is-hidden"));
    $(`#view-${tab.dataset.view}`).classList.remove("is-hidden");
    if (tab.dataset.view === "alerts") loadAlerts();
    if (tab.dataset.view === "orders") loadOrders();
    if (tab.dataset.view === "automation") loadAutomation();
    if (tab.dataset.view === "machines") loadMachines();
  });
});

/* ── Dashboard ────────────────────────────────────────────────────────── */
async function loadDashboard() {
  try {
    const s = await api("/api/dashboard/summary");

    $("#kpi-grid").innerHTML = `
      <div class="kpi">
        <div class="kpi-label">Fleet health</div>
        <div class="kpi-value" style="color:${healthColor(s.fleet_health_avg)}">${s.fleet_health_avg}</div>
        <div class="kpi-sub">${s.machines_total} machines monitored</div>
      </div>
      <div class="kpi kpi-crit">
        <div class="kpi-label">Critical</div>
        <div class="kpi-value" style="color:var(--crit)">${s.machines_critical}</div>
        <div class="kpi-sub">below 40 / 100</div>
      </div>
      <div class="kpi kpi-warn">
        <div class="kpi-label">Warning</div>
        <div class="kpi-value" style="color:var(--warn)">${s.machines_warning}</div>
        <div class="kpi-sub">below 65 / 100</div>
      </div>
      <div class="kpi kpi-info">
        <div class="kpi-label">Open alerts</div>
        <div class="kpi-value">${s.open_alerts}</div>
        <div class="kpi-sub">unacknowledged</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">Work orders</div>
        <div class="kpi-value">${s.open_work_orders}</div>
        <div class="kpi-sub">open or in progress</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">Automation</div>
        <div class="kpi-value">${s.automation_actions_24h}</div>
        <div class="kpi-sub">actions in 24h</div>
      </div>`;

    if (!s.worst_machines.length) {
      $("#worst-list").innerHTML = `<div class="empty">No readings yet. Add a machine and send a reading to see health scores.</div>`;
    } else {
      $("#worst-list").innerHTML = s.worst_machines.map((m) => `
        <div class="worst-row" data-id="${m.id}">
          <div>
            <div class="worst-name">${esc(m.name)}</div>
            <div class="worst-tag">${esc(m.asset_tag)} · zone ${esc(m.vibration_zone)}</div>
          </div>
          <div class="worst-score" style="color:${healthColor(m.health_index)}">${m.health_index}</div>
          <div class="bar"><i style="width:${m.health_index}%;background:${healthColor(m.health_index)}"></i></div>
        </div>`).join("");

      $$(".worst-row").forEach((row) => {
        row.addEventListener("click", () => openMachine(Number(row.dataset.id)));
      });
    }

    if (!s.recent_automation.length) {
      $("#automation-feed").innerHTML = `<div class="empty">No automation has run yet.</div>`;
    } else {
      $("#automation-feed").innerHTML = s.recent_automation.map((a) => `
        <div class="feed-item">
          <div class="feed-dot ${esc(a.outcome)}"></div>
          <div>
            <div class="feed-action">${esc(a.action)} <span class="feed-time">· ${timeAgo(a.created_at)}</span></div>
            <div class="feed-detail">${esc(a.detail || a.runbook)}</div>
          </div>
        </div>`).join("");
    }
  } catch (err) {
    toast(err.message, true);
  }
}

/* ── Machines ─────────────────────────────────────────────────────────── */
async function loadMachines() {
  try {
    const [machines, plants] = await Promise.all([
      api("/api/machines"),
      api("/api/plants"),
    ]);
    state.machines = machines;
    state.plants = plants;

    if (!machines.length) {
      $("#machine-list").innerHTML = `<div class="empty">No machines yet. Click “+ Add machine” to register your first asset.</div>`;
      return;
    }

    const details = await Promise.all(
      machines.map((m) => api(`/api/machines/${m.id}`).catch(() => null))
    );

    $("#machine-list").innerHTML = machines.map((m, i) => {
      const d = details[i];
      const h = d ? d.health_index : null;
      return `
        <div class="machine-row" data-id="${m.id}">
          <div>
            <div class="machine-name">${esc(m.name)}</div>
            <div class="machine-meta">${esc(m.asset_tag)} · ${esc(m.machine_type)} · class ${m.power_class}</div>
          </div>
          <span class="badge ${healthClass(h)}">${h === null ? "no data" : h + "/100"}</span>
          <span class="badge badge-muted">${esc(m.status)}</span>
        </div>`;
    }).join("");

    $$(".machine-row").forEach((row) => {
      row.addEventListener("click", () => openMachine(Number(row.dataset.id)));
    });
  } catch (err) {
    toast(err.message, true);
  }
}

async function openMachine(id) {
  try {
    const d = await api(`/api/machines/${id}`);
    state.selectedMachine = d;

    $$(".tab").forEach((t) => t.classList.remove("is-active"));
    $('.tab[data-view="machines"]').classList.add("is-active");
    $$(".view").forEach((v) => v.classList.add("is-hidden"));
    $("#view-machines").classList.remove("is-hidden");
    $("#machine-detail-panel").classList.remove("is-hidden");

    $("#detail-title").textContent = `${d.name} — ${d.asset_tag}`;

    const rulText = d.rul_days === null || d.rul_days === undefined
      ? "—"
      : `${d.rul_days} d`;

    $("#machine-detail").innerHTML = `
      <div class="detail-cell">
        <div class="detail-label">Health</div>
        <div class="detail-value" style="color:${healthColor(d.health_index)}">${d.health_index ?? "—"}</div>
      </div>
      <div class="detail-cell">
        <div class="detail-label">Vibration zone</div>
        <div class="detail-value">${esc(d.vibration_zone || "—")}</div>
      </div>
      <div class="detail-cell">
        <div class="detail-label">RUL estimate</div>
        <div class="detail-value">${rulText}</div>
        <div class="detail-label" style="margin-top:4px">${esc(d.rul_confidence || "")}</div>
      </div>
      <div class="detail-cell">
        <div class="detail-label">Open alerts</div>
        <div class="detail-value">${d.open_alerts}</div>
      </div>
      <div class="detail-cell">
        <div class="detail-label">Work orders</div>
        <div class="detail-value">${d.open_work_orders}</div>
      </div>
      <div class="detail-cell">
        <div class="detail-label">Runtime</div>
        <div class="detail-value sm">${Number(d.runtime_hours).toLocaleString()} h</div>
      </div>
      <div class="detail-cell">
        <div class="detail-label">Baseline vib.</div>
        <div class="detail-value sm">${d.baseline_vibration} mm/s</div>
      </div>
      <div class="detail-cell">
        <div class="detail-label">Baseline temp</div>
        <div class="detail-value sm">${d.baseline_temperature} °C</div>
      </div>`;

    $("#reading-form").dataset.machineId = String(id);
    $("#reading-result").innerHTML = "";
    $("#machine-detail-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    toast(err.message, true);
  }
}

$("#close-detail").addEventListener("click", () => {
  $("#machine-detail-panel").classList.add("is-hidden");
});

$("#reading-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = e.target.dataset.machineId;
  if (!id) return;
  const fd = new FormData(e.target);
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  try {
    const r = await api(`/api/machines/${id}/readings`, {
      method: "POST",
      body: JSON.stringify({
        vibration_rms: Number(fd.get("vibration_rms")),
        temperature_c: Number(fd.get("temperature_c")),
        current_a: Number(fd.get("current_a")),
        runtime_hours: Number(fd.get("runtime_hours")),
      }),
    });

    const notes = (r.breakdown.notes || []).map((n) => `<div class="rr-line">· ${esc(n)}</div>`).join("");
    $("#reading-result").innerHTML = `
      <div class="rr-line"><strong>Health ${r.health_index}/100</strong> · ISO zone ${esc(r.vibration_zone)}</div>
      <div class="rr-line">Penalties — vibration ${r.breakdown.vibration}, thermal ${r.breakdown.thermal}, current ${r.breakdown.current}, duty ${r.breakdown.duty}</div>
      ${notes}
      ${r.alert_created ? `<div class="rr-line" style="color:var(--warn)"><strong>Alert raised.</strong>${r.work_order_id ? ` Work order #${r.work_order_id} opened automatically.` : ""}</div>` : ""}
      ${r.rul_days !== null && r.rul_days !== undefined ? `<div class="rr-line">Estimated RUL: <strong>${r.rul_days} days</strong> (${esc(r.rul_confidence)} confidence)</div>` : ""}`;

    toast(r.alert_created ? "Reading scored — alert raised" : "Reading scored");
    loadDashboard();
    openMachine(Number(id));
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});

/* ── Add machine modal ────────────────────────────────────────────────── */
$("#add-machine-btn").addEventListener("click", async () => {
  try {
    if (!state.plants.length) {
      state.plants = await api("/api/plants");
    }
    if (!state.plants.length) {
      const plant = await api("/api/plants", {
        method: "POST",
        body: JSON.stringify({ name: "Karachi Plant 1", city: "Karachi" }),
      });
      state.plants = [plant];
    }
    $("#plant-select").innerHTML = state.plants
      .map((p) => `<option value="${p.id}">${esc(p.name)} — ${esc(p.city)}</option>`)
      .join("");
    $("#modal").classList.remove("is-hidden");
  } catch (err) {
    toast(err.message, true);
  }
});

$("#modal-close").addEventListener("click", () => $("#modal").classList.add("is-hidden"));

$("#machine-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  try {
    await api("/api/machines", {
      method: "POST",
      body: JSON.stringify({
        plant_id: Number(fd.get("plant_id")),
        name: fd.get("name"),
        asset_tag: fd.get("asset_tag"),
        machine_type: fd.get("machine_type"),
        power_class: Number(fd.get("power_class")),
        baseline_vibration: Number(fd.get("baseline_vibration")),
        baseline_temperature: Number(fd.get("baseline_temperature")),
        baseline_current: Number(fd.get("baseline_current")),
      }),
    });
    $("#modal").classList.add("is-hidden");
    e.target.reset();
    toast("Machine registered");
    loadMachines();
    loadDashboard();
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});

/* ── Alerts ───────────────────────────────────────────────────────────── */
async function loadAlerts() {
  try {
    const sev = $("#alert-severity-filter").value;
    const alerts = await api(`/api/alerts${sev ? `?severity=${sev}` : ""}`);

    if (!alerts.length) {
      $("#alert-list").innerHTML = `<div class="empty">No alerts. Everything is running inside its envelope.</div>`;
      return;
    }

    $("#alert-list").innerHTML = alerts.map((a) => `
      <div class="alert-row ${esc(a.severity)}">
        <div class="alert-top">
          <div class="alert-title">${esc(a.title)}</div>
          <span class="badge ${a.severity === "critical" ? "badge-crit" : a.severity === "warning" ? "badge-warn" : "badge-info"}">${esc(a.severity)}</span>
        </div>
        <div class="alert-detail">${esc(a.detail)}</div>
        <div class="alert-meta">
          <span>machine #${a.machine_id}</span>
          <span>health ${a.health_index}</span>
          <span>${timeAgo(a.created_at)}</span>
          ${a.acknowledged ? "<span>✓ acknowledged</span>" : `<button class="btn btn-ghost btn-sm" data-ack="${a.id}">Acknowledge</button>`}
        </div>
      </div>`).join("");

    $$("[data-ack]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          await api(`/api/alerts/${btn.dataset.ack}/acknowledge`, { method: "POST" });
          toast("Alert acknowledged");
          loadAlerts();
          loadDashboard();
        } catch (err) {
          toast(err.message, true);
        }
      });
    });
  } catch (err) {
    toast(err.message, true);
  }
}

$("#alert-severity-filter").addEventListener("change", loadAlerts);

/* ── Work orders ──────────────────────────────────────────────────────── */
async function loadOrders() {
  try {
    const st = $("#order-status-filter").value;
    const orders = await api(`/api/work-orders${st ? `?status_filter=${st}` : ""}`);

    if (!orders.length) {
      $("#order-list").innerHTML = `<div class="empty">No work orders. The RPA opens them automatically when a machine crosses a threshold.</div>`;
      return;
    }

    $("#order-list").innerHTML = orders.map((o) => `
      <div class="order-row">
        <div class="order-top">
          <div class="order-title">#${o.id} · ${esc(o.title)}</div>
          <span class="badge ${o.priority === "urgent" ? "badge-crit" : o.priority === "high" ? "badge-warn" : "badge-muted"}">${esc(o.priority)}</span>
        </div>
        <div class="order-desc">${esc(o.description.split("\n")[0])}</div>
        <div class="order-meta">
          <span>${esc(o.status)}</span>
          <span>${esc(o.assigned_to || "unassigned")}</span>
          <span>by ${esc(o.created_by)}</span>
          <span>${timeAgo(o.created_at)}</span>
          ${o.status !== "done" && o.status !== "cancelled"
            ? `<button class="btn btn-ghost btn-sm" data-done="${o.id}">Mark done</button>`
            : ""}
        </div>
      </div>`).join("");

    $$("[data-done]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          await api(`/api/work-orders/${btn.dataset.done}`, {
            method: "PATCH",
            body: JSON.stringify({ status: "done" }),
          });
          toast("Work order closed");
          loadOrders();
          loadDashboard();
        } catch (err) {
          toast(err.message, true);
        }
      });
    });
  } catch (err) {
    toast(err.message, true);
  }
}

$("#order-status-filter").addEventListener("change", loadOrders);

/* ── Automation log ───────────────────────────────────────────────────── */
async function loadAutomation() {
  try {
    const logs = await api("/api/automation-log?limit=100");
    if (!logs.length) {
      $("#automation-table").innerHTML = `<div class="empty">No automation has run yet.</div>`;
      return;
    }
    $("#automation-table").innerHTML = logs.map((l) => `
      <div class="auto-row">
        <div class="auto-runbook">${esc(l.runbook)}</div>
        <div class="auto-detail"><strong>${esc(l.action)}</strong> — ${esc(l.detail || l.outcome)}</div>
        <div class="auto-time">${timeAgo(l.created_at)}</div>
      </div>`).join("");
  } catch (err) {
    toast(err.message, true);
  }
}

/* ── Boot ─────────────────────────────────────────────────────────────── */
function loadAll() {
  loadDashboard();
  loadMachines();
}

if (state.token) {
  api("/api/auth/me")
    .then((user) => {
      state.user = user;
      localStorage.setItem(USER_KEY, JSON.stringify(user));
      showApp();
    })
    .catch(() => logout());
} else {
  logout();
}
