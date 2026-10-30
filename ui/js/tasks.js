import { $, api, el, showError, toast } from "./util.js";
import { finishMarkdown, renderMarkdown } from "./markdown.js";

const STATUS_CLASS = { completed: "ok", running: "warn", queued: "warn", failed: "bad", interrupted: "bad", paused: "warn" };
const ACTIVE = ["queued", "running"];

let selected = null;
let lastSeq = 0;
let knownStatus = {};

function badge(status) {
  return el("span", { class: `badge ${STATUS_CLASS[status] || ""}`, textContent: status });
}

function logEntry(event) {
  const time = new Date(event.time * 1000).toLocaleTimeString();
  const stamp = el("span", { class: "muted", textContent: `${time}  ` });
  switch (event.type) {
    case "tool_call":
      return el("div", { class: "log-entry" }, stamp, `Calling ${event.name} ${JSON.stringify(event.arguments)}`);
    case "tool_result":
      return el("div", { class: `log-entry ${event.is_error ? "failed" : ""}` }, stamp, event.text);
    case "text":
      return el("div", { class: "log-entry" }, stamp, event.text);
    default:
      return el("div", { class: "log-entry muted" }, stamp, event.text);
  }
}

function renderPlan(task) {
  const box = $("taskPlan");
  box.replaceChildren();
  if (task.plan.length) {
    box.append(
      el("h3", { textContent: "Plan" }),
      el("ol", {}, ...task.plan.map((step) => el("li", { class: `plan-step ${step.done ? "done" : ""}`, textContent: step.text }))),
    );
  }
  const notes = $("taskNotes");
  notes.replaceChildren();
  if (task.notes.length) {
    notes.append(el("h3", { textContent: "Notes" }), ...task.notes.map((note) => el("p", { class: "task-note", textContent: note })));
  }
}

function renderDetail(task) {
  $("taskDetail").classList.remove("hidden");
  $("taskTitle").textContent = task.title;
  const status = $("taskStatus");
  status.className = `badge ${STATUS_CLASS[task.status] || ""}`;
  status.textContent = task.status;
  const context = task.context ? `, ${task.context.used} of ${task.context.limit} tokens in use` : "";
  $("taskMeta").textContent = `Step ${task.steps} of ${task.max_steps}${context}`;
  $("taskCancel").classList.toggle("hidden", !ACTIVE.includes(task.status));
  $("taskResume").classList.toggle("hidden", ACTIVE.includes(task.status) || task.status === "completed");
  $("taskDelete").classList.toggle("hidden", task.status === "running");
  $("taskError").classList.toggle("hidden", !task.error);
  $("taskError").textContent = task.error || "";
  const result = $("taskResult");
  result.classList.toggle("hidden", !task.result);
  if (task.result && result.dataset.text !== task.result) {
    result.dataset.text = task.result;
    renderMarkdown(result, task.result);
    finishMarkdown(result);
  }
  renderPlan(task);
}

async function refreshDetail() {
  if (!selected) return;
  const id = selected;
  const task = await api.get(`/api/tasks/${id}?after=${lastSeq}`).catch(() => null);
  if (!task || id !== selected) return;
  renderDetail(task);
  const log = $("taskLog");
  const follow = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
  log.append(...task.events.map(logEntry));
  if (follow) log.scrollTop = log.scrollHeight;
  lastSeq = task.seq;
}

export function openTask(id) {
  location.hash = "tasks";
  select(id);
}

function select(id) {
  selected = id;
  lastSeq = 0;
  $("taskLog").replaceChildren();
  delete $("taskResult").dataset.text;
  refreshList();
  refreshDetail();
}

async function refreshList() {
  const tasks = await api.get("/api/tasks").catch(() => null);
  if (!tasks) return;
  for (const task of tasks) {
    const before = knownStatus[task.id];
    if (ACTIVE.includes(before) && !ACTIVE.includes(task.status)) toast(`Task ${task.status}: ${task.title}`);
    knownStatus[task.id] = task.status;
  }
  const running = tasks.filter((task) => ACTIVE.includes(task.status)).length;
  $("taskCount").textContent = running ? `(${running})` : "";
  const list = $("taskList");
  list.replaceChildren(...tasks.map((task) =>
    el(
      "button",
      { type: "button", class: `list-item task-row ${task.id === selected ? "active" : ""}`, onclick: () => select(task.id) },
      el("span", { class: "name", textContent: task.title }),
      badge(task.status),
    ),
  ));
  if (!tasks.length) list.append(el("p", { class: "muted", textContent: "No tasks yet. Ask for a big job in the chat." }));
}

async function act(path) {
  try {
    await api.post(`/api/tasks/${selected}/${path}`);
    await refreshList();
    await refreshDetail();
  } catch (err) {
    showError(err);
  }
}

export function initTasks() {
  $("taskCancel").addEventListener("click", () => act("cancel"));
  $("taskResume").addEventListener("click", () => act("resume"));
  $("taskDelete").addEventListener("click", async () => {
    if (!confirm("Delete this task?")) return;
    await api.del(`/api/tasks/${selected}`).catch(showError);
    selected = null;
    $("taskDetail").classList.add("hidden");
    refreshList();
  });
  refreshList();
  setInterval(() => {
    refreshList();
    refreshDetail();
  }, 2000);
}
