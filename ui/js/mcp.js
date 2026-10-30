import { $, api, el, showError, toast } from "./util.js";

let dialogTarget = null;

const STATUS_CLASS = { connected: "ok", connecting: "warn", reconnecting: "warn", disabled: "" };

function schemaSkeleton(schema) {
  const skeleton = {};
  for (const [key, spec] of Object.entries(schema?.properties || {})) {
    if (!(schema.required || []).includes(key)) continue;
    skeleton[key] = spec.type === "number" || spec.type === "integer" ? 0 : spec.type === "boolean" ? false : "";
  }
  return JSON.stringify(skeleton, null, 2);
}

function openToolDialog(server, tool) {
  dialogTarget = { server: server.id, tool: tool.name };
  $("toolDialogTitle").textContent = `${server.id} / ${tool.name}`;
  $("toolDialogDesc").textContent = tool.description;
  $("toolArgs").value = schemaSkeleton(tool.schema);
  $("toolOutput").classList.add("hidden");
  $("toolDialog").showModal();
}

async function runDialogTool() {
  let args;
  try {
    args = JSON.parse($("toolArgs").value || "{}");
  } catch (err) {
    toast(`Arguments are not valid JSON: ${err.message}`, "error");
    return;
  }
  $("toolRun").disabled = true;
  const output = $("toolOutput");
  output.textContent = "Running...";
  output.classList.remove("hidden");
  try {
    const { server, tool } = dialogTarget;
    const result = await api.post(`/api/mcp/servers/${server}/tools/${encodeURIComponent(tool)}/call`, { arguments: args });
    output.textContent = (result.is_error ? "Error: " : "") + result.text;
  } catch (err) {
    output.textContent = err.message;
  }
  $("toolRun").disabled = false;
}

function act(promise) {
  return promise.then(refresh).catch(showError);
}

function toolRow(server, tool) {
  const checkbox = el("input", {
    type: "checkbox",
    checked: tool.enabled,
    title: "Let the model use this tool",
    onchange: (event) =>
      act(api.post(`/api/mcp/servers/${server.id}/tools/${encodeURIComponent(tool.name)}/enabled`, { enabled: event.target.checked })),
  });
  checkbox.setAttribute("aria-label", `Enable ${tool.name}`);
  return el(
    "div",
    { class: "tool-row" },
    checkbox,
    el("div", { class: "name" }, tool.name, el("small", { textContent: tool.description })),
    el("button", { type: "button", textContent: "Test", onclick: () => openToolDialog(server, tool) }),
  );
}

function serverCard(server) {
  const route = `/api/mcp/servers/${server.id}`;
  const target = server.url || [server.command, ...server.args].join(" ");
  return el(
    "div",
    { class: "card" },
    el(
      "div",
      { class: "server-head" },
      el("h3", { textContent: server.id }),
      el("span", { class: `badge ${server.error && server.status !== "connected" ? "bad" : STATUS_CLASS[server.status]}`, textContent: server.status }),
      server.active_transport ? el("span", { class: "badge", textContent: server.active_transport }) : null,
      el("span", { class: "spacer" }),
      el("button", { type: "button", textContent: "Reconnect", onclick: () => act(api.post(`${route}/reconnect`)) }),
      el("button", {
        type: "button",
        textContent: server.enabled ? "Disable" : "Enable",
        onclick: () => act(api.post(`${route}/enabled`, { enabled: !server.enabled })),
      }),
      el("button", {
        type: "button",
        class: "danger",
        textContent: "Remove",
        onclick: () => confirm(`Remove ${server.id}?`) && act(api.del(route)),
      }),
    ),
    el("p", { class: "muted mono", textContent: target }),
    server.error && server.status !== "connected" ? el("p", { class: "error", textContent: server.error }) : null,
    ...server.tools.map((tool) => toolRow(server, tool)),
  );
}

let lastSnapshot = "";

export async function refresh() {
  const servers = await api.get("/api/mcp/servers").catch(() => null);
  if (!servers) return;
  const enabled = servers.flatMap((s) => (s.status === "connected" ? s.tools.filter((t) => t.enabled) : []));
  $("toolCount").textContent = enabled.length ? `${enabled.length} MCP` : "";
  const snapshot = JSON.stringify(servers);
  if (snapshot === lastSnapshot) return;
  lastSnapshot = snapshot;
  $("mcpServers").replaceChildren(...servers.map(serverCard));
}

function parseHeaders(text) {
  const headers = {};
  for (const line of text.split("\n")) {
    const split = line.indexOf(":");
    if (split > 0) headers[line.slice(0, split).trim()] = line.slice(split + 1).trim();
  }
  return headers;
}

function syncTransport() {
  const stdio = $("mcpTransport").value === "stdio";
  for (const node of document.querySelectorAll("[data-for]")) {
    node.classList.toggle("hidden", (node.dataset.for === "stdio") !== stdio);
  }
}

async function connect(event) {
  event.preventDefault();
  $("mcpConnect").disabled = true;
  $("mcpConnect").textContent = "Connecting...";
  try {
    const server = await api.post("/api/mcp/servers", {
      id: $("mcpId").value.trim(),
      transport: $("mcpTransport").value,
      url: $("mcpUrl").value.trim() || null,
      headers: parseHeaders($("mcpHeaders").value),
      command: $("mcpCommand").value.trim() || null,
      args: $("mcpArgs").value,
      timeout: Number($("mcpTimeout").value) || 120,
    });
    if (server.status === "connected") {
      toast(`Connected to ${server.id}, ${server.tools.length} tools`);
      $("mcpForm").reset();
      syncTransport();
    } else {
      toast(`Could not connect yet: ${server.error || "no answer"}`, "error");
    }
    await refresh();
  } catch (err) {
    showError(err);
  }
  $("mcpConnect").disabled = false;
  $("mcpConnect").textContent = "Connect";
}

export function renderEndpoints(telemetry) {
  const base = telemetry.cloudflare_url || location.origin;
  const rows = [
    ["OpenAI API", `${base}/v1`],
    ["MCP endpoint", `${base}/mcp`],
  ];
  $("endpoints").replaceChildren(
    ...rows.flatMap(([key, value]) => [el("dt", { textContent: key }), el("dd", { textContent: value })]),
  );
}

export function initMcp() {
  $("mcpTransport").addEventListener("change", syncTransport);
  $("mcpForm").addEventListener("submit", connect);
  $("toolRun").addEventListener("click", runDialogTool);
  syncTransport();
  refresh();
  setInterval(refresh, 5000);
}
