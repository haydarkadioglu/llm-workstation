import { initChat } from "./chat.js";
import { initMcp, renderEndpoints } from "./mcp.js";
import { initMedia } from "./media.js";
import { initModels, onTelemetry } from "./models.js";
import { initParams } from "./params.js";
import { initSettings, renderSystem } from "./settings.js";
import { initSkills } from "./skills.js";
import { initTasks } from "./tasks.js";

function route() {
  const page = location.hash.slice(1) || "chat";
  const current = document.getElementById(`view-${page}`) ? page : "chat";
  for (const view of document.querySelectorAll(".view")) {
    view.classList.toggle("hidden", view.id !== `view-${current}`);
  }
  for (const link of document.querySelectorAll("#nav a")) {
    link.classList.toggle("active", link.hash === `#${current}`);
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
initSettings();
initTasks();
initSkills();
initChat();
