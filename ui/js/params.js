import { $, el, store } from "./util.js";

const PERSONAS = {
  short: ["Short and clear", "You are a helpful AI assistant. Keep your answers concise and clear. Get straight to the point."],
  detailed: ["Comprehensive", "You are a thorough AI assistant. Explain step by step, give examples and cover edge cases."],
  coding: ["Expert coder", "You are an expert software engineer. Provide clean, correct code and only the explanation that is needed."],
  creative: ["Creative writer", "You are a creative writer. Use vivid language and imaginative ideas, and avoid generic phrasing."],
  custom: ["Custom", ""],
};

const SLIDERS = [
  ["temperature", "Temperature", 0, 2, 0.05],
  ["top_p", "Top P", 0, 1, 0.01],
  ["top_k", "Top K", 0, 200, 1],
  ["min_p", "Min P", 0, 1, 0.01],
  ["repeat_penalty", "Repeat penalty", 1, 2, 0.01],
  ["max_tokens", "Max tokens", 64, 8192, 64],
];

const DEFAULTS = {
  temperature: 0.7,
  top_p: 0.9,
  top_k: 40,
  min_p: 0.05,
  repeat_penalty: 1.1,
  max_tokens: 1024,
  persona: "short",
  system_prompt: PERSONAS.short[1],
  seed: "",
  stop: "",
  max_steps: 10,
  agent_mode: true,
};

let values = { ...DEFAULTS, ...store.get("params", {}) };

function save() {
  store.set("params", values);
}

function sync() {
  for (const [key] of SLIDERS) {
    $(`p-${key}`).value = values[key];
    $(`o-${key}`).textContent = values[key];
  }
  $("persona").value = values.persona;
  $("systemPrompt").value = values.system_prompt;
  $("seed").value = values.seed;
  $("stopStrings").value = values.stop;
  $("maxSteps").value = values.max_steps;
  $("agentMode").checked = values.agent_mode;
}

function bind(id, key, read = (node) => node.value) {
  $(id).addEventListener("input", (event) => {
    values[key] = read(event.target);
    save();
  });
}

export function initParams() {
  for (const [key, name] of Object.entries(PERSONAS)) {
    $("persona").append(el("option", { value: key, textContent: name[0] }));
  }
  for (const [key, label, min, max, step] of SLIDERS) {
    const output = el("output", { id: `o-${key}` });
    const input = el("input", {
      id: `p-${key}`,
      type: "range",
      min,
      max,
      step,
      oninput: (event) => {
        values[key] = Number(event.target.value);
        output.textContent = values[key];
        save();
      },
    });
    input.setAttribute("aria-label", label);
    $("paramSliders").append(el("div", { class: "slider" }, el("span", { textContent: label }), output, input));
  }

  bind("systemPrompt", "system_prompt");
  bind("seed", "seed");
  bind("stopStrings", "stop");
  bind("maxSteps", "max_steps", (node) => Number(node.value) || DEFAULTS.max_steps);
  bind("agentMode", "agent_mode", (node) => node.checked);
  $("systemPrompt").addEventListener("input", () => {
    values.persona = "custom";
    $("persona").value = "custom";
    save();
  });
  $("persona").addEventListener("change", (event) => {
    values.persona = event.target.value;
    if (values.persona !== "custom") values.system_prompt = PERSONAS[values.persona][1];
    $("systemPrompt").value = values.system_prompt;
    save();
  });
  $("resetParams").addEventListener("click", () => {
    values = { ...DEFAULTS, agent_mode: values.agent_mode };
    save();
    sync();
  });
  $("toggleParams").addEventListener("click", () => $("params").classList.toggle("hidden"));
  sync();
}

export function requestParams() {
  const { temperature, top_p, top_k, min_p, repeat_penalty, max_tokens } = values;
  return {
    temperature,
    top_p,
    top_k,
    min_p,
    repeat_penalty,
    max_tokens,
    system_prompt: values.system_prompt || null,
    agent_mode: values.agent_mode,
    max_steps: values.max_steps,
    seed: values.seed === "" ? null : Number(values.seed),
    stop: values.stop.split("\n").filter(Boolean),
  };
}
