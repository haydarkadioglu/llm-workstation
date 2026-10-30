import { $, api, el, showError, store } from "./util.js";

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

export function initSettings() {
  $("hfToken").value = store.get("hfToken", "");
  $("hfToken").addEventListener("input", (event) => store.set("hfToken", event.target.value.trim()));
  $("driveMode").addEventListener("change", async (event) => {
    await api.post("/api/drive/mode", { enabled: event.target.checked }).catch(showError);
    refreshDrive();
  });
  refreshDrive();
}
