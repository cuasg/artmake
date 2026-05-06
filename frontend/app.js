/* global window, document, fetch */

const $ = (id) => document.getElementById(id);
const setText = (id, text) => {
  const el = $(id);
  if (el) el.textContent = text;
};

const state = {
  ws: null,
  connected: false,
  lastFrame: null,
  lastSettings: null,
  patterns: [],
  // Settings page drafts (only committed when user clicks Save)
  draftPatch: null,
  // Controls are often updated from server frame payloads; we must not
  // overwrite a user edit mid-drag. We track "recent local edits" per control.
  uiLocks: new Set(),
  lastLocalEditAt: new Map(), // key -> ms timestamp
  wsGen: 0,
  reconnectTimer: null,
  render: {
    ledShape: "circle",
    ledSpacing: 1,
    glow: 0.25,
  },
  canvas: {
    el: null,
    ctx: null,
    dpr: 1,
    w: 0,
    h: 0,
  },
  pendingPatch: null,
  patchTimer: null,
  inFlightPatch: null,
  inFlightSince: 0,
  pendingUpload: null, // { file: File, name: string }
};

function setUploadUiBusy(on) {
  const btnSave = $("btnUploadSave");
  const btnClear = $("btnUploadClear");
  const file = $("fileUpload");
  const label = $("inpUploadLabel");
  const crop = $("selCropFocus");
  if (btnSave) btnSave.disabled = on || !state.pendingUpload;
  if (btnClear) btnClear.disabled = on || !state.pendingUpload;
  if (file) file.disabled = !!on;
  if (label) label.disabled = !!on;
  if (crop) crop.disabled = !!on;
}

function showUploadProgress(show) {
  const wrap = $("uploadProgressWrap");
  if (wrap) wrap.hidden = !show;
}

function setUploadProgress(pct, text = null) {
  const bar = $("uploadProgressBar");
  const txt = $("txtUploadProgress");
  const pctEl = $("txtUploadProgressPct");
  if (txt && text != null) txt.textContent = String(text);
  if (!bar) return;
  bar.classList.remove("indeterminate");
  const p = Math.max(0, Math.min(1, Number(pct)));
  bar.style.width = `${Math.round(p * 100)}%`;
  if (pctEl) pctEl.textContent = `${Math.round(p * 100)}%`;
}

function setUploadIndeterminate(text = "Working…") {
  const bar = $("uploadProgressBar");
  const txt = $("txtUploadProgress");
  const pctEl = $("txtUploadProgressPct");
  if (txt) txt.textContent = text;
  if (pctEl) pctEl.textContent = "";
  if (bar) {
    bar.classList.add("indeterminate");
    bar.style.width = "40%";
  }
}

