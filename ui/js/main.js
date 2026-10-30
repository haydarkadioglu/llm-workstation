import { initChat } from "./chat.js";
import { initDrive } from "./drive.js";
import { initMcp, renderEndpoints } from "./mcp.js";
import { initMedia } from "./media.js";
import { initModels, onTelemetry } from "./models.js";
import { initParams } from "./params.js";
import { initSettings, renderSystem } from "./settings.js";
import { initSkills } from "./skills.js";
import { initTasks } from "./tasks.js";

const CHAT_PANES = ["chat", "tasks", "skills"];

function route() {
  const page = location.hash.slice(1) || "chat";
  const pane = CHAT_PANES.includes(page) ? page : null;
  const current = pane ? "chat" : document.getElementById(`view-${page}`) ? page : "chat";
  for (const view of document.querySelectorAll(".view")) {
    view.classList.toggle("hidden", view.id !== `view-${current}`);
  }
  for (const link of document.querySelectorAll("#nav a")) {
    link.classList.toggle("active", link.hash === `#${current}`);
  }
  for (const name of CHAT_PANES) {
    document.getElementById(`pane-${name}`).classList.toggle("hidden", name !== (pane || "chat"));
  }
  for (const link of document.querySelectorAll("#chatTabs a")) {
    link.classList.toggle("active", link.hash === `#${pane || "chat"}`);
  }
}

window.addEventListener("hashchange", route);
route();

initParams();
onTelemetry(renderEndpoints);
onTelemetry(renderSystem);
initModels();
initMcp();
initMedia();
initDrive();
initSettings();
initTasks();
initSkills();
initChat();
