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

async function apiPatch(path, body) {
  const res = await fetch(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : `PATCH ${path} failed (${res.status})`);
  }
  return data;
}

async function apiDelete(path) {
  const res = await fetch(path, { method: "DELETE" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : `DELETE ${path} failed (${res.status})`);
  }
  return data;
}

const galleryState = {
  pendingUpload: null, // { file: File, name: string, previewUrl: string }
};

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

function showUploadProgress(show) {
  const wrap = $("uploadProgressWrap");
  if (wrap) wrap.hidden = !show;
}

function setUploadUiBusy(on) {
  const btnSave = $("btnUploadSave");
  const btnClear = $("btnUploadClear");
  const file = $("fileUpload");
  const label = $("inpUploadLabel");
  const crop = $("selCropFocus");
  if (btnSave) btnSave.disabled = on || !galleryState.pendingUpload;
  if (btnClear) btnClear.disabled = on || !galleryState.pendingUpload;
  if (file) file.disabled = !!on;
  if (label) label.disabled = !!on;
  if (crop) crop.disabled = !!on;
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
    const age = Date.now() - start;
    const delay = age < 4000 ? 350 : age < 15000 ? 650 : 1000;
    await new Promise((r) => window.setTimeout(r, delay));
  }
}

const PRESETS = [
  { w: 32, h: 32, label: "32×32" },
  { w: 64, h: 64, label: "64×64" },
  { w: 64, h: 96, label: "64×96" },
];

async function generateLocal(imageId, w, h, source) {
  return await apiPost(
    `/api/images/${encodeURIComponent(imageId)}/toolpaths/${w}x${h}/${encodeURIComponent(source)}/generate`,
    {}
  );
}

async function generateAi(imageId, w, h) {
  return await apiPost(`/api/images/${encodeURIComponent(imageId)}/toolpaths/${w}x${h}/ai/generate`, {});
}

async function generateAiLineartImage(imageId) {
  return await apiPost(`/api/images/${encodeURIComponent(imageId)}/ai-stylize`, {});
}

