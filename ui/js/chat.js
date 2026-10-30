import { $, api, el, newId, readFileAsDataUrl, showError, store, toast } from "./util.js";
import { finishMarkdown, renderMarkdown } from "./markdown.js";
import { requestParams } from "./params.js";

let chat = blankChat();
let controller = null;
let attachment = null;

function blankChat() {
  return { id: newId(), title: "New chat", updated: 0, messages: [] };
}

function textOf(content) {
  if (typeof content === "string") return content;
  return (content || []).filter((p) => p.type === "text").map((p) => p.text).join("");
}

function imageOf(content) {
  if (!Array.isArray(content)) return null;
  const part = content.find((p) => p.type === "image_url");
  return part ? part.image_url.url : null;
}

/* Rendering */

function thinkingBlock(text = "", open = false) {
  const body = el("div", { class: "fold-body", textContent: text });
  const label = el("summary", { textContent: open ? "Thinking..." : "Thought process" });
  const block = el("details", { class: "fold", open }, label, body);
  return { block, body, label };
}

function toolCard(name, args) {
  const body = el("div", { class: "fold-body mono", textContent: JSON.stringify(args, null, 2) });
  const label = el("summary", { textContent: `Calling ${name}...` });
  const block = el("details", { class: "fold" }, label, body);
  return { block, body, label, name };
}

function finishToolCard(card, text, isError) {
  card.label.textContent = `${isError ? "Failed" : "Used"} ${card.name}`;
  card.block.classList.toggle("failed", isError);
  if (isError) card.block.open = true;
  card.body.textContent += `\n\n${text}`;
}

function userMessage(message, index) {
  const text = textOf(message.content);
  const image = imageOf(message.content);
  return el(
    "div",
    { class: "user-msg" },
    el("div", { class: "bubble" }, image ? el("img", { src: image, alt: "Attached image" }) : null, text),
    el(
      "div",
      { class: "msg-actions" },
      el("button", {
        class: "link",
        type: "button",
        textContent: "Edit",
        onclick: () => {
          if (controller) return;
          $("input").value = text;
          chat.messages.length = index;
          renderChat();
          $("input").focus();
        },
      }),
      el("button", {
        class: "link",
        type: "button",
        textContent: "Retry",
        onclick: () => {
          if (controller) return;
          chat.messages.length = index + 1;
          renderChat();
          generate();
        },
      }),
    ),
  );
}

function appendMarkdown(turn, text) {
  const node = el("div", { class: "md" });
  renderMarkdown(node, text);
  finishMarkdown(node);
  turn.append(node);
}

function renderChat() {
  const box = $("messages");
  box.replaceChildren();
  if (!chat.messages.length) {
    box.append(el("p", { class: "empty", textContent: "Send a message to start." }));
    return;
  }
  let turn = null;
  const cards = {};
  chat.messages.forEach((message, index) => {
    if (message.role === "user") {
      box.append(userMessage(message, index));
      turn = null;
      return;
    }
    if (!turn) {
      turn = el("div", { class: "turn" });
      box.append(turn);
    }
    if (message.role === "tool") {
      const card = cards[message.tool_call_id];
      if (card) finishToolCard(card, message.content, Boolean(message.is_error));
      return;
    }
    if (message.thinking) turn.append(thinkingBlock(message.thinking).block);
    if (textOf(message.content)) appendMarkdown(turn, textOf(message.content));
    for (const call of message.tool_calls || []) {
      let args = call.function.arguments;
      try {
        args = JSON.parse(args);
      } catch {}
      const card = toolCard(call.function.name, args);
      cards[call.id] = card;
      turn.append(card.block);
    }
  });
  box.scrollTop = box.scrollHeight;
}

/* Sidebar */

async function refreshList() {
  const chats = await api.get("/api/chats").catch(() => []);
  const list = $("chatList");
  list.replaceChildren();
  for (const item of chats) {
    list.append(
      el(
        "div",
        { class: `chat-entry ${item.id === chat.id ? "active" : ""}` },
        el("button", { class: "title", type: "button", textContent: item.title, title: item.title, onclick: () => openChat(item.id) }),
        el("button", {
          class: "remove",
          type: "button",
          textContent: "×",
          title: "Delete chat",
          ariaLabel: `Delete ${item.title}`,
          onclick: () => removeChat(item.id),
        }),
      ),
    );
  }
}

async function openChat(id) {
  if (controller) return;
  try {
    chat = await api.get(`/api/chats/${id}`);
    store.set("activeChat", id);
    renderChat();
    refreshList();
  } catch (err) {
    showError(err);
  }
}

async function removeChat(id) {
  await api.del(`/api/chats/${id}`).catch(showError);
  if (id === chat.id) startNewChat();
  else refreshList();
}

function startNewChat() {
  if (controller) return;
  chat = blankChat();
  store.set("activeChat", null);
  renderChat();
  refreshList();
  $("input").focus();
}

async function persist() {
  chat.updated = Date.now();
  if (chat.title === "New chat") {
    const first = chat.messages.find((m) => m.role === "user");
    if (first) chat.title = textOf(first.content).slice(0, 60) || "Image";
  }
  store.set("activeChat", chat.id);
  await api.put(`/api/chats/${chat.id}`, chat).catch(showError);
  refreshList();
}

/* Generation */

function setBusy(busy) {
  $("send").disabled = busy;
  $("stop").classList.toggle("hidden", !busy);
}

