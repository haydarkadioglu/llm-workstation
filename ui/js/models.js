import { $, api, el, formatSize, showError, store, toast } from "./util.js";

const PRESETS = [
  ["Qwen 2.5 1.5B", "Qwen/Qwen2.5-1.5B-Instruct"],
  ["Qwen 2.5 0.5B", "Qwen/Qwen2.5-0.5B-Instruct"],
  ["Qwen2 VL 2B (vision)", "Qwen/Qwen2-VL-2B-Instruct"],
  ["Qwen 2.5 1.5B GGUF", "Qwen/Qwen2.5-1.5B-Instruct-GGUF/qwen2.5-1.5b-instruct-q4_k_m.gguf"],
  ["SD 1.5 (image)", "runwayml/stable-diffusion-v1-5", "image"],
];

export let telemetry = null;
const listeners = [];
let lastModel;

export function onTelemetry(listener) {
  listeners.push(listener);
}

function stat(label, value, pct) {
  return el(
    "div",
    { class: "stat" },
    el("span", { class: "muted", textContent: label }),
    el("b", { textContent: value }),
    pct === undefined ? null : el("div", { class: "meter" }, el("div", { style: `width:${pct}%` })),
  );
}

function renderStats(t) {
  const system = t.system;
  const cards = [];
  for (const gpu of system.gpus) {
    cards.push(stat(gpu.name, `${(gpu.used_vram / 1024).toFixed(1)} / ${(gpu.total_vram / 1024).toFixed(1)} GB`, gpu.vram_pct));
  }
  cards.push(stat("RAM", `${system.ram_used_gb} / ${system.ram_total_gb} GB`, system.ram_pct));
  cards.push(stat("Disk free", `${system.disk.free_gb} GB`, system.disk.pct));
  cards.push(stat("Speed", `${t.tokens_per_sec} tok/s`));
  if (t.context_length) cards.push(stat("Context size", t.context_length.toLocaleString()));
  $("stats").replaceChildren(...cards);
}

function renderStatus(t) {
  const loading = !["Ready", "No model loaded", "Error loading model"].includes(t.model_status);
  const failed = t.model_status === "Error loading model";
  $("statusDot").className = `dot ${failed ? "bad" : loading ? "busy" : t.current_model ? "ok" : ""}`;
  const name = t.current_model ? t.current_model.split(/[\\/]/).pop() : "";
  $("statusText").textContent = loading || failed || !name ? t.model_status : name;
  $("status").title = t.current_model
    ? `${t.current_model}\n${t.supports_tools ? "Native tool calling" : "Tools use the prompt fallback"}`
    : t.model_status;

  $("loadBar").classList.toggle("hidden", !loading);
  $("loadBarFill").style.width = `${t.loading_progress}%`;
  $("loadError").classList.toggle("hidden", !t.error_message);
  $("loadError").textContent = t.error_message || "";

  for (const hint of document.querySelectorAll(".model-hint")) {
    const ready = t.model_kind === hint.dataset.kind;
    hint.textContent = ready ? `Using ${name}.` : `Load a ${hint.dataset.kind} model in the Models tab first.`;
  }
}

async function poll() {
  try {
    telemetry = await api.get("/api/telemetry");
    renderStatus(telemetry);
    renderStats(telemetry);
    listeners.forEach((listener) => listener(telemetry));
    if (telemetry.current_model !== lastModel) {
      lastModel = telemetry.current_model;
      refreshCached();
    }
  } catch {
    $("statusDot").className = "dot bad";
    $("statusText").textContent = "Server unreachable";
  }
  setTimeout(poll, 2000);
}

export async function loadModel(modelId, type = "text", options = {}) {
  const token = store.get("hfToken", "");
  try {
    await api.post("/api/model/load", { model_id: modelId, model_type: type, hf_token: token || null, ...options });
    toast(`Loading ${modelId.split("/").pop()}`);
  } catch (err) {
    showError(err);
  }
}

function listItem(text, ...extras) {
  return el("div", { class: "list-item" }, el("span", { class: "name", textContent: text }), ...extras);
}

