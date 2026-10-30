import { $, api, el, formatSize, showError, toast } from "./util.js";
import { deleteCached, driveLoad, loadModel, refreshCached } from "./models.js";

let status = null;
let folder = "";

function setSaveTargets() {
  for (const label of document.querySelectorAll(".save-target")) {
    label.classList.toggle("hidden", !status?.mounted);
    label.querySelector("select").value = status?.drive_mode ? "drive" : "local";
  }
}

async function setSaveTarget(event) {
  await api.post("/api/drive/mode", { enabled: event.target.value === "drive" }).catch(showError);
  refreshDrive();
}

function row(text, ...extras) {
  return el("div", { class: "list-item" }, el("span", { class: "name", textContent: text }), ...extras);
}

async function saveToDrive(model) {
  try {
    await api.post("/api/drive/transfer", { model_id: model.id, to_drive: true });
    toast(`Copying ${model.name || model.id} to Drive`);
  } catch (err) {
    showError(err);
  }
}

function modelRow(model) {
  const name = model.name || model.id;
  const kind = model.kind === "diffusion" ? "image" : "text";
  const actions =
    model.location === "drive"
      ? [
          el("button", { type: "button", textContent: "Load", onclick: () => driveLoad(model, kind) }),
          el("button", {
            type: "button",
            class: "danger",
            textContent: "Delete",
            onclick: () => deleteCached(model).then(renderModels),
          }),
        ]
      : [el("button", { type: "button", textContent: "Save to Drive", onclick: () => saveToDrive(model) })];
  return row(
    name,
    el("span", { class: "badge", textContent: model.location === "drive" ? "Drive" : "This machine" }),
    el("span", { class: "muted", textContent: `${model.size_gb} GB` }),
    ...actions,
  );
}

async function renderModels() {
  const models = await api.get("/api/models/cached").catch(() => []);
  const list = $("driveModels");
  list.replaceChildren(...models.map(modelRow));
  if (!models.length) list.append(el("p", { class: "muted", textContent: "No models yet." }));
}

async function openFolder(path) {
  const box = $("driveBrowser");
  try {
    const listing = await api.get(`/api/drive/browse?path=${encodeURIComponent(path)}`);
    folder = listing.path;
    $("drivePath").textContent = `My Drive/${folder}`;
    const entries = [];
    if (folder) {
      const parent = folder.split("/").slice(0, -1).join("/");
      entries.push(el("div", { class: "list-item" }, el("button", { type: "button", class: "folder-link", textContent: "Back", onclick: () => openFolder(parent) })));
    }
    for (const name of listing.folders) {
      const target = folder ? `${folder}/${name}` : name;
      entries.push(el("div", { class: "list-item" }, el("button", { type: "button", class: "folder-link", textContent: name, onclick: () => openFolder(target) })));
    }
    for (const file of listing.files) {
      const model = { id: file.path, name: file.name, location: "drive" };
      entries.push(
        row(
          file.name,
          el("span", { class: "muted", textContent: formatSize(file.size) }),
          el("button", { type: "button", textContent: "Load", onclick: () => driveLoad(model, "text") }),
          el("button", { type: "button", textContent: "Load from Drive", title: "Slower, but uses no local disk", onclick: () => loadModel(file.path) }),
        ),
      );
    }
    box.replaceChildren(...entries);
    if (!listing.folders.length && !listing.files.length) box.append(el("p", { class: "muted", textContent: "Nothing to show in this folder." }));
  } catch (err) {
    box.replaceChildren(el("p", { class: "error", textContent: err.message }));
  }
}

function backupText(backup) {
  if (!backup) return "No backup yet.";
  const when = new Date(backup.time * 1000).toLocaleString();
  return `Last backup: ${when}${backup.has_token ? ", with your token" : ""}.`;
}

function renderBackup(data) {
  $("driveBackupInfo").textContent = data.backup_error ? `Automatic backup failed: ${data.backup_error}` : backupText(data.backup);
  $("driveBackupInfo").classList.toggle("error", Boolean(data.backup_error));
  $("driveAuto").checked = data.auto_backup;
  $("driveRestoreNow").classList.toggle("hidden", !data.backup);
}

async function renderOffer(data) {
  // A fresh runtime has nothing of its own, which is when a backup is worth offering.
  const chats = await api.get("/api/chats").catch(() => []);
  const skills = await api.get("/api/skills").catch(() => []);
  const fresh = !chats.length && !skills.length;
  $("driveRestoreOffer").classList.toggle("hidden", !(data.backup && fresh));
  if (data.backup) $("driveRestoreText").textContent = `${backupText(data.backup)} Restore your chats, skills, tasks and settings?`;
}

export async function refreshDrive() {
  const data = await api.get("/api/drive/status").catch(() => null);
  if (!data) return;
  const justMounted = data.mounted && !status?.mounted;
  status = data;
  setSaveTargets();
  $("driveConnect").classList.toggle("hidden", data.mounted || !data.is_colab);
  $("driveReady").classList.toggle("hidden", !data.mounted);
  if (!data.mounted) {
    $("driveState").textContent = data.is_colab ? "Not connected." : "Google Drive is only available on Colab. Nothing is needed here.";
    return;
  }
  const space = data.space;
  $("driveState").textContent = `Connected. ${space.free_gb} GB free of ${space.total_gb} GB.`;
  renderBackup(data);
  if (justMounted) {
    renderModels();
    openFolder("");
    renderOffer(data);
  }
}

async function backupNow() {
  $("driveBackup").disabled = true;
  try {
    await api.post("/api/drive/backup", { include_token: $("driveWithToken").checked });
    $("driveWithToken").checked = false;
    toast("Backed up to Drive");
    refreshDrive();
  } catch (err) {
    showError(err);
  }
  $("driveBackup").disabled = false;
}

async function restore() {
  if (!confirm("Restore the backup? Files with the same name here are replaced.")) return;
  try {
    await api.post("/api/drive/restore");
    toast("Restored. Reloading.");
    setTimeout(() => location.reload(), 800);
  } catch (err) {
    showError(err);
  }
}

export function initDrive() {
  $("driveCheck").addEventListener("click", refreshDrive);
  $("driveCopySnippet").addEventListener("click", async () => {
    await navigator.clipboard.writeText($("driveSnippet").textContent);
    toast("Copied");
  });
  $("driveBackup").addEventListener("click", backupNow);
  $("driveRestore").addEventListener("click", restore);
  $("driveRestoreNow").addEventListener("click", restore);
  $("driveAuto").addEventListener("change", async (event) => {
    await api.post("/api/drive/auto-backup", { enabled: event.target.checked }).catch(showError);
    refreshDrive();
  });
  for (const label of document.querySelectorAll(".save-target")) {
    label.querySelector("select").addEventListener("change", setSaveTarget);
  }
  window.addEventListener("hashchange", () => {
    if (location.hash === "#drive" && status?.mounted) {
      renderModels();
      refreshCached();
    }
  });
  refreshDrive();
  setInterval(refreshDrive, 15000);
}