function hexToRgba(hex, alpha = 1) {
  try {
    let t = String(hex || "").trim();
    if (t.startsWith("#")) t = t.slice(1);
    if (t.length === 3) t = t.split("").map((c) => c + c).join("");
    if (t.length !== 6) return `rgba(184, 215, 255, ${alpha})`;
    const r = parseInt(t.slice(0, 2), 16);
    const g = parseInt(t.slice(2, 4), 16);
    const b = parseInt(t.slice(4, 6), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  } catch (_) {
    return `rgba(184, 215, 255, ${alpha})`;
  }
}

/** Catalog line_art_display_color: null = simulator default; random_bright = preview tint */
function resolveLineArtStrokeCss(spec, globalLineHex) {
  if (spec == null || String(spec).trim() === "") return hexToRgba(globalLineHex || "#b8d7ff", 0.95);
  if (String(spec).toLowerCase().trim() === "random_bright") return "rgba(72, 255, 168, 0.95)";
  const h = String(spec).startsWith("#") ? String(spec) : `#${spec}`;
  return hexToRgba(h, 0.95);
}

function drawToolpathPreview(canvas, toolpath, strokeCss) {
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

  ctx.strokeStyle = strokeCss || "rgba(160, 210, 255, 0.95)";
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

async function copyImageUrlToClipboard(url) {
  if (!navigator.clipboard || !window.ClipboardItem) {
    throw new Error("Clipboard image copy not supported in this browser.");
  }
  const res = await fetch(url, { credentials: "same-origin" });
  if (!res.ok) throw new Error(`Could not load image (${res.status}).`);
  const blob = await res.blob();
  const type = blob.type && blob.type.startsWith("image/") ? blob.type : "image/png";
  await navigator.clipboard.write([new window.ClipboardItem({ [type]: blob })]);
}

function wireModal() {
  const modal = $("previewModal");
  const btnClose = $("btnClosePreview");
  const btnCopy = $("btnCopyPreview");
  const hint = $("previewHint");
  const canvas = $("previewCanvas");
  const raster = $("previewRaster");

  /** @type {"toolpath"|"raster"} */
  let previewMode = "toolpath";
  let rasterCopyUrl = "";

  const close = () => {
    if (!modal) return;
    modal.hidden = true;
    previewMode = "toolpath";
    rasterCopyUrl = "";
    if (raster) {
      raster.hidden = true;
      raster.removeAttribute("src");
    }
    if (canvas) canvas.hidden = false;
  };
  btnClose?.addEventListener("click", close);
  modal?.addEventListener("click", (e) => {
    if (e.target === modal) close();
  });
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && modal && !modal.hidden) close();
  });

  btnCopy?.addEventListener("click", async () => {
    try {
      btnCopy.disabled = true;
      if (hint) hint.textContent = "Copying…";
      if (previewMode === "raster" && rasterCopyUrl) {
        await copyImageUrlToClipboard(rasterCopyUrl);
      } else if (canvas) {
        await copyCanvasToClipboard(canvas);
      }
      if (hint) hint.textContent = "Copied image to clipboard.";
    } catch (err) {
      if (hint) hint.textContent = String(err && err.message ? err.message : err);
    } finally {
      btnCopy.disabled = false;
    }
  });

  return {
    openToolpath: (title, toolpath, strokeCss) => {
      if (!modal || !canvas) return;
      previewMode = "toolpath";
      rasterCopyUrl = "";
      if (raster) {
        raster.hidden = true;
        raster.removeAttribute("src");
      }
      canvas.hidden = false;
      const t = $("previewTitle");
      if (t) t.textContent = title || "Preview";
      if (hint) hint.textContent = "Tip: Copy saves the preview image to your clipboard.";
      modal.hidden = false;
      canvas.width = 900;
      canvas.height = 900;
      drawToolpathPreview(canvas, toolpath, strokeCss);
    },
    openRaster: (title, imageUrl) => {
      if (!modal || !canvas || !raster) return;
      previewMode = "raster";
      rasterCopyUrl = imageUrl || "";
      canvas.hidden = true;
      raster.hidden = false;
      raster.alt = title || "Preview";
      raster.src = imageUrl;
      const t = $("previewTitle");
      if (t) t.textContent = title || "Preview";
      if (hint) hint.textContent = "Tip: Copy saves this image to your clipboard.";
      modal.hidden = false;
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

  let globalLineColor = "#b8d7ff";
  try {
    const st = await apiGet("/api/settings");
    const lc = st && st.art && st.art.line_color;
    if (typeof lc === "string" && lc.trim()) globalLineColor = lc.trim();
  } catch (_) {
    /* keep default */
  }

  // Upload workflow (staged save + progress)
  const fileEl = $("fileUpload");
  if (fileEl) {
    fileEl.addEventListener("change", (e) => {
      const f = e.target.files && e.target.files[0];
      if (!f) return;
      if (galleryState.pendingUpload && galleryState.pendingUpload.previewUrl) {
        try { URL.revokeObjectURL(galleryState.pendingUpload.previewUrl); } catch (_) {}
      }
      const url = URL.createObjectURL(f);
      galleryState.pendingUpload = { file: f, name: f.name || "selected file", previewUrl: url };
      const img = $("imgUploadPreview");
      if (img) img.src = url;
      const picked = $("txtUploadPicked");
      if (picked) picked.textContent = galleryState.pendingUpload.name;
      setUploadUiBusy(false);
    });
  }

  $("btnUploadClear")?.addEventListener("click", () => {
    if (galleryState.pendingUpload && galleryState.pendingUpload.previewUrl) {
      try { URL.revokeObjectURL(galleryState.pendingUpload.previewUrl); } catch (_) {}
    }
    galleryState.pendingUpload = null;
    if ($("fileUpload")) $("fileUpload").value = "";
    if ($("imgUploadPreview")) $("imgUploadPreview").src = "";
    if ($("txtUploadPicked")) $("txtUploadPicked").textContent = "";
    showUploadProgress(false);
    setUploadUiBusy(false);
  });

  $("btnUploadSave")?.addEventListener("click", async () => {
    const pending = galleryState.pendingUpload;
    if (!pending || !pending.file) return;
    showUploadProgress(true);
    setUploadIndeterminate("Uploading…");
    setUploadUiBusy(true);
    try {
      const uploaded = await apiUpload("/api/images/upload", pending.file, {
        label: ($("inpUploadLabel")?.value || "").trim(),
        crop_focus: ($("selCropFocus")?.value || "center").trim(),
      });
      const jobId = uploaded && uploaded.job_id ? uploaded.job_id : null;
      if (jobId) {
        const done = await pollJob(jobId, {
          onUpdate: (j) => {
            const total = Math.max(1, Number(j.total || 1));
            const finished = Math.max(0, Number(j.done || 0));
            const pct = finished / total;
            const step = j.current ? ` · ${j.current}` : "";
            setUploadProgress(pct, `${j.status_label || "Generating presets"}${step}`);
          },
        });
        if (done && done.status === "error") throw new Error(done.error || "Upload job failed");
      } else {
        setUploadProgress(1, "Done");
      }

      if ($("inpUploadLabel")) $("inpUploadLabel").value = "";
      $("btnUploadClear")?.click();
      window.location.reload();
    } catch (e) {
      window.alert(String(e && e.message ? e.message : e));
    } finally {
      showUploadProgress(false);
      setUploadUiBusy(false);
    }
  });

  try {
    const list = await apiGet("/api/images");
    const imgsRaw = list.images || [];
    const imgs = Array.isArray(imgsRaw) ? imgsRaw : [];

    const byId = new Map();
    for (const it of imgs) byId.set(it.id, it);
    const children = new Map(); // parent_id -> [child]
    for (const it of imgs) {
      if (it && it.parent_id) {
        if (!children.has(it.parent_id)) children.set(it.parent_id, []);
        children.get(it.parent_id).push(it);
      }
    }

    const roots = imgs.filter((it) => !it.parent_id);
    if (status) status.textContent = roots.length ? `${roots.length} uploads` : "No uploads yet";

    grid.innerHTML = "";
    for (const img of roots) {
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
      const kind = (img.kind || "original").toString();
      meta.textContent = kind === "ai_lineart" ? "AI line art" : (img.has_ai_toolpath ? "AI ready" : "Not optimized");
      titleRow.appendChild(title);
      titleRow.appendChild(meta);
      card.appendChild(titleRow);

      // If AI line-art exists, prefer vectorizing/previewing that derived image.
      const kids = children.get(img.id) || [];
      const aiKids = kids.filter((k) => (k.kind || "") === "ai_lineart");
      const vectorSource = aiKids.length ? aiKids[0] : img;

      // Rename / delete controls
      const manageRow = document.createElement("div");
      manageRow.className = "btnRow";
      manageRow.style.marginTop = "10px";

      const inpName = document.createElement("input");
      inpName.type = "text";
      inpName.placeholder = "Rename…";
      inpName.value = (img.label || "").toString();
      inpName.className = "btn";
      inpName.style.flex = "1";
      inpName.style.minWidth = "0";
      inpName.style.cursor = "text";
      inpName.style.padding = "8px 10px";
      manageRow.appendChild(inpName);

      const btnRename = document.createElement("button");
      btnRename.className = "btn";
      btnRename.type = "button";
      btnRename.textContent = "Rename";
      btnRename.addEventListener("click", async () => {
        const v = (inpName.value || "").trim();
        if (!v) return;
        btnRename.disabled = true;
        try {
          await apiPatch(`/api/images/${encodeURIComponent(img.id)}`, { label: v });
          window.location.reload();
        } catch (e) {
          window.alert(String(e && e.message ? e.message : e));
        } finally {
          btnRename.disabled = false;
        }
      });
      manageRow.appendChild(btnRename);

      const btnDelete = document.createElement("button");
      btnDelete.className = "btn danger";
      btnDelete.type = "button";
      btnDelete.textContent = "Delete";
      btnDelete.addEventListener("click", async () => {
        if (!window.confirm(`Delete “${img.label || img.id}” and all its generated files?`)) return;
        btnDelete.disabled = true;
        try {
          await apiDelete(`/api/images/${encodeURIComponent(img.id)}`);
          window.location.reload();
        } catch (e) {
          window.alert(String(e && e.message ? e.message : e));
        } finally {
          btnDelete.disabled = false;
        }
      });
      manageRow.appendChild(btnDelete);

      const btnDeleteVariants = document.createElement("button");
      btnDeleteVariants.className = "btn danger";
      btnDeleteVariants.type = "button";
      btnDeleteVariants.textContent = "Delete all variants";
      btnDeleteVariants.addEventListener("click", async () => {
        const who = vectorSource.id === img.id ? (img.label || img.id) : (vectorSource.label || vectorSource.id);
        if (!window.confirm(`Delete all toolpath variants for “${who}”?`)) return;
        btnDeleteVariants.disabled = true;
        try {
          await apiDelete(`/api/images/${encodeURIComponent(vectorSource.id)}/toolpath`);
          window.location.reload();
        } catch (e) {
          window.alert(String(e && e.message ? e.message : e));
        } finally {
          btnDeleteVariants.disabled = false;
        }
      });
      manageRow.appendChild(btnDeleteVariants);
      card.appendChild(manageRow);

      // AI stylized line-art image (creates a derived gallery item)
      if ((img.kind || "original") === "original") {
        const aiRow = document.createElement("div");
        aiRow.className = "btnRow";
        aiRow.style.marginTop = "10px";
        aiRow.style.alignItems = "center";

        const aiBtn = document.createElement("button");
        aiBtn.className = "btn primary";
        aiBtn.type = "button";
        aiBtn.textContent = "Generate AI line-art image";

        const aiStatus = document.createElement("div");
        aiStatus.className = "muted";
        aiStatus.style.marginTop = "8px";

        aiBtn.addEventListener("click", async () => {
          aiBtn.disabled = true;
          aiStatus.textContent = "Generating AI line-art… (can take ~30–120s)";
          try {
            await generateAiLineartImage(img.id);
            aiStatus.textContent = "Saved AI line-art. Refreshing…";
            window.location.reload();
          } catch (e) {
            aiStatus.textContent = String(e && e.message ? e.message : e);
          } finally {
            aiBtn.disabled = false;
          }
        });

        aiRow.appendChild(aiBtn);
        card.appendChild(aiRow);
        card.appendChild(aiStatus);
      }

      // Show derived AI line-art thumbnail(s) inside this card
      if (aiKids.length) {
        const block = document.createElement("div");
        block.className = "settingsGroup";
        block.style.marginTop = "10px";

        const t = document.createElement("div");
        t.className = "groupTitle";
        t.textContent = "AI line art";
        block.appendChild(t);

        for (const kid of aiKids) {
          const row = document.createElement("div");
          row.className = "variantRow";

          const im = document.createElement("img");
          // Use the same sizing as other variant previews to avoid layout overlap.
          im.className = "variantCanvas";
          im.src = `/api/images/${encodeURIComponent(kid.id)}`;
          im.alt = kid.label || kid.id;
          im.style.cursor = "pointer";
          im.title = "Click to enlarge";
          im.addEventListener("click", () => {
            const name = kid.label || "AI line art";
            modal?.openRaster(`${name} · AI line-art image`, im.src);
          });
          row.appendChild(im);

          const info = document.createElement("div");
          info.className = "variantInfo";
          const ttl = document.createElement("div");
          ttl.className = "galleryTitle";
          ttl.textContent = kid.label || "AI line art";
          const sub = document.createElement("div");
          sub.className = "muted";
          sub.textContent = kid.id;
          info.appendChild(ttl);
          info.appendChild(sub);

          const actions = document.createElement("div");
          actions.className = "variantActions";

          const btnUse = document.createElement("button");
          btnUse.className = "btn primary";
          btnUse.type = "button";
          btnUse.textContent = "Preview in Simulator";
          btnUse.addEventListener("click", () => {
            void applyVariantAndGoHome(kid.id, 64, 96);
          });
          actions.appendChild(btnUse);

          const btnDelKid = document.createElement("button");
          btnDelKid.className = "btn danger";
          btnDelKid.type = "button";
          btnDelKid.textContent = "Delete AI";
          btnDelKid.addEventListener("click", async () => {
            if (!window.confirm(`Delete AI line-art “${kid.label || kid.id}”?`)) return;
            btnDelKid.disabled = true;
            try {
              await apiDelete(`/api/images/${encodeURIComponent(kid.id)}`);
              window.location.reload();
            } catch (e) {
              window.alert(String(e && e.message ? e.message : e));
            } finally {
              btnDelKid.disabled = false;
            }
          });
          actions.appendChild(btnDelKid);

          info.appendChild(actions);
          row.appendChild(info);
          block.appendChild(row);
        }

        card.appendChild(block);
      }

      // Crop focus controls
      const cropRow = document.createElement("div");
      cropRow.className = "btnRow";
      cropRow.style.marginTop = "10px";
      cropRow.style.alignItems = "center";

      const cropLab = document.createElement("div");
      cropLab.className = "muted";
      cropLab.textContent = "Crop focus:";
      cropRow.appendChild(cropLab);

      const selCrop = document.createElement("select");
      selCrop.className = "gallerySelect";
      selCrop.setAttribute("aria-label", "Crop focus");
      const opts = [
        ["center", "Center"],
        ["left", "Left"],
        ["right", "Right"],
        ["top", "Top"],
        ["bottom", "Bottom"],
      ];
      for (const [v, t] of opts) {
        const o = document.createElement("option");
        o.value = v;
        o.textContent = t;
        selCrop.appendChild(o);
      }
      selCrop.value = (img.crop_focus || "center").toString();
      cropRow.appendChild(selCrop);

      const btnSaveCrop = document.createElement("button");
      btnSaveCrop.className = "btn";
      btnSaveCrop.type = "button";
      btnSaveCrop.textContent = "Save crop";
      btnSaveCrop.addEventListener("click", async () => {
        btnSaveCrop.disabled = true;
        try {
          await apiPost(`/api/images/${encodeURIComponent(img.id)}/crop-focus`, { crop_focus: selCrop.value });
          window.location.reload();
        } catch (e) {
          window.alert(String(e && e.message ? e.message : e));
        } finally {
          btnSaveCrop.disabled = false;
        }
      });
      cropRow.appendChild(btnSaveCrop);
      card.appendChild(cropRow);

      const variants = Array.isArray(vectorSource.toolpaths) ? vectorSource.toolpaths : [];

      // Line art color (simulator default vs fixed hex vs random bright — stored per image id)
      const colorRow = document.createElement("div");
      colorRow.className = "btnRow";
      colorRow.style.marginTop = "10px";
      colorRow.style.alignItems = "center";
      colorRow.style.flexWrap = "wrap";
      colorRow.style.gap = "8px";

      const colorLab = document.createElement("div");
      colorLab.className = "muted";
      colorLab.textContent = "Line color:";
      colorRow.appendChild(colorLab);

      const selMode = document.createElement("select");
      selMode.className = "gallerySelect";
      selMode.setAttribute("aria-label", "Line art color mode");
      const optInherit = document.createElement("option");
      optInherit.value = "inherit";
      optInherit.textContent = "Simulator default";
      const optRand = document.createElement("option");
      optRand.value = "random";
      optRand.textContent = "Random (bright)";
      const optCustom = document.createElement("option");
      optCustom.value = "custom";
      optCustom.textContent = "Custom…";
      selMode.appendChild(optInherit);
      selMode.appendChild(optRand);
      selMode.appendChild(optCustom);

      const inpCol = document.createElement("input");
      inpCol.type = "color";
      inpCol.className = "galleryColorInput";
      inpCol.setAttribute("aria-label", "Custom line art color");

      const lacRaw = vectorSource.line_art_display_color;
      const lacLower = lacRaw != null ? String(lacRaw).toLowerCase().trim() : "";
      if (!lacRaw || lacRaw === "") {
        selMode.value = "inherit";
        inpCol.value = /^#[0-9a-fA-F]{6}$/.test(globalLineColor) ? globalLineColor : "#b8d7ff";
      } else if (lacLower === "random_bright") {
        selMode.value = "random";
        inpCol.value = "#48ffa8";
      } else {
        selMode.value = "custom";
        const hx = String(lacRaw).startsWith("#") ? String(lacRaw) : `#${lacRaw}`;
        inpCol.value = hx.length === 7 ? hx : "#b8d7ff";
      }
      inpCol.disabled = selMode.value !== "custom";
      selMode.addEventListener("change", () => {
        inpCol.disabled = selMode.value !== "custom";
      });

      colorRow.appendChild(selMode);
      colorRow.appendChild(inpCol);

      const btnSaveColor = document.createElement("button");
      btnSaveColor.className = "btn primary";
      btnSaveColor.type = "button";
      btnSaveColor.textContent = "Save color";
      const colorStatus = document.createElement("div");
      colorStatus.className = "muted";
      colorStatus.style.flex = "1";
      colorStatus.style.minWidth = "120px";

      btnSaveColor.addEventListener("click", async () => {
        btnSaveColor.disabled = true;
        colorStatus.textContent = "";
        let payload = {};
        if (selMode.value === "inherit") payload = { line_art_display_color: null };
        else if (selMode.value === "random") payload = { line_art_display_color: "random_bright" };
        else payload = { line_art_display_color: inpCol.value };

        try {
          await apiPatch(`/api/images/${encodeURIComponent(vectorSource.id)}/line-art-display-color`, payload);
          vectorSource.line_art_display_color = payload.line_art_display_color;
          colorStatus.textContent = "Saved.";
          void renderSelected();
        } catch (e) {
          colorStatus.textContent = String(e && e.message ? e.message : e);
        } finally {
          btnSaveColor.disabled = false;
        }
      });
      colorRow.appendChild(btnSaveColor);
      colorRow.appendChild(colorStatus);
      card.appendChild(colorRow);

      // Generate controls (bulk presets)
      const genRow = document.createElement("div");
      genRow.className = "btnRow";
      genRow.style.marginTop = "10px";
      genRow.style.alignItems = "center";

      const genLabel = document.createElement("div");
      genLabel.className = "muted";
      genLabel.textContent = "Generate:";
      genRow.appendChild(genLabel);

      const statusLine = document.createElement("div");
      statusLine.className = "muted";
      statusLine.style.marginTop = "8px";

      const mkBtn = (text, primary = false) => {
        const b = document.createElement("button");
        b.className = primary ? "btn primary" : "btn";
        b.type = "button";
        b.textContent = text;
        return b;
      };

      const runBulk = async (kind) => {
        const btns = genRow.querySelectorAll("button");
        btns.forEach((b) => (b.disabled = true));
        statusLine.textContent = `Generating ${kind} presets…`;
        try {
          for (const p of PRESETS) {
            if (kind === "ai") await generateAi(img.id, p.w, p.h);
            else await generateLocal(vectorSource.id, p.w, p.h, kind);
          }
          statusLine.textContent = `Done generating ${kind} presets. Refreshing…`;
          window.location.reload();
        } catch (e) {
          statusLine.textContent = String(e && e.message ? e.message : e);
        } finally {
          btns.forEach((b) => (b.disabled = false));
        }
      };

      const btnVec = mkBtn("Vectorized (all presets)");
      btnVec.addEventListener("click", () => { void runBulk("vectorized"); });
      genRow.appendChild(btnVec);

      // Edge generation removed (vectorized only).

      const btnAi = mkBtn("AI (all presets)", true);
      btnAi.addEventListener("click", () => { void runBulk("ai"); });
      genRow.appendChild(btnAi);

      card.appendChild(genRow);
      card.appendChild(statusLine);
      const picker = document.createElement("div");
      picker.className = "variantRow";
      picker.style.marginTop = "10px";

      const cv = document.createElement("canvas");
      cv.className = "variantCanvas";
      cv.width = 72;
      cv.height = 72;
      picker.appendChild(cv);

      const info = document.createElement("div");
      info.className = "variantInfo";

      const top = document.createElement("div");
      top.className = "btnRow";

      const sel = document.createElement("select");
      sel.className = "gallerySelect";
      sel.style.maxWidth = "100%";
      sel.setAttribute("aria-label", "Matrix preset for preview");

      // Build options from presets; disable sizes that don't exist yet.
      const avail = new Set((variants || []).map((v) => `${v.w}x${v.h}`));
      for (const p of PRESETS) {
        const o = document.createElement("option");
        o.value = `${p.w}x${p.h}`;
        o.textContent = p.label;
        if (!avail.has(o.value)) o.disabled = true;
        sel.appendChild(o);
      }
      // Pick best default: 64x96 if available, else first available preset.
      const prefer = ["64x96", "64x64", "32x32", "16x16", "8x8", "128x128"];
      sel.value = prefer.find((x) => avail.has(x)) || (sel.querySelector("option:not([disabled])")?.value || "64x96");
      top.appendChild(sel);

      const txt = document.createElement("div");
      txt.className = "muted";
      txt.style.minWidth = "0";
      txt.style.flex = "1";
      top.appendChild(txt);

      info.appendChild(top);

      const actions = document.createElement("div");
      actions.className = "variantActions";

      const btnPreview = document.createElement("button");
      btnPreview.className = "btn primary";
      btnPreview.type = "button";
      btnPreview.textContent = "Preview in Simulator";
      actions.appendChild(btnPreview);

      const btnRegen = document.createElement("button");
      btnRegen.className = "btn";
      btnRegen.type = "button";
      btnRegen.textContent = "Regenerate";
      actions.appendChild(btnRegen);

      const btnDel = document.createElement("button");
      btnDel.className = "btn danger";
      btnDel.type = "button";
      btnDel.textContent = "Delete";
      actions.appendChild(btnDel);

      info.appendChild(actions);
      picker.appendChild(info);
      card.appendChild(picker);

      const strokeCss = () => resolveLineArtStrokeCss(vectorSource.line_art_display_color, globalLineColor);

      const renderSelected = async () => {
        const v = String(sel.value || "");
        const [wS, hS] = v.split("x");
        const w = Number(wS);
        const h = Number(hS);
        const metaV = (variants || []).find((vv) => `${vv.w}x${vv.h}` === v) || null;
        txt.textContent = metaV ? `${metaV.strokes || 1} stroke(s), ${metaV.points || 0} pts` : "—";
        btnPreview.onclick = () => { void applyVariantAndGoHome(vectorSource.id, w, h); };

        btnRegen.onclick = async () => {
          btnRegen.disabled = true;
          try {
            await generateLocal(vectorSource.id, w, h, "vectorized");
            window.location.reload();
          } catch (e) {
            window.alert(String(e && e.message ? e.message : e));
          } finally {
            btnRegen.disabled = false;
          }
        };

        btnDel.onclick = async () => {
          if (!window.confirm(`Delete ${w}×${h} · vectorized?`)) return;
          btnDel.disabled = true;
          try {
            await apiDelete(`/api/images/${encodeURIComponent(vectorSource.id)}/toolpaths/${w}x${h}/vectorized`);
            window.location.reload();
          } catch (e) {
            window.alert(String(e && e.message ? e.message : e));
          } finally {
            btnDel.disabled = false;
          }
        };

        try {
          const tp = await apiGet(`/api/images/${encodeURIComponent(vectorSource.id)}/toolpaths/${w}x${h}/vectorized`);
          drawToolpathPreview(cv, tp, strokeCss());
          cv.style.cursor = "pointer";
          cv.title = "Click to open large preview (copyable)";
          cv.onclick = () => {
            const name = vectorSource.label || vectorSource.id;
            modal?.openToolpath(`${name} · ${w}×${h} · vectorized`, tp, strokeCss());
          };
        } catch (_) {
          // leave blank
        }
      };

      sel.addEventListener("change", () => { void renderSelected(); });
      if (!variants.length) {
        txt.textContent = "No vectorized variants yet. Use Generate above.";
        sel.disabled = true;
        btnPreview.disabled = true;
        btnRegen.disabled = true;
        btnDel.disabled = true;
      } else {
        void renderSelected();
      }

      grid.appendChild(card);
    }
  } catch (e) {
    if (status) status.textContent = String(e && e.message ? e.message : e);
  }
}

main();