async function refreshCached() {
  const models = await api.get("/api/models/cached").catch(() => []);
  const list = $("cached");
  list.replaceChildren();
  if (!models.length) list.append(el("p", { class: "muted", textContent: "Nothing downloaded yet." }));
  for (const model of models) {
    list.append(
      listItem(
        model.id,
        el("span", { class: "badge", textContent: model.location }),
        el("span", { class: "muted", textContent: `${model.size_gb} GB` }),
        el("button", { type: "button", textContent: "Load", onclick: () => loadModel(model.id) }),
        el("button", {
          type: "button",
          class: "danger",
          textContent: "Delete",
          onclick: async () => {
            if (!confirm(`Delete ${model.id}?`)) return;
            await api.post("/api/models/cached/delete", { model_id: model.id }).catch(showError);
            refreshCached();
          },
        }),
      ),
    );
  }
}

function fitBadge(size) {
  const gpu = telemetry?.system.gpus[0];
  if (!gpu) return null;
  const free = (gpu.total_vram - gpu.used_vram) * 1024 ** 2;
  const needed = size * 1.2;
  if (needed < free) return el("span", { class: "badge ok", textContent: "Fits in VRAM" });
  if (needed < gpu.total_vram * 1024 ** 2) return el("span", { class: "badge warn", textContent: "Unload first" });
  return el("span", { class: "badge bad", textContent: "Too big for VRAM" });
}

async function showFiles(repoId) {
  const box = $("searchFiles");
  box.replaceChildren(el("p", { class: "muted", textContent: "Loading files..." }));
  try {
    const { files } = await api.get(`/api/hf/repo_files?repo_id=${encodeURIComponent(repoId)}`);
    box.replaceChildren(
      ...files.map((file) =>
        listItem(
          file.filename,
          file.parts > 1 ? el("span", { class: "badge", textContent: `${file.parts} parts` }) : null,
          el("span", { class: "muted", textContent: formatSize(file.size) }),
          fitBadge(file.size),
          el("button", { type: "button", textContent: "Load", onclick: () => loadModel(`${repoId}/${file.filename}`) }),
        ),
      ),
    );
    if (!files.length) box.replaceChildren(el("p", { class: "muted", textContent: "No GGUF files in this repo." }));
  } catch (err) {
    box.replaceChildren(el("p", { class: "error", textContent: err.message }));
  }
}

async function search(event) {
  event.preventDefault();
  const box = $("searchRepos");
  box.replaceChildren(el("p", { class: "muted", textContent: "Searching..." }));
  $("searchFiles").replaceChildren();
  try {
    const { models } = await api.get(`/api/hf/search?query=${encodeURIComponent($("searchInput").value)}`);
    box.replaceChildren(
      ...models.map((model) =>
        listItem(
          model.repo_id,
          el("span", { class: "muted", textContent: model.downloads.toLocaleString() }),
          el("button", { type: "button", textContent: "Files", onclick: () => showFiles(model.repo_id) }),
        ),
      ),
    );
    if (!models.length) box.replaceChildren(el("p", { class: "muted", textContent: "No models found." }));
  } catch (err) {
    box.replaceChildren(el("p", { class: "error", textContent: err.message }));
  }
}

async function fillSavedSettings() {
  const id = $("loadId").value.trim();
  if (!id) return;
  const saved = await api.get(`/api/model/settings?model_id=${encodeURIComponent(id)}`).catch(() => ({}));
  $("loadCtx").value = saved.n_ctx ?? "";
  $("loadGpu").value = saved.n_gpu_layers ?? "";
}

export function initModels() {
  for (const [label, id, type = "text"] of PRESETS) {
    $("presets").append(
      el("button", {
        type: "button",
        textContent: label,
        onclick: () => {
          $("loadId").value = id;
          $("loadType").value = type;
          fillSavedSettings();
        },
      }),
    );
  }
  $("loadId").addEventListener("change", fillSavedSettings);
  $("loadForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const options = {};
    if ($("loadCtx").value) options.n_ctx = Number($("loadCtx").value);
    if ($("loadGpu").value) options.n_gpu_layers = Number($("loadGpu").value);
    loadModel($("loadId").value.trim(), $("loadType").value, options);
  });
  $("unload").addEventListener("click", () => api.post("/api/model/unload").catch(showError));
  $("searchForm").addEventListener("submit", search);
  poll();
}