class TurnView {
  constructor() {
    this.turn = el("div", { class: "turn" });
    $("messages").append(this.turn);
    this.text = "";
    this.node = null;
    this.thinking = null;
    this.cards = {};
    this.frame = null;
  }

  scroll() {
    const box = $("messages");
    if (box.scrollHeight - box.scrollTop - box.clientHeight < 160) box.scrollTop = box.scrollHeight;
  }

  addToken(text) {
    this.thinkingDone();
    if (!this.node) {
      this.node = el("div", { class: "md" });
      this.turn.append(this.node);
      this.text = "";
    }
    this.text += text;
    this.frame ??= requestAnimationFrame(() => {
      this.frame = null;
      if (this.node) renderMarkdown(this.node, this.text);
      this.scroll();
    });
  }

  addThinking(text) {
    this.thinking ??= thinkingBlock("", true);
    if (!this.thinking.block.isConnected) this.turn.append(this.thinking.block);
    this.thinking.body.textContent += text;
    this.scroll();
  }

  thinkingDone() {
    if (!this.thinking) return;
    this.thinking.block.open = false;
    this.thinking.label.textContent = "Thought process";
    this.thinking = null;
  }

  endText() {
    if (!this.node) return;
    cancelAnimationFrame(this.frame);
    this.frame = null;
    renderMarkdown(this.node, this.text);
    finishMarkdown(this.node);
    this.node = null;
  }

  addTool(event) {
    this.thinkingDone();
    this.endText();
    const card = toolCard(event.name, event.arguments);
    this.cards[event.id] = card;
    this.turn.append(card.block);
    this.scroll();
  }

  finishTool(event) {
    const card = this.cards[event.id];
    if (card) finishToolCard(card, event.text, event.is_error);
    this.scroll();
  }

  addNote(text) {
    this.turn.append(el("p", { class: "error", textContent: text }));
  }
}

function showContext(event) {
  const pct = Math.round((event.used / event.limit) * 100);
  $("contextMeter").textContent = `${event.used} / ${event.limit} (${pct}%)`;
  $("contextMeter").title = event.dropped ? `${event.dropped} older turns left out to fit` : "Prompt tokens / context size";
  if (event.dropped) toast(`${event.dropped} older turns were left out to fit the context.`);
}

function handleEvent(event, view) {
  switch (event.type) {
    case "token":
      view.addToken(event.text);
      break;
    case "thinking":
      view.addThinking(event.text);
      break;
    case "tool_call":
      view.addTool(event);
      break;
    case "tool_result":
      view.finishTool(event);
      break;
    case "message":
      chat.messages.push(event.message);
      if (event.message.role === "assistant") view.endText();
      break;
    case "context":
      showContext(event);
      break;
    case "error":
      view.addNote(event.message);
      break;
    case "done":
      if (event.tokens_per_sec) $("contextMeter").textContent += ` · ${event.tokens_per_sec} tok/s`;
      break;
  }
}

async function readEvents(response, view) {
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    const frames = buffer.split("\n\n");
    buffer = frames.pop();
    for (const frame of frames) {
      if (frame.startsWith("data: ")) handleEvent(JSON.parse(frame.slice(6)), view);
    }
  }
}

function historyForModel() {
  return chat.messages.map(({ role, content, tool_calls, tool_call_id, name }) => ({
    role,
    content,
    ...(tool_calls && { tool_calls }),
    ...(tool_call_id && { tool_call_id, name }),
  }));
}

async function generate() {
  const view = new TurnView();
  controller = new AbortController();
  setBusy(true);
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: historyForModel(), ...requestParams() }),
      signal: controller.signal,
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `Request failed (${response.status})`);
    }
    await readEvents(response, view);
  } catch (err) {
    if (err.name === "AbortError") {
      if (view.text) chat.messages.push({ role: "assistant", content: view.text });
    } else {
      view.addNote(err.message);
    }
  }
  view.thinkingDone();
  view.endText();
  controller = null;
  setBusy(false);
  await persist();
}

function send() {
  const text = $("input").value.trim();
  if (controller || (!text && !attachment)) return;
  const content = attachment
    ? [{ type: "text", text }, { type: "image_url", image_url: { url: attachment } }]
    : text;
  chat.messages.push({ role: "user", content });
  $("input").value = "";
  resizeInput();
  clearAttachment();
  renderChat();
  generate();
}

function resizeInput() {
  const input = $("input");
  input.style.height = "auto";
  input.style.height = `${input.scrollHeight + 2}px`;
}

function clearAttachment() {
  attachment = null;
  $("attachment").classList.add("hidden");
  $("attachInput").value = "";
}

export async function initChat() {
  $("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    send();
  });
  $("input").addEventListener("input", resizeInput);
  $("input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send();
    }
  });
  $("stop").addEventListener("click", () => controller?.abort());
  $("newChat").addEventListener("click", startNewChat);
  $("attach").addEventListener("click", () => $("attachInput").click());
  $("attachmentClear").addEventListener("click", clearAttachment);
  $("attachInput").addEventListener("change", async (event) => {
    const [file] = event.target.files;
    if (!file) return;
    attachment = await readFileAsDataUrl(file);
    $("attachmentImg").src = attachment;
    $("attachment").classList.remove("hidden");
  });

  const active = store.get("activeChat", null);
  if (active) await openChat(active).catch(() => {});
  renderChat();
  refreshList();
}
