import { $, api, el, showError, store, toast } from "./util.js";

async function refreshDrive() {
  const drive = await api.get("/api/drive/status").catch(() => null);
  if (!drive) return;
  $("driveMode").checked = drive.drive_mode;
  $("driveMode").disabled = !drive.mounted;
  $("driveStatus").textContent = drive.mounted
    ? `Mounted. ${drive.cached_models.length} models stored in Drive.`
    : "Not mounted. In Colab, run drive.mount('/content/drive') before starting the workstation.";
}

export function renderSystem(telemetry) {
  const system = telemetry.system;
  const rows = [
    ["OS", system.os],
    ["Python", system.python_version],
    ["GPU", system.gpus.map((g) => g.name).join(", ") || "None"],
    ["Public URL", telemetry.cloudflare_url || "No tunnel"],
  ];
  $("systemInfo").replaceChildren(
    ...rows.flatMap(([key, value]) => [el("dt", { textContent: key }), el("dd", { textContent: value })]),
  );
}

async function refreshToken() {
  const status = await api.get("/api/hf/token").catch(() => null);
  if (!status) return;
  const who = status.account ? `Connected as ${status.account}` : "Token set";
  const text = {
    saved: `${who} (${status.hint}).`,
    environment: `Using HF_TOKEN from the environment (${status.hint}).`,
  };
  $("hfTokenStatus").textContent = text[status.source] || "No token saved.";
  $("hfTokenRemove").classList.toggle("hidden", status.source !== "saved");
  // Older versions kept the token in this browser. Offer it until it is saved.
  const legacy = store.get("hfToken", "");
  if (legacy && status.source === "saved") store.remove("hfToken");
  else if (legacy) $("hfToken").value = legacy;
}

async function saveToken(event) {
  event.preventDefault();
  $("hfTokenSave").disabled = true;
  try {
    await api.post("/api/hf/token", { token: $("hfToken").value });
    $("hfToken").value = "";
    toast("Token saved");
  } catch (err) {
    showError(err);
  }
  $("hfTokenSave").disabled = false;
  refreshToken();
}

export function initSettings() {
  $("hfTokenForm").addEventListener("submit", saveToken);
  $("hfTokenRemove").addEventListener("click", async () => {
    await api.del("/api/hf/token").catch(showError);
    refreshToken();
  });
  refreshToken();
  $("driveMode").addEventListener("change", async (event) => {
    await api.post("/api/drive/mode", { enabled: event.target.checked }).catch(showError);
    refreshDrive();
  });
  refreshDrive();
}
