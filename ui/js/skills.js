import { $, api, el, readFileAsDataUrl, showError, toast } from "./util.js";

function editing() {
  return {
    name: $("skillName").value.trim(),
    description: $("skillDescription").value.trim(),
    instructions: $("skillInstructions").value,
  };
}

function openEditor(skill) {
  $("skillName").value = skill.name;
  $("skillDescription").value = skill.description;
  $("skillInstructions").value = skill.instructions;
  $("skillChange").value = "";
  $("skillEditor").classList.remove("hidden");
  $("skillEditor").scrollIntoView({ block: "nearest" });
}

async function withBusy(button, label, action) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try {
    await action();
  } catch (err) {
    showError(err);
  }
  button.disabled = false;
  button.textContent = original;
}

function skillRow(skill) {
  const route = `/api/skills/${skill.name}`;
  const toggle = el("input", {
    type: "checkbox",
    checked: skill.enabled,
    title: "Use this skill in chats and tasks",
    onchange: (event) => api.post(`${route}/enabled`, { enabled: event.target.checked }).then(refresh).catch(showError),
  });
  toggle.setAttribute("aria-label", `Use ${skill.name}`);
  return el(
    "div",
    { class: "tool-row" },
    toggle,
    el("div", { class: "name" }, skill.name, el("small", { textContent: skill.description })),
    el("button", { type: "button", textContent: "Edit", onclick: () => api.get(route).then(openEditor).catch(showError) }),
    el("a", { class: "button", href: `${route}/download`, download: `${skill.name}.md`, textContent: "Download" }),
    el("button", {
      type: "button",
      class: "danger",
      textContent: "Delete",
      onclick: () => confirm(`Delete ${skill.name}?`) && api.del(route).then(refresh).catch(showError),
    }),
  );
}

export async function refresh() {
  const skills = await api.get("/api/skills").catch(() => null);
  if (!skills) return;
  const list = $("skillList");
  list.replaceChildren(...skills.map(skillRow));
  if (!skills.length) list.append(el("p", { class: "muted", textContent: "No skills yet. Create one above, or load a file you saved earlier." }));
  const enabled = skills.filter((s) => s.enabled).length;
  $("skillCount").textContent = enabled ? `(${enabled})` : "";
}

async function importFile(file) {
  const isZip = file.name.toLowerCase().endsWith(".zip");
  const payload = { filename: file.name };
  if (isZip) payload.base64 = (await readFileAsDataUrl(file)).split(",")[1];
  else payload.text = await file.text();
  return (await api.post("/api/skills/import", payload)).imported;
}

export function initSkills() {
  $("skillRefine").addEventListener("click", () => {
    const change = $("skillChange").value.trim();
    if (!change) return;
    withBusy($("skillRefine"), "Changing...", async () => {
      openEditor(await api.post("/api/skills/draft", { description: change, current: editing() }));
    });
  });
  $("skillForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const saved = await api.post("/api/skills", editing());
      toast(`Saved ${saved.name}`);
      $("skillEditor").classList.add("hidden");
      refresh();
    } catch (err) {
      showError(err);
    }
  });
  $("skillDiscard").addEventListener("click", () => $("skillEditor").classList.add("hidden"));
  $("skillImport").addEventListener("click", () => $("skillFile").click());
  $("skillFile").addEventListener("change", async (event) => {
    const names = [];
    for (const file of event.target.files) {
      try {
        names.push(...(await importFile(file)));
      } catch (err) {
        showError(err);
      }
    }
    event.target.value = "";
    if (names.length) toast(`Loaded ${names.join(", ")}`);
    refresh();
  });
  refresh();
}
