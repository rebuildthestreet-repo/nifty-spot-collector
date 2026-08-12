"use strict";

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

async function api(path, opts) {
  const resp = await fetch(path, opts);
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(body.error || `${resp.status} ${resp.statusText}`);
  }
  return body;
}

function el(id) {
  return document.getElementById(id);
}

function fmtExpiry(iso) {
  if (!iso) return "unknown";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    weekday: "short", hour: "2-digit", minute: "2-digit",
    year: "numeric", month: "short", day: "numeric",
  }) + " IST";
}

// Streams a POST response body live into onText(), holding back only the
// last few hundred characters until the stream closes -- long enough to
// safely catch the trailing __LAUNCHER_RESULT__{...} marker line (see
// server.py) without buffering the whole (possibly minutes-long) output
// before showing anything.
async function streamPost(url, body, onText) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!resp.ok) {
    let msg = resp.statusText;
    try { msg = (await resp.json()).error || msg; } catch (e) { /* ignore */ }
    onText(`[launcher] ${msg}\n`);
    return null;
  }

  const marker = "__LAUNCHER_RESULT__";
  const holdback = marker.length + 300;
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let tail = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    tail += decoder.decode(value, { stream: true });
    if (tail.length > holdback) {
      const flushLen = tail.length - holdback;
      onText(tail.slice(0, flushLen));
      tail = tail.slice(flushLen);
    }
  }

  const idx = tail.indexOf(marker);
  if (idx === -1) {
    onText(tail);
    return null;
  }
  onText(tail.slice(0, idx));
  try {
    return JSON.parse(tail.slice(idx + marker.length).trim());
  } catch (e) {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Status card
// ---------------------------------------------------------------------------

let latestStatus = null;
let latestEnv = null;

function renderTokenBanner(token) {
  const banner = el("token-banner");
  banner.classList.remove("ok", "warn", "none");
  if (!token.present) {
    banner.textContent = "Token: not connected yet. See “Connect” below.";
    banner.classList.add("none");
    return;
  }
  const when = fmtExpiry(token.expiry);
  if (token.expired) {
    banner.innerHTML = `Token: <strong>expired</strong> (was valid until ${when}). This is normal &mdash; it expires every morning. <a href="#card-connect">Connect again →</a>`;
    banner.classList.add("warn");
  } else {
    banner.textContent = `Token: valid until ${when}.`;
    banner.classList.add("ok");
  }
  if (token.shadowed_sources && token.shadowed_sources.length) {
    banner.innerHTML += ` <span class="shadow-note">(also present in: ${token.shadowed_sources.join(", ")} — not in effect)</span>`;
  }
}

function tile(label, ok, detail) {
  const cls = ok === true ? "ok" : ok === false ? "fail" : "skip";
  return `<div class="tile ${cls}"><span class="tile-label">${label}</span><span class="tile-detail">${detail || ""}</span></div>`;
}

function renderStatus(status) {
  latestStatus = status;
  const tiles = [
    tile("Python", status.python_version ? true : null, `${status.python_version || "?"} (${status.interpreter || "?"})`),
    tile("Dependencies", status.deps_ok, status.deps_ok ? "installed" : "run setup below"),
    tile("sqlite3 CLI", status.sqlite3_present, status.sqlite3_present ? "found" : "not found (optional)"),
    tile(".env", status.env_file.exists, status.env_file.exists ? status.env_file.path : "not found yet"),
    tile("Database", status.db.path ? status.db.exists : null,
      status.db.path ? `${status.db.path}${status.db.exists ? ` (${status.db.row_count ?? "?"} rows)` : " (not created yet)"}` : "SPOT_DB_PATH not set"),
  ];
  el("status-tiles").innerHTML = tiles.join("");
  el("status-raw").textContent = status.raw_output || "";
  renderTokenBanner(status.token);
  updateLocks();
}

async function refreshStatus() {
  try {
    renderStatus(await api("/api/status"));
  } catch (e) {
    el("status-tiles").textContent = `Could not load status: ${e.message}`;
  }
}

// ---------------------------------------------------------------------------
// Credentials card
// ---------------------------------------------------------------------------

const CRED_KEYS = ["SPOT_DB_PATH", "UPSTOX_CLIENT_ID", "UPSTOX_CLIENT_SECRET", "UPSTOX_REDIRECT_URI"];

function renderConflictWarning(fieldEl, info) {
  const warn = fieldEl.querySelector(".conflict-warning");
  if (info && info.conflict) {
    warn.hidden = false;
    warn.textContent = `A real environment variable is set to a different value and is winning over what's shown here (in effect: ${maskIfSecret(fieldEl, info.env_value)}).`;
  } else {
    warn.hidden = true;
  }
}

function maskIfSecret(fieldEl, value) {
  if (fieldEl.dataset.secret === "true" && value) {
    return "•".repeat(Math.min(value.length, 12));
  }
  return value;
}

function renderEnv(envData) {
  latestEnv = envData;
  el("env-path").textContent = envData.path;
  el("env-raw").textContent = maskRawEnvText(envData.raw_text, envData.secret_keys);
  el("redirect-uri-hint").textContent = envData.default_redirect_uri;
  el("connect-redirect-uri").textContent = envData.default_redirect_uri;

  for (const key of CRED_KEYS) {
    const input = el(`f-${key}`);
    const fieldEl = input.closest(".field");
    const info = envData.fields[key];
    if (document.activeElement !== input) {
      input.value = info.file_value || (key === "UPSTOX_REDIRECT_URI" ? envData.default_redirect_uri : "");
    }
    renderConflictWarning(fieldEl, info);
  }
  updateLocks();
}

function maskRawEnvText(text, secretKeys) {
  if (!text) return "(no .env file yet)";
  return text
    .split("\n")
    .map((line) => {
      const m = line.match(/^([A-Z_]+)=(.*)$/);
      if (m && secretKeys.includes(m[1]) && m[2]) {
        return `${m[1]}=${"•".repeat(Math.min(m[2].length, 12))}`;
      }
      return line;
    })
    .join("\n");
}

async function refreshEnv() {
  try {
    renderEnv(await api("/api/env"));
  } catch (e) {
    el("env-raw").textContent = `Could not load .env: ${e.message}`;
  }
}

function setupRevealToggles() {
  document.querySelectorAll(".reveal-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = el(btn.dataset.target);
      const showing = input.type === "text";
      input.type = showing ? "password" : "text";
      btn.textContent = showing ? "Show" : "Hide";
    });
  });
}

