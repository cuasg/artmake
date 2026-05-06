/* global window, document, fetch */

const $ = (id) => document.getElementById(id);

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
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : `POST ${path} failed (${res.status})`);
  }
  return data;
}

function drawToolpathPreview(canvas, toolpath) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "rgb(8,10,14)";
  ctx.fillRect(0, 0, w, h);

  const mx = toolpath?.matrix;
  const mw = mx?.width || toolpath?.raw?.width || 0;
  const mh = mx?.height || toolpath?.raw?.height || 0;
  if (!mw || !mh) return;

  const strokes = toolpath.expanded_strokes;
  const pts = toolpath.expanded_points;

  const scale = Math.min(w / mw, h / mh);
  const ox = (w - mw * scale) / 2;
  const oy = (h - mh * scale) / 2;

  ctx.strokeStyle = "rgba(160, 210, 255, 0.95)";
  ctx.lineWidth = Math.max(1, scale * 0.12);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  const drawStroke = (arr) => {
    if (!Array.isArray(arr) || arr.length < 2) return;
    ctx.beginPath();
    for (let i = 0; i < arr.length; i++) {
      const p = arr[i];
      if (!p || p.length !== 2) continue;
      const x = ox + (p[0] + 0.5) * scale;
      const y = oy + (p[1] + 0.5) * scale;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  };

  if (Array.isArray(strokes) && strokes.length) {
    for (const s of strokes) drawStroke(s);
  } else if (Array.isArray(pts) && pts.length) {
    drawStroke(pts);
  }
}

function canvasToPngBlob(canvas) {
  return new Promise((resolve) => {
    canvas.toBlob((b) => resolve(b), "image/png");
  });
}

async function copyCanvasToClipboard(canvas) {
  const blob = await canvasToPngBlob(canvas);
  if (!blob) throw new Error("Could not encode PNG.");
  if (!navigator.clipboard || !window.ClipboardItem) {
    throw new Error("Clipboard image copy not supported in this browser.");
  }
  await navigator.clipboard.write([new window.ClipboardItem({ "image/png": blob })]);
}

function wireModal() {
  const modal = $("previewModal");
  const btnClose = $("btnClosePreview");
  const btnCopy = $("btnCopyPreview");
  const hint = $("previewHint");
  const canvas = $("previewCanvas");

  const close = () => {
    if (!modal) return;
    modal.hidden = true;
  };
  btnClose?.addEventListener("click", close);
  modal?.addEventListener("click", (e) => {
    if (e.target === modal) close();
  });
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && modal && !modal.hidden) close();
  });

  btnCopy?.addEventListener("click", async () => {
    if (!canvas) return;
    try {
      btnCopy.disabled = true;
      if (hint) hint.textContent = "Copying…";
      await copyCanvasToClipboard(canvas);
      if (hint) hint.textContent = "Copied PNG to clipboard.";
    } catch (err) {
      if (hint) hint.textContent = String(err && err.message ? err.message : err);
    } finally {
      btnCopy.disabled = false;
    }
  });

  return {
    open: (title, toolpath) => {
      if (!modal || !canvas) return;
      const t = $("previewTitle");
      if (t) t.textContent = title || "Preview";
      if (hint) hint.textContent = "Tip: Copy puts a PNG in your clipboard.";
      modal.hidden = false;
      // render at higher pixel res for nicer copy
      canvas.width = 900;
      canvas.height = 900;
      drawToolpathPreview(canvas, toolpath);
    },
  };
}

async function applyVariantAndGoHome(imageId, w, h) {
  const preset = `${w}x${h}`;
  await apiPost("/api/settings", {
    matrix: { preset },
    art: { pattern: "living_drawing", drawing_id: imageId },
  });
  window.location.href = "/";
}

async function main() {
  const status = $("galleryStatus");
  const grid = $("galleryGrid");
  if (!grid) return;

  const modal = wireModal();

  try {
    const list = await apiGet("/api/images");
    const imgs = list.images || [];
    if (status) status.textContent = imgs.length ? `${imgs.length} uploads` : "No uploads yet";

    grid.innerHTML = "";
    for (const img of imgs) {
      const card = document.createElement("div");
      card.className = "galleryCard";

      const thumb = document.createElement("img");
      thumb.className = "galleryThumb";
      thumb.src = `/api/images/${encodeURIComponent(img.id)}`;
      thumb.alt = img.label || img.id;
      card.appendChild(thumb);

      const titleRow = document.createElement("div");
      titleRow.className = "galleryTitleRow";
      const title = document.createElement("div");
      title.className = "galleryTitle";
      title.textContent = img.label || img.id;
      const meta = document.createElement("div");
      meta.className = "galleryMeta";
      meta.textContent = img.has_ai_toolpath ? "AI ready" : "Not optimized";
      titleRow.appendChild(title);
      titleRow.appendChild(meta);
      card.appendChild(titleRow);

      const variants = Array.isArray(img.toolpaths) ? img.toolpaths : [];
      if (!variants.length) {
        const m = document.createElement("div");
        m.className = "muted";
        m.style.marginTop = "10px";
        m.textContent = "No optimized versions yet. Use “Refine with ChatGPT” in the simulator.";
        card.appendChild(m);
      } else {
        const listEl = document.createElement("div");
        listEl.className = "variantList";
        for (const v of variants) {
          const row = document.createElement("div");
          row.className = "variantRow";

          const cv = document.createElement("canvas");
          cv.className = "variantCanvas";
          cv.width = 72;
          cv.height = 72;
          row.appendChild(cv);

          const info = document.createElement("div");
          info.className = "variantInfo";
          const lab = document.createElement("div");
          lab.className = "galleryTitle";
          lab.textContent = `${v.w}×${v.h} · ${v.source || "ai"}`;
          const sub = document.createElement("div");
          sub.className = "muted";
          sub.textContent = `${v.strokes || 1} stroke(s), ${v.points || 0} pts`;
          info.appendChild(lab);
          info.appendChild(sub);

          const actions = document.createElement("div");
          actions.className = "variantActions";
          const btn = document.createElement("button");
          btn.className = "btn primary";
          btn.type = "button";
          btn.textContent = "Preview in Simulator";
          btn.addEventListener("click", () => {
            void applyVariantAndGoHome(img.id, v.w, v.h);
          });
          actions.appendChild(btn);
          info.appendChild(actions);
          row.appendChild(info);

          // Load toolpath JSON for preview render
          try {
            const tp = await apiGet(`/api/images/${encodeURIComponent(img.id)}/toolpaths/${v.w}x${v.h}/${encodeURIComponent(v.source || "ai")}`);
            drawToolpathPreview(cv, tp);
            cv.style.cursor = "pointer";
            cv.title = "Click to open large preview (copyable)";
            cv.addEventListener("click", () => {
              modal?.open(`${img.label || img.id} · ${v.w}×${v.h} · ${v.source || "ai"}`, tp);
            });
          } catch (_) {
            // leave blank
          }

          listEl.appendChild(row);
        }
        card.appendChild(listEl);
      }

      grid.appendChild(card);
    }
  } catch (e) {
    if (status) status.textContent = String(e && e.message ? e.message : e);
  }
}

main();

