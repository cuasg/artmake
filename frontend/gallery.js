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

const PRESETS = [
  { w: 8, h: 8, label: "8×8" },
  { w: 16, h: 16, label: "16×16" },
  { w: 32, h: 32, label: "32×32" },
  { w: 64, h: 64, label: "64×64" },
  { w: 128, h: 128, label: "128×128" },
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
        if (!window.confirm(`Delete all toolpath variants for “${img.label || img.id}”?`)) return;
        btnDeleteVariants.disabled = true;
        try {
          await apiDelete(`/api/images/${encodeURIComponent(img.id)}/toolpath`);
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
      const kids = children.get(img.id) || [];
      const aiKids = kids.filter((k) => (k.kind || "") === "ai_lineart");
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
          im.className = "galleryThumb";
          im.style.width = "96px";
          im.style.height = "96px";
          im.style.objectFit = "cover";
          im.src = `/api/images/${encodeURIComponent(kid.id)}`;
          im.alt = kid.label || kid.id;
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
      selCrop.className = "btn";
      selCrop.style.padding = "8px 10px";
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

      const variants = Array.isArray(img.toolpaths) ? img.toolpaths : [];

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
            else await generateLocal(img.id, p.w, p.h, kind);
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

          const regen = document.createElement("button");
          regen.className = "btn";
          regen.type = "button";
          const src = v.source || "ai";
          regen.textContent = `Regenerate ${src}`;
          regen.addEventListener("click", async () => {
            regen.disabled = true;
            try {
              if (src === "ai") await generateAi(img.id, v.w, v.h);
              else await generateLocal(img.id, v.w, v.h, src);
              window.location.reload();
            } catch (e) {
              window.alert(String(e && e.message ? e.message : e));
            } finally {
              regen.disabled = false;
            }
          });
          actions.appendChild(regen);

          const del = document.createElement("button");
          del.className = "btn danger";
          del.type = "button";
          del.textContent = "Delete variant";
          del.addEventListener("click", async () => {
            if (!window.confirm(`Delete ${v.w}×${v.h} · ${src}?`)) return;
            del.disabled = true;
            try {
              await apiDelete(
                `/api/images/${encodeURIComponent(img.id)}/toolpaths/${v.w}x${v.h}/${encodeURIComponent(src)}`
              );
              window.location.reload();
            } catch (e) {
              window.alert(String(e && e.message ? e.message : e));
            } finally {
              del.disabled = false;
            }
          });
          actions.appendChild(del);

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