async function saveCredentials(ev) {
  ev.preventDefault();
  const updates = {};
  for (const key of CRED_KEYS) updates[key] = el(`f-${key}`).value;
  const btn = el("btn-save-credentials");
  btn.disabled = true;
  try {
    await api("/api/env", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    });
    await refreshEnv();
    await refreshStatus();
  } catch (e) {
    alert(`Could not save .env: ${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

async function savePastedToken() {
  const input = el("f-paste-token");
  const errorEl = el("paste-token-error");
  errorEl.hidden = true;
  const btn = el("btn-save-token");
  btn.disabled = true;
  try {
    await api("/api/env", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ UPSTOX_ACCESS_TOKEN: input.value }),
    });
    input.value = "";
    await refreshEnv();
    await refreshStatus();
  } catch (e) {
    errorEl.textContent = e.message;
    errorEl.hidden = false;
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Install (setup.sh)
// ---------------------------------------------------------------------------

async function runSetup() {
  const out = el("install-output");
  const btn = el("btn-run-setup");
  out.hidden = false;
  out.textContent = "";
  btn.disabled = true;
  const result = await streamPost("/api/setup/stream", {}, (text) => {
    out.textContent += text;
    out.scrollTop = out.scrollHeight;
  });
  btn.disabled = false;
  if (result) {
    out.textContent += result.ok ? "\n[launcher] setup.sh finished successfully.\n" : `\n[launcher] setup.sh exited with code ${result.returncode}.\n`;
  }
  await refreshStatus();
}

// ---------------------------------------------------------------------------
// Connect (OAuth)
// ---------------------------------------------------------------------------

async function loadLoginUrl() {
  try {
    const data = await api("/api/oauth/login-url");
    const link = el("link-oauth-login");
    if (data.login_url) {
      link.href = data.login_url;
      link.classList.remove("disabled");
    } else {
      link.href = "#";
      link.classList.add("disabled");
    }
    const statusEl = el("connect-token-status");
    if (data.missing.length) {
      statusEl.textContent = `Save these fields on the Credentials card first: ${data.missing.join(", ")}.`;
    } else {
      statusEl.textContent = "";
    }
  } catch (e) {
    el("connect-token-status").textContent = `Could not build login URL: ${e.message}`;
  }
}

let oauthPollTimer = null;

function startOauthPolling() {
  const out = el("oauth-output");
  out.hidden = false;
  out.textContent = "Waiting for the redirect from Upstox…\n";
  if (oauthPollTimer) clearInterval(oauthPollTimer);
  oauthPollTimer = setInterval(async () => {
    let state;
    try {
      state = await api("/api/oauth/result");
    } catch (e) {
      return;
    }
    if (state.transcript) {
      out.textContent = state.transcript;
    }
    if (!state.pending && state.success !== null) {
      clearInterval(oauthPollTimer);
      oauthPollTimer = null;
      await refreshStatus();
      await refreshEnv();
    }
  }, 1000);
}

// ---------------------------------------------------------------------------
// Collect
// ---------------------------------------------------------------------------

function defaultDateRange() {
  const to = new Date();
  to.setDate(to.getDate() - 31);
  const from = new Date(to);
  from.setDate(from.getDate() - 7);
  const fmt = (d) => d.toISOString().slice(0, 10);
  return { from: fmt(from), to: fmt(to) };
}

async function runCollect(ev) {
  ev.preventDefault();
  const out = el("collect-output");
  const chartResult = el("chart-result");
  const btn = el("btn-run-collect");
  out.hidden = false;
  out.textContent = "";
  chartResult.hidden = true;
  btn.disabled = true;

  const body = { from: el("f-from-date").value, to: el("f-to-date").value };
  const result = await streamPost("/api/collect/stream", body, (text) => {
    out.textContent += text;
    out.scrollTop = out.scrollHeight;
  });
  btn.disabled = false;

  if (result && result.chart_ok) {
    el("chart-path").textContent = result.chart_path;
    const bust = `/api/chart-file?t=${Date.now()}`;
    el("chart-image").src = bust;
    el("chart-download").href = bust;
    chartResult.hidden = false;
  }
  await refreshStatus();
}

// ---------------------------------------------------------------------------
// Section locking
// ---------------------------------------------------------------------------

function setLocked(id, locked, reason) {
  const section = el(id);
  section.classList.toggle("locked", locked);
  let note = section.querySelector(".lock-note");
  if (locked) {
    if (!note) {
      note = document.createElement("p");
      note.className = "lock-note";
      section.insertBefore(note, section.children[1]);
    }
    note.textContent = reason;
  } else if (note) {
    note.remove();
  }
}

function updateLocks() {
  if (!latestStatus || !latestEnv) return;
  const depsOk = latestStatus.deps_ok;
  const dbSet = !!latestEnv.fields.SPOT_DB_PATH.effective_value;
  const credsSet = latestEnv.fields.UPSTOX_CLIENT_ID.effective_value
    && latestEnv.fields.UPSTOX_CLIENT_SECRET.effective_value
    && latestEnv.fields.UPSTOX_REDIRECT_URI.effective_value;

  setLocked("card-connect", !depsOk || !credsSet,
    !depsOk ? "Requires: dependencies installed." : "Requires: API Key, API Secret, and Redirect URL saved below.");
  setLocked("card-collect", !depsOk || !dbSet,
    !depsOk ? "Requires: dependencies installed." : "Requires: database file location saved on the Credentials card.");

  const warn = el("collect-token-warning");
  if (latestStatus.token.present && !latestStatus.token.expired) {
    warn.hidden = true;
  } else {
    warn.hidden = false;
    warn.textContent = latestStatus.token.present
      ? "Token is expired -- collect will fail. Connect again above first."
      : "No token yet -- collect will fail until you Connect above.";
  }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  const range = defaultDateRange();
  el("f-from-date").value = range.from;
  el("f-to-date").value = range.to;

  setupRevealToggles();

  el("btn-refresh-status").addEventListener("click", refreshStatus);
  el("btn-run-setup").addEventListener("click", runSetup);
  el("form-credentials").addEventListener("submit", saveCredentials);
  el("btn-save-token").addEventListener("click", savePastedToken);
  el("form-collect").addEventListener("submit", runCollect);
  el("btn-copy-redirect-uri").addEventListener("click", () => {
    navigator.clipboard.writeText(el("connect-redirect-uri").textContent);
  });
  el("link-oauth-login").addEventListener("click", (ev) => {
    if (el("link-oauth-login").classList.contains("disabled")) {
      ev.preventDefault();
      return;
    }
    startOauthPolling();
  });

  refreshStatus();
  refreshEnv();
  loadLoginUrl();
});