async function pollJob(jobId, { onUpdate } = {}) {
  const start = Date.now();
  while (true) {
    const j = await apiGet(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (typeof onUpdate === "function") onUpdate(j);
    if (j && (j.status === "done" || j.status === "error")) return j;
    // back off a bit over time
    const age = Date.now() - start;
    const delay = age < 4000 ? 350 : age < 15000 ? 650 : 1000;
    await new Promise((r) => window.setTimeout(r, delay));
  }
}

function isSettingsPage() {
  try {
    return window.location && String(window.location.pathname || "").toLowerCase().endsWith("/settings.html");
  } catch (_) {
    return false;
  }
}

function setCommitBarVisible(on) {
  const bar = $("settingsCommitBar");
  if (bar) bar.hidden = !on;
}

function stageOrSendSettingsPatch(patch, opts = { immediate: false }) {
  if (!isSettingsPage()) {
    queueSettingsPatch(patch, opts);
    return;
  }

  // Stage locally until Save is clicked.
  state.draftPatch = deepMerge(state.draftPatch || {}, patch);
  setCommitBarVisible(true);

  // Optimistic UI on settings page too.
  if (state.lastSettings) {
    const merged = deepMerge(state.lastSettings, state.draftPatch);
    applySettingsToUI(merged);
  }
}

function wsUrl() {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/ws`;
}

function setConnectionStatus(text) {
  setText("statusConnection", text);
}

async function apiGet(path) {
  const res = await fetch(path, { method: "GET" });
  if (!res.ok) throw new Error(`GET ${path} failed (${res.status})`);
  return await res.json();
}

async function apiPost(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : "{}",
  });
  if (!res.ok) throw new Error(`POST ${path} failed (${res.status})`);
  return await res.json();
}

async function apiPostDetailed(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : "{}",
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data && data.detail;
    const msg =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail) && detail.length
          ? detail.map((d) => d.msg || d).join("; ")
          : `POST ${path} failed (${res.status})`;
    throw new Error(msg);
  }
  return data;
}

async function apiUpload(path, file, opts = {}) {
  const fd = new FormData();
  fd.append("file", file);
  const label = (opts.label || "").trim();
  if (label) fd.append("label", label);
  const cropFocus = (opts.crop_focus || "").trim();
  if (cropFocus) fd.append("crop_focus", cropFocus);
  const res = await fetch(path, { method: "POST", body: fd });
  if (!res.ok) throw new Error(`POST ${path} failed (${res.status})`);
  return await res.json();
}

async function apiPatch(path, body) {
  const res = await fetch(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`PATCH ${path} failed (${res.status})`);
  return await res.json();
}

async function apiDelete(path) {
  const res = await fetch(path, { method: "DELETE" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data && data.detail;
    const msg = typeof detail === "string" ? detail : `DELETE ${path} failed (${res.status})`;
    throw new Error(msg);
  }
  return data;
}

function formatMatrix(settings) {
  const w = settings?.matrix?.width;
  const h = settings?.matrix?.height;
  if (!w || !h) return "—";
  return `${w}×${h}`;
}

function syncPhotoLineDrawingUi(settings) {
  const hint = $("photoPatternHint");
  const pattern = settings?.art?.pattern ?? ($("selPattern")?.value || "");
  const isPhoto = pattern === "living_drawing";
  if (hint) {
    if (isPhoto) {
      hint.textContent = "";
      hint.classList.remove("isVisible");
    } else {
      hint.textContent =
        "Choose Living drawing in Pattern to animate uploads on the matrix (brightness above applies there too).";
      hint.classList.add("isVisible");
    }
  }

  const photoControls = $("photoControls");
  if (photoControls) {
    photoControls.style.display = isPhoto ? "" : "none";
  }
}

function applySettingsToUI(settings) {
  if (!settings) return;

  // Some messages (e.g. "status") can be partial. Only proceed when we have
  // the core sub-objects required to render the UI controls.
  const hasFullShape =
    settings.matrix && settings.stream && settings.art && settings.simulator && settings.output;

  const recentlyEdited = (key, windowMs = 700) => {
    const t = state.lastLocalEditAt.get(key) || 0;
    return Date.now() - t < windowMs;
  };

  // Simulator status bar exists only on the main page.
  if ($("statusOutput")) $("statusOutput").textContent = settings?.output?.mode || "simulator";
  setText("statusOutput", settings?.output?.mode || "simulator");
  setText("statusRunning", settings?.running ? "running" : "stopped");
  setText("statusMatrix", formatMatrix(settings));
  const learned = settings.learned?.learned_max_fps;
  const learnedProfile = settings.learned?.profile;
  const learnedLabel = learned ? `, learned ${learned}${learnedProfile ? ` (${learnedProfile})` : ""}` : "";
  const fpsLabel =
    settings?.stream
      ? (settings.stream.auto_fps
          ? `${settings.stream.fps} (auto, cap ${settings.stream.max_fps}${learnedLabel})`
          : `${settings.stream.fps}`)
      : "—";
  setText("statusFps", fpsLabel);
  setText("statusPattern", String(settings?.art?.pattern ?? "—"));

  // Sidebar “Now playing”
  const nowPlaying = $("txtNowPlaying");
  if (nowPlaying) {
    const pat = String(settings?.art?.pattern ?? "—");
    const patInfo = Array.isArray(state.patterns) ? state.patterns.find((p) => p && p.name === pat) : null;
    const patLabel = patInfo?.display_name || pat;
    let extra = "";
    if (pat === "living_drawing") {
      const opt = $("selDrawing")?.selectedOptions && $("selDrawing").selectedOptions[0];
      const lab = (opt && opt.dataset && opt.dataset.displayLabel) || settings?.art?.drawing_id || "";
      if (lab) extra = ` · ${lab}`;
    }
    nowPlaying.textContent = `${patLabel}${extra}`;
  }

  const oa = settings.integrations?.openai;
  if (oa) {
    const badge = $("badgeOpenAiKey");
    if (badge) {
      const ok = !!oa.api_key_configured;
      const fp = (oa.api_key_fingerprint || "").toString().trim();
      badge.textContent = ok ? `Key saved${fp ? ` (${fp})` : ""}` : "Key missing";
      badge.classList.toggle("good", ok);
      badge.classList.toggle("bad", !ok);
    }
    if ($("txtOpenAiKeyHint")) {
      $("txtOpenAiKeyHint").textContent = oa.api_key_configured
        ? "API key is saved locally."
        : "No API key saved yet — use Save key or set OPENAI_API_KEY in the environment.";
    }
    if ($("txtOpenAiModel") && !state.uiLocks.has("txtOpenAiModel") && !recentlyEdited("txtOpenAiModel")) {
      $("txtOpenAiModel").value = oa.model || "";
    }
  }

  if (!hasFullShape) return;

  if ($("selPattern") && !state.uiLocks.has("selPattern") && !recentlyEdited("selPattern")) $("selPattern").value = settings.art.pattern;
  if ($("rngBrightness") && !state.uiLocks.has("rngBrightness") && !recentlyEdited("rngBrightness")) $("rngBrightness").value = String(settings.art.brightness);
  if ($("txtBrightness") && !recentlyEdited("rngBrightness")) $("txtBrightness").textContent = Number(settings.art.brightness).toFixed(2);
  if ($("rngSpeed") && !state.uiLocks.has("rngSpeed") && !recentlyEdited("rngSpeed")) $("rngSpeed").value = String(settings.art.speed);
  if ($("txtSpeed") && !recentlyEdited("rngSpeed")) $("txtSpeed").textContent = Number(settings.art.speed).toFixed(2);
  if ($("numFps") && !state.uiLocks.has("numFps") && !recentlyEdited("numFps")) $("numFps").value = String(settings.stream.fps);
  if ($("selAutoFps") && !state.uiLocks.has("selAutoFps") && !recentlyEdited("selAutoFps")) $("selAutoFps").value = String(!!settings.stream.auto_fps);
  if ($("numMaxFps") && !state.uiLocks.has("numMaxFps") && !recentlyEdited("numMaxFps")) $("numMaxFps").value = String(settings.stream.max_fps);
  if ($("selAutoLearn") && !state.uiLocks.has("selAutoLearn") && !recentlyEdited("selAutoLearn")) $("selAutoLearn").value = String(!!settings.stream.auto_learn);

  const preset = `${settings.matrix.width}x${settings.matrix.height}`;
  if ($("selMatrixPreset") && !state.uiLocks.has("selMatrixPreset") && !recentlyEdited("selMatrixPreset")) $("selMatrixPreset").value = preset;

  if ($("selLedShape") && !state.uiLocks.has("selLedShape") && !recentlyEdited("selLedShape")) $("selLedShape").value = settings.simulator.led_shape;
  if ($("rngLedSpacing") && !state.uiLocks.has("rngLedSpacing") && !recentlyEdited("rngLedSpacing")) $("rngLedSpacing").value = String(settings.simulator.led_spacing);
  if ($("txtLedSpacing") && !recentlyEdited("rngLedSpacing")) $("txtLedSpacing").textContent = String(settings.simulator.led_spacing);
  if ($("rngGlow") && !state.uiLocks.has("rngGlow") && !recentlyEdited("rngGlow")) $("rngGlow").value = String(settings.simulator.glow);
  if ($("txtGlow") && !recentlyEdited("rngGlow")) $("txtGlow").textContent = Number(settings.simulator.glow).toFixed(2);

  state.render.ledShape = settings.simulator.led_shape;
  state.render.ledSpacing = settings.simulator.led_spacing;
  state.render.glow = settings.simulator.glow;

  // Living drawing defaults
  if ($("clrLine") && !state.uiLocks.has("clrLine") && !recentlyEdited("clrLine")) $("clrLine").value = settings.art.line_color || "#b8d7ff";
  if ($("numDrawPps") && !state.uiLocks.has("numDrawPps") && !recentlyEdited("numDrawPps")) $("numDrawPps").value = String(settings.art.draw_pps ?? 250);
  if ($("numHold") && !state.uiLocks.has("numHold") && !recentlyEdited("numHold")) $("numHold").value = String(settings.art.hold_seconds ?? 4);
  if ($("numErasePps") && !state.uiLocks.has("numErasePps") && !recentlyEdited("numErasePps")) $("numErasePps").value = String(settings.art.erase_pps ?? 800);
  if ($("selToolpathSource") && !state.uiLocks.has("selToolpathSource") && !recentlyEdited("selToolpathSource")) $("selToolpathSource").value = settings.art.toolpath_source || "auto";

  syncPhotoLineDrawingUi(settings);
}

function effectiveOverlayPatch() {
  // While a settings update is in-flight, keep overlaying it on top of
  // any server settings to prevent UI snap-back.
  if (state.inFlightPatch && state.pendingPatch) return deepMerge(state.inFlightPatch, state.pendingPatch);
  return state.inFlightPatch || state.pendingPatch;
}

async function sendSettingsPatchNow() {
  if (state.patchTimer) {
    window.clearTimeout(state.patchTimer);
    state.patchTimer = null;
  }
  if (state.inFlightPatch) return; // already sending; we'll send again after it completes
  if (!state.pendingPatch) return;

  const toSend = state.pendingPatch;
  // Keep it overlaid until we get an ack.
  state.inFlightPatch = toSend;
  state.inFlightSince = Date.now();
  state.pendingPatch = null;

  try {
    const updated = await apiPost("/api/settings", sanitizeSettingsPatchForApi(toSend));
    state.lastSettings = updated;
    state.inFlightPatch = null;
    applySettingsToUI(updated);
  } catch (err) {
    console.error(err);
    // Keep overlay for a bit; then drop so UI can recover.
    // (Most likely a transient network issue.)
    if (Date.now() - state.inFlightSince > 2500) {
      state.inFlightPatch = null;
    }
  } finally {
    // If changes accumulated while we were sending, send again immediately.
    if (state.pendingPatch) {
      // Avoid tight loop; yield a tick.
      window.setTimeout(() => { void sendSettingsPatchNow(); }, 0);
    }
  }
}

function queueSettingsPatch(patch, opts = { immediate: false }) {
  // Merge patches locally and debounce network call.
  state.pendingPatch = deepMerge(state.pendingPatch || {}, patch);

  // Optimistic UI: apply overlay immediately.
  if (state.lastSettings) {
    const overlay = effectiveOverlayPatch();
    const merged = overlay ? deepMerge(state.lastSettings, overlay) : state.lastSettings;
    applySettingsToUI(merged);
  }

  if (opts.immediate) {
    void sendSettingsPatchNow();
    return;
  }

  if (state.patchTimer) window.clearTimeout(state.patchTimer);
  state.patchTimer = window.setTimeout(() => {
    void sendSettingsPatchNow();
  }, 80);
}

function deepMerge(base, patch) {
  if (base && typeof base === "object" && !Array.isArray(base) && patch && typeof patch === "object" && !Array.isArray(patch)) {
    const out = { ...base };
    for (const [k, v] of Object.entries(patch)) {
      out[k] = deepMerge(out[k], v);
    }
    return out;
  }
  return patch;
}

/** Strip UI/runtime-only keys so optimistic merges never POST junk that breaks pydantic or YAML. */
function sanitizeSettingsPatchForApi(patch) {
  if (!patch || typeof patch !== "object" || Array.isArray(patch)) return patch;
  const out = { ...patch };
  delete out.learned;
  delete out.effective_fps;
  delete out.version;
  if (out.integrations && typeof out.integrations === "object") {
    out.integrations = { ...out.integrations };
    const oa = out.integrations.openai;
    if (oa && typeof oa === "object") {
      out.integrations.openai = { ...oa };
      delete out.integrations.openai.api_key_configured;
    }
  }
  return out;
}

function initCanvas() {
  state.canvas.el = $("matrixCanvas");
  if (!state.canvas.el) {
    // Settings page (and other pages) don't have a simulator canvas.
    state.canvas.ctx = null;
    return;
  }
  state.canvas.ctx = state.canvas.el.getContext("2d", { alpha: false });
  resizeCanvasToCss();
  window.addEventListener("resize", () => resizeCanvasToCss());
}

function resizeCanvasToCss() {
  const canvas = state.canvas.el;
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  state.canvas.dpr = dpr;

  const w = Math.max(1, Math.floor(rect.width * dpr));
  const h = Math.max(1, Math.floor(rect.height * dpr));
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  state.canvas.w = w;
  state.canvas.h = h;
}

function clearCanvas() {
  const { ctx, w, h } = state.canvas;
  if (!ctx) return;
  ctx.fillStyle = "rgb(8,10,14)";
  ctx.fillRect(0, 0, w, h);
}

function drawFrame(frame) {
  if (!frame) return;
  const { ctx, w: cw, h: ch } = state.canvas;
  if (!ctx) return;
  const mw = frame.width;
  const mh = frame.height;
  const pixels = frame.pixels;
  const rgb = frame.rgb;
  if (!mw || !mh) return;
  if (!pixels && !rgb) return;

  // Determine cell size to fit matrix into canvas.
  const spacing = Math.max(0, Number(state.render.ledSpacing) || 0);
  const cellW = Math.floor((cw - spacing * (mw + 1)) / mw);
  const cellH = Math.floor((ch - spacing * (mh + 1)) / mh);
  const cell = Math.max(1, Math.min(cellW, cellH));

  const gridW = mw * cell + spacing * (mw + 1);
  const gridH = mh * cell + spacing * (mh + 1);
  const ox = Math.floor((cw - gridW) / 2);
  const oy = Math.floor((ch - gridH) / 2);

  ctx.clearRect(0, 0, cw, ch);
  ctx.fillStyle = "rgb(8,10,14)";
  ctx.fillRect(0, 0, cw, ch);

  const glow = Math.max(0, Math.min(1, Number(state.render.glow) || 0));
  ctx.shadowBlur = glow * cell * 0.9;

  const isCircle = state.render.ledShape === "circle";
  const r = isCircle ? Math.floor(cell / 2) : 0;

  if (rgb) {
    let i = 0;
    for (let y = 0; y < mh; y++) {
      for (let x = 0; x < mw; x++) {
        const rr = rgb[i];
        const gg = rgb[i + 1];
        const bb = rgb[i + 2];
        i += 3;

        const px = ox + spacing + x * (cell + spacing);
        const py = oy + spacing + y * (cell + spacing);

        ctx.fillStyle = `rgb(${rr},${gg},${bb})`;
        ctx.shadowColor = `rgba(${rr},${gg},${bb},${0.25 + glow * 0.45})`;

        if (isCircle) {
          ctx.beginPath();
          ctx.arc(px + r, py + r, Math.max(1, r - 0.3), 0, Math.PI * 2);
          ctx.fill();
        } else {
          ctx.fillRect(px, py, cell, cell);
        }
      }
    }
  } else {
    for (let y = 0; y < mh; y++) {
      const row = pixels[y];
      if (!row) continue;
      for (let x = 0; x < mw; x++) {
        const pxl = row[x];
        if (!pxl) continue;
        const [rr, gg, bb] = pxl;

        const px = ox + spacing + x * (cell + spacing);
        const py = oy + spacing + y * (cell + spacing);

        ctx.fillStyle = `rgb(${rr},${gg},${bb})`;
        ctx.shadowColor = `rgba(${rr},${gg},${bb},${0.25 + glow * 0.45})`;

        if (isCircle) {
          ctx.beginPath();
          ctx.arc(px + r, py + r, Math.max(1, r - 0.3), 0, Math.PI * 2);
          ctx.fill();
        } else {
          ctx.fillRect(px, py, cell, cell);
        }
      }
    }
  }

  ctx.shadowBlur = 0;
}

function connectWs() {
  state.wsGen += 1;
  const myGen = state.wsGen;
  if (state.reconnectTimer) {
    window.clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
  }

  // Close previous socket without triggering a reconnect loop.
  if (state.ws) {
    try {
      state.ws.onopen = null;
      state.ws.onclose = null;
      state.ws.onerror = null;
      state.ws.onmessage = null;
      state.ws.close();
    } catch (_) {}
  }

  setConnectionStatus("connecting…");
  const ws = new WebSocket(wsUrl());
  ws.binaryType = "arraybuffer";
  state.ws = ws;

  ws.onopen = () => {
    if (myGen !== state.wsGen) return;
    state.connected = true;
    setConnectionStatus("connected");
  };

  ws.onclose = () => {
    if (myGen !== state.wsGen) return;
    state.connected = false;
    setConnectionStatus("disconnected");
    // Reconnect with a gentle backoff.
    state.reconnectTimer = window.setTimeout(connectWs, 800);
  };

  ws.onerror = () => {
    if (myGen !== state.wsGen) return;
    setConnectionStatus("error");
  };

  ws.onmessage = (ev) => {
    if (myGen !== state.wsGen) return;

    if (ev.data instanceof ArrayBuffer) {
      const buf = new Uint8Array(ev.data);
      if (buf.length < 8) return;
      const w = buf[0] | (buf[1] << 8);
      const h = buf[2] | (buf[3] << 8);
      const seq = (buf[4]) | (buf[5] << 8) | (buf[6] << 16) | (buf[7] << 24);
      const rgb = buf.subarray(8);
      state.lastFrame = { width: w, height: h, rgb };
      drawFrame(state.lastFrame);
      setText("statusSeq", String(seq));
      return;
    }

    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch (e) {
      return;
    }

    if (msg.type === "status") {
      // Status messages are partial. Merge into the last full settings object.
      const base = state.lastSettings && typeof state.lastSettings === "object" ? state.lastSettings : {};
      const mergedStatus = deepMerge(base, msg.settings || {});
      state.lastSettings = mergedStatus;
      const overlay = effectiveOverlayPatch();
      const merged = overlay ? deepMerge(mergedStatus, overlay) : mergedStatus;
      applySettingsToUI(merged);
      if (!msg.settings?.running) {
        // On stop, visually freeze/clear so "stopped" is obvious.
        clearCanvas();
        setText("statusSeq", "—");
      }
      return;
    }

    if (msg.type === "settings") {
      // Server-authoritative settings broadcast (sent only when changed).
      // Merge so partial WS payloads (e.g. no integrations block) don't wipe UI-only fields.
      const base = state.lastSettings && typeof state.lastSettings === "object" ? state.lastSettings : {};
      const s = deepMerge(base, msg.settings || {});
      state.lastSettings = s;
      const overlay = effectiveOverlayPatch();
      const merged = overlay ? deepMerge(s, overlay) : s;
      applySettingsToUI(merged);
      return;
    }

    // JSON frames are no longer used; frames arrive as binary ArrayBuffer.
  };
}

function lockWhileInteracting(id, el) {
  if (!el) return;
  const lock = () => state.uiLocks.add(id);
  const unlock = () => state.uiLocks.delete(id);
  const markEdit = () => state.lastLocalEditAt.set(id, Date.now());

  el.addEventListener("pointerdown", lock);
  el.addEventListener("pointerup", unlock);
  el.addEventListener("pointercancel", unlock);
  el.addEventListener("blur", unlock);
  el.addEventListener("focus", lock);
  el.addEventListener("input", markEdit);
  el.addEventListener("change", markEdit);
}

function clampNumber(n, min, max) {
  if (Number.isNaN(n)) return min;
  return Math.min(max, Math.max(min, n));
}

function animateClick(el) {
  if (!el) return;
  el.classList.remove("clicked");
  // Force reflow so repeated clicks retrigger animation.
  void el.offsetWidth;
  el.classList.add("clicked");
  window.setTimeout(() => el.classList.remove("clicked"), 170);
}

function wireUi() {
  // Prevent server updates from snapping active controls back while editing.
  lockWhileInteracting("selPattern", $("selPattern"));
  lockWhileInteracting("rngBrightness", $("rngBrightness"));
  lockWhileInteracting("rngSpeed", $("rngSpeed"));
  lockWhileInteracting("numFps", $("numFps"));
  lockWhileInteracting("selAutoFps", $("selAutoFps"));
  lockWhileInteracting("numMaxFps", $("numMaxFps"));
  lockWhileInteracting("selAutoLearn", $("selAutoLearn"));
  lockWhileInteracting("selMatrixPreset", $("selMatrixPreset"));
  lockWhileInteracting("selLedShape", $("selLedShape"));
  lockWhileInteracting("rngLedSpacing", $("rngLedSpacing"));
  lockWhileInteracting("rngGlow", $("rngGlow"));
  lockWhileInteracting("txtOpenAiModel", $("txtOpenAiModel"));
  lockWhileInteracting("clrLine", $("clrLine"));
  lockWhileInteracting("numDrawPps", $("numDrawPps"));
  lockWhileInteracting("numHold", $("numHold"));
  lockWhileInteracting("numErasePps", $("numErasePps"));
  lockWhileInteracting("selToolpathSource", $("selToolpathSource"));

  $("btnStart")?.addEventListener("click", async () => {
    animateClick($("btnStart"));
    try {
      const updated = await apiPost("/api/control/start");
      state.lastSettings = updated;
      applySettingsToUI(updated);
      // Ensure frames resume immediately even if ws stalled.
      if (!state.connected) connectWs();
    } catch (e) { console.error(e); }
  });
  $("btnStop")?.addEventListener("click", async () => {
    animateClick($("btnStop"));
    try {
      const updated = await apiPost("/api/control/stop");
      state.lastSettings = updated;
      applySettingsToUI(updated);
      // Hard stop: discard any previous frame buffer so the next start redraws
      // from scratch with the latest matrix/settings.
      state.lastFrame = null;
      clearCanvas();
      setText("statusSeq", "—");
    } catch (e) { console.error(e); }
  });

  $("btnSaveOpenAiKey")?.addEventListener("click", () => {
    const v = ($("inpOpenAiApiKey")?.value || "").trim();
    if (!v) {
      const hint = $("txtOpenAiKeyHint");
      if (hint) hint.textContent = "Type an API key, then click Save key.";
      return;
    }
    stageOrSendSettingsPatch({ integrations: { openai: { api_key: v } } }, { immediate: true });
    if ($("inpOpenAiApiKey")) $("inpOpenAiApiKey").value = "";
  });
  $("btnClearOpenAiKey")?.addEventListener("click", () => {
    stageOrSendSettingsPatch({ integrations: { openai: { api_key: "" } } }, { immediate: true });
    if ($("inpOpenAiApiKey")) $("inpOpenAiApiKey").value = "";
  });
  $("txtOpenAiModel")?.addEventListener("change", (e) => {
    state.lastLocalEditAt.set("txtOpenAiModel", Date.now());
    stageOrSendSettingsPatch({ integrations: { openai: { model: e.target.value.trim() } } });
  });

  // Settings panel was removed; settings now live on /settings.html

  $("selPattern")?.addEventListener("change", (e) => {
    state.lastLocalEditAt.set("selPattern", Date.now());
    // Pattern switches should feel instant; trigger transition immediately.
    stageOrSendSettingsPatch({ art: { pattern: e.target.value } }, { immediate: true });
    syncPhotoLineDrawingUi({ art: { pattern: e.target.value } });
  });
  $("rngBrightness")?.addEventListener("input", (e) => {
    const v = clampNumber(Number(e.target.value), 0, 1);
    if ($("txtBrightness")) $("txtBrightness").textContent = v.toFixed(2);
    state.lastLocalEditAt.set("rngBrightness", Date.now());
    stageOrSendSettingsPatch({ art: { brightness: v } });
  });
  $("rngSpeed")?.addEventListener("input", (e) => {
    const v = clampNumber(Number(e.target.value), 0, 5);
    if ($("txtSpeed")) $("txtSpeed").textContent = v.toFixed(2);
    state.lastLocalEditAt.set("rngSpeed", Date.now());
    stageOrSendSettingsPatch({ art: { speed: v } });
  });
  $("numFps")?.addEventListener("change", (e) => {
    const v = clampNumber(Number(e.target.value), 1, 120);
    e.target.value = String(v);
    state.lastLocalEditAt.set("numFps", Date.now());
    stageOrSendSettingsPatch({ stream: { fps: v } });
  });
  $("selAutoFps")?.addEventListener("change", (e) => {
    state.lastLocalEditAt.set("selAutoFps", Date.now());
    const on = e.target.value === "true";
    stageOrSendSettingsPatch({ stream: { auto_fps: on } }, { immediate: true });
  });
  $("numMaxFps")?.addEventListener("change", (e) => {
    const v = clampNumber(Number(e.target.value), 1, 120);
    e.target.value = String(v);
    state.lastLocalEditAt.set("numMaxFps", Date.now());
    stageOrSendSettingsPatch({ stream: { max_fps: v } }, { immediate: true });
  });
  $("selAutoLearn")?.addEventListener("change", (e) => {
    state.lastLocalEditAt.set("selAutoLearn", Date.now());
    const on = e.target.value === "true";
    stageOrSendSettingsPatch({ stream: { auto_learn: on } }, { immediate: true });
  });
  $("btnResetLearned")?.addEventListener("click", async () => {
    try {
      await apiPost("/api/perf/reset");
      // Refresh settings so learned cap display updates.
      const s = await apiGet("/api/settings");
      state.lastSettings = s;
      applySettingsToUI(s);
    } catch (e) {
      console.error(e);
    }
  });
  $("selMatrixPreset")?.addEventListener("change", (e) => {
    // Backend uses preset to set width/height.
    state.lastLocalEditAt.set("selMatrixPreset", Date.now());
    stageOrSendSettingsPatch({ matrix: { preset: e.target.value } }, { immediate: true });
    clearCanvas();
    // If the last frame was from a different matrix size, discard it to prevent “stuck at 8×8” perception.
    state.lastFrame = null;
  });
  $("selLedShape")?.addEventListener("change", (e) => {
    state.lastLocalEditAt.set("selLedShape", Date.now());
    stageOrSendSettingsPatch({ simulator: { led_shape: e.target.value } });
  });
  $("rngLedSpacing")?.addEventListener("input", (e) => {
    const v = clampNumber(Number(e.target.value), 0, 10);
    if ($("txtLedSpacing")) $("txtLedSpacing").textContent = String(v);
    state.lastLocalEditAt.set("rngLedSpacing", Date.now());
    stageOrSendSettingsPatch({ simulator: { led_spacing: v } });
  });
  $("rngGlow")?.addEventListener("input", (e) => {
    const v = clampNumber(Number(e.target.value), 0, 1);
    if ($("txtGlow")) $("txtGlow").textContent = v.toFixed(2);
    state.lastLocalEditAt.set("rngGlow", Date.now());
    stageOrSendSettingsPatch({ simulator: { glow: v } });
  });

  // Living drawing controls
  const fileUpload = $("fileUpload");
  if (fileUpload) {
    fileUpload.addEventListener("change", async (e) => {
      const f = e.target.files && e.target.files[0];
      if (!f) return;
      // Do not upload immediately; stage selection and wait for Save.
      state.pendingUpload = { file: f, name: f.name || "selected file" };
      setText("txtUploadPicked", state.pendingUpload.name);
      setUploadUiBusy(false);
    });
  }

  $("btnUploadClear")?.addEventListener("click", () => {
    state.pendingUpload = null;
    if ($("fileUpload")) $("fileUpload").value = "";
    setText("txtUploadPicked", "");
    showUploadProgress(false);
    setUploadUiBusy(false);
  });

  $("btnUploadSave")?.addEventListener("click", async () => {
    const pending = state.pendingUpload;
    if (!pending || !pending.file) return;
    showUploadProgress(true);
    setUploadIndeterminate("Uploading…");
    setUploadUiBusy(true);
    try {
      const uploaded = await apiUpload("/api/images/upload", pending.file, {
        label: ($("inpUploadLabel")?.value || "").trim(),
        crop_focus: ($("selCropFocus")?.value || "center").trim(),
      });

      const imageId = uploaded && uploaded.id ? uploaded.id : null;
      const jobId = uploaded && uploaded.job_id ? uploaded.job_id : null;
      if (jobId) {
        const done = await pollJob(jobId, {
          onUpdate: (j) => {
            const total = Math.max(1, Number(j.total || 1));
            const finished = Math.max(0, Number(j.done || 0));
            const pct = finished / total;
            const step = j.current ? ` · ${j.current}` : "";
            setUploadProgress(pct, `${j.status_label || "Converting presets"}${step}`);
          },
        });
        if (done && done.status === "error") throw new Error(done.error || "Upload job failed");
      } else if (uploaded && Array.isArray(uploaded.generated)) {
        // Backwards compatibility: if server still returns generated list.
        setUploadProgress(1, "Done");
      } else {
        setUploadIndeterminate("Processing…");
      }

      await refreshDrawingList(imageId);
      if ($("inpUploadLabel")) $("inpUploadLabel").value = "";
      state.pendingUpload = null;
      if ($("fileUpload")) $("fileUpload").value = "";
      setText("txtUploadPicked", "");
      setUploadProgress(1, "Done");
      await new Promise((r) => window.setTimeout(r, 450));
    } catch (err) {
      console.error(err);
      window.alert(String(err && err.message ? err.message : err));
    } finally {
      showUploadProgress(false);
      setUploadUiBusy(false);
    }
  });

  const btnUse = $("btnUseLivingDrawing");
  if (btnUse) {
    btnUse.addEventListener("click", async () => {
      const drawingId = $("selDrawing")?.value || null;
      const artPatch = { pattern: "living_drawing", drawing_id: drawingId };

      // These controls live on the Settings page now; only include if present.
      if ($("clrLine")) artPatch.line_color = $("clrLine").value || "#b8d7ff";
      if ($("selToolpathSource")) artPatch.toolpath_source = $("selToolpathSource").value || "auto";
      if ($("numDrawPps")) artPatch.draw_pps = Number($("numDrawPps").value || 250);
      if ($("numHold")) artPatch.hold_seconds = Number($("numHold").value || 4);
      if ($("numErasePps")) artPatch.erase_pps = Number($("numErasePps").value || 800);

    stageOrSendSettingsPatch({ art: artPatch }, { immediate: true });
    });
  }

  $("selToolpathSource")?.addEventListener("change", (e) => {
    state.lastLocalEditAt.set("selToolpathSource", Date.now());
    stageOrSendSettingsPatch({ art: { toolpath_source: e.target.value } }, { immediate: true });
  });

  const gen = async (source) => {
    const id = $("selDrawing")?.value || "";
    const status = $("txtRefineStatus");
    if (!id) {
      if (status) status.textContent = "Select an upload first.";
      return;
    }
    const w = state.lastSettings?.matrix?.width;
    const h = state.lastSettings?.matrix?.height;
    if (!w || !h) return;
    if (status) status.textContent = `Generating ${source} for ${w}×${h}…`;
    try {
      await apiPostDetailed(`/api/images/${encodeURIComponent(id)}/toolpaths/${w}x${h}/${source}/generate`, {});
      if (status) status.textContent = `Generated ${source} for ${w}×${h}.`;
      await refreshDrawingList(id);
    } catch (err) {
      console.error(err);
      if (status) status.textContent = String(err && err.message ? err.message : err);
    }
  };
  $("btnGenerateVectorized")?.addEventListener("click", () => { void gen("vectorized"); });
  // Edge generation removed (vectorized only).

  $("selDrawing")?.addEventListener("change", () => {
    syncRenameInputFromSelection();
    const id = $("selDrawing")?.value || null;
    // If living drawing is active, switching library selection should switch the drawing immediately.
    const isLiving = state.lastSettings?.art?.pattern === "living_drawing";
    if (isLiving) {
      stageOrSendSettingsPatch({ art: { drawing_id: id } }, { immediate: true });
    }

    // Update sidebar label immediately (without waiting for a server roundtrip)
    const nowPlaying = $("txtNowPlaying");
    if (nowPlaying && isLiving) {
      const opt = $("selDrawing")?.selectedOptions && $("selDrawing").selectedOptions[0];
      const lab = (opt && opt.dataset && opt.dataset.displayLabel) || id || "";
      if (lab) {
        const cur = nowPlaying.textContent || "Living drawing";
        const base = cur.split(" · ")[0] || "Living drawing";
        nowPlaying.textContent = `${base} · ${lab}`;
      }
    }
  });

  $("clrLine")?.addEventListener("input", (e) => {
    const v = String(e.target.value || "#b8d7ff");
    state.lastLocalEditAt.set("clrLine", Date.now());
    stageOrSendSettingsPatch({ art: { line_color: v } }, { immediate: true });
  });
  $("numDrawPps")?.addEventListener("change", (e) => {
    const v = clampNumber(Number(e.target.value), 10, 5000);
    e.target.value = String(v);
    state.lastLocalEditAt.set("numDrawPps", Date.now());
    stageOrSendSettingsPatch({ art: { draw_pps: v } });
  });
  $("numHold")?.addEventListener("change", (e) => {
    const v = clampNumber(Number(e.target.value), 0, 60);
    e.target.value = String(v);
    state.lastLocalEditAt.set("numHold", Date.now());
    stageOrSendSettingsPatch({ art: { hold_seconds: v } });
  });
  $("numErasePps")?.addEventListener("change", (e) => {
    const v = clampNumber(Number(e.target.value), 10, 20000);
    e.target.value = String(v);
    state.lastLocalEditAt.set("numErasePps", Date.now());
    stageOrSendSettingsPatch({ art: { erase_pps: v } });
  });

  const btnDelAi = $("btnDeleteAiToolpath");
  if (btnDelAi) {
    btnDelAi.addEventListener("click", async () => {
      const id = $("selDrawing")?.value || "";
      const status = $("txtRefineStatus");
      if (!id) {
        window.alert("Select an image in the library first.");
        return;
      }
      if (
        !window.confirm(
          "Remove the saved ChatGPT toolpath for this upload? The image stays; playback will use the local vectorized version (if available)."
        )
      ) {
        return;
      }
      try {
        const out = await apiDelete(`/api/images/${encodeURIComponent(id)}/toolpath`);
        if (status) {
          status.textContent = out.removed ? "AI toolpath removed." : "There was no AI toolpath to remove.";
        }
        await refreshDrawingList(id);
      } catch (err) {
        console.error(err);
        window.alert(String(err && err.message ? err.message : err));
      }
    });
  }

  const btnDelImg = $("btnDeleteImage");
  if (btnDelImg) {
    btnDelImg.addEventListener("click", async () => {
      const id = $("selDrawing")?.value || "";
      const status = $("txtRefineStatus");
      if (!id) {
        window.alert("Select an image in the library first.");
        return;
      }
      const lab =
        ($("selDrawing").selectedOptions && $("selDrawing").selectedOptions[0]?.dataset.displayLabel) || id;
      if (
        !window.confirm(
          `Permanently delete “${lab}”? This removes the file and any ChatGPT path. This cannot be undone.`
        )
      ) {
        return;
      }
      try {
        const out = await apiDelete(`/api/images/${encodeURIComponent(id)}`);
        if (out.settings) {
          state.lastSettings = out.settings;
          applySettingsToUI(out.settings);
        }
        if (status) status.textContent = "Upload deleted.";
        await refreshDrawingList(null);
      } catch (err) {
        console.error(err);
        window.alert(String(err && err.message ? err.message : err));
      }
    });
  }

  const btnRename = $("btnRenameDrawing");
  if (btnRename) {
    btnRename.addEventListener("click", async () => {
      const id = $("selDrawing")?.value || "";
      const nv = ($("inpRenameDrawing")?.value || "").trim();
      if (!id) {
        window.alert("Select an image in the library first.");
        return;
      }
      if (!nv) {
        window.alert("Enter a new display name.");
        return;
      }
      try {
        await apiPatch(`/api/images/${encodeURIComponent(id)}`, { label: nv });
        if ($("inpRenameDrawing")) $("inpRenameDrawing").value = "";
        await refreshDrawingList(id);
      } catch (err) {
        console.error(err);
        window.alert(String(err && err.message ? err.message : err));
      }
    });
  }

  const btnRefine = $("btnRefineToolpath");
  if (btnRefine) {
    btnRefine.addEventListener("click", async () => {
      const drawingId = $("selDrawing")?.value || "";
      const status = $("txtRefineStatus");
      if (!drawingId) {
        if (status) status.textContent = "Select an uploaded drawing first.";
        return;
      }
      if (status) status.textContent = "Calling ChatGPT… (may take ~30–120s)";
      btnRefine.disabled = true;
      try {
        const out = await apiPostDetailed(`/api/images/${encodeURIComponent(drawingId)}/refine-toolpath`, {});
        if (status) status.textContent = `Saved AI toolpath (${out.points} points).`;
        await refreshDrawingList(drawingId);
      } catch (e) {
        console.error(e);
        if (status) status.textContent = String(e && e.message ? e.message : e);
      } finally {
        btnRefine.disabled = false;
      }
    });
  }

  // Settings page commit/discard
  const btnCommit = $("btnCommitSettings");
  const btnDiscard = $("btnDiscardSettings");
  const commitBar = $("settingsCommitBar");
  if (btnCommit && btnDiscard && commitBar && isSettingsPage()) {
    // If we land on settings with no draft, ensure hidden.
    setCommitBarVisible(!!state.draftPatch);

    btnCommit.addEventListener("click", async () => {
      if (!state.draftPatch) return;
      btnCommit.disabled = true;
      btnDiscard.disabled = true;
      try {
        const updated = await apiPost("/api/settings", sanitizeSettingsPatchForApi(state.draftPatch));
        state.lastSettings = updated;
        state.draftPatch = null;
        setCommitBarVisible(false);
        applySettingsToUI(updated);
      } catch (e) {
        console.error(e);
        window.alert(String(e && e.message ? e.message : e));
      } finally {
        btnCommit.disabled = false;
        btnDiscard.disabled = false;
      }
    });

    btnDiscard.addEventListener("click", async () => {
      btnCommit.disabled = true;
      btnDiscard.disabled = true;
      try {
        state.draftPatch = null;
        setCommitBarVisible(false);
        const s = await apiGet("/api/settings");
        state.lastSettings = s;
        applySettingsToUI(s);
      } catch (e) {
        console.error(e);
      } finally {
        btnCommit.disabled = false;
        btnDiscard.disabled = false;
      }
    });
  }
}

async function loadInitialData() {
  const patterns = await apiGet("/api/patterns");
  state.patterns = patterns.patterns || [];
  const sel = $("selPattern");
  if (sel) {
    sel.innerHTML = "";
    for (const p of state.patterns) {
      const opt = document.createElement("option");
      opt.value = p.name;
      opt.textContent = p.display_name;
      sel.appendChild(opt);
    }
  }

  const settings = await apiGet("/api/settings");
  state.lastSettings = settings;
  applySettingsToUI(settings);

  await refreshDrawingList();
}

async function refreshDrawingList(selectedId) {
  const sel = $("selDrawing");
  if (!sel) return;
  const list = await apiGet("/api/images");
  const imgs = list.images || [];
  sel.innerHTML = "";

  const opt0 = document.createElement("option");
  opt0.value = "";
  // Use plain ASCII here; some fonts render ellipsis poorly in <select>.
  opt0.textContent = imgs.length ? "Select..." : "No uploads yet";
  sel.appendChild(opt0);

  for (const img of imgs) {
    const opt = document.createElement("option");
    opt.value = img.id;
    const tag = img.has_ai_toolpath ? " · AI" : "";
    const lab = (img.label || "").trim() || img.id;
    opt.textContent = `${lab}${tag}`;
    opt.title = `${img.filename || ""} · ${img.id}`;
    opt.dataset.displayLabel = lab;
    sel.appendChild(opt);
  }

  const desired =
    selectedId ||
    (state.lastSettings && state.lastSettings.art && state.lastSettings.art.drawing_id) ||
    "";
  if (desired) sel.value = desired;
  if (!sel.value) sel.selectedIndex = 0;
  syncRenameInputFromSelection();
}

function syncRenameInputFromSelection() {
  const sel = $("selDrawing");
  const inp = $("inpRenameDrawing");
  if (!sel || !inp) return;
  const opt = sel.selectedOptions && sel.selectedOptions[0];
  if (!opt || !opt.value) {
    inp.value = "";
    return;
  }
  inp.value = opt.dataset.displayLabel || "";
}

async function main() {
  initCanvas();
  clearCanvas();
  wireUi();
  try {
    await loadInitialData();
  } catch (e) {
    console.error(e);
  }
  connectWs();
}

main();

