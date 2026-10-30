import { el } from "./util.js";

marked.use({ gfm: true, breaks: true });

export function renderMarkdown(node, text) {
  node.innerHTML = DOMPurify.sanitize(marked.parse(text));
}

/* Highlighting is too slow to rerun on every token, so it happens when a block is finished. */
export function finishMarkdown(node) {
  for (const pre of node.querySelectorAll("pre")) {
    const code = pre.querySelector("code");
    if (code) hljs.highlightElement(code);
    pre.append(
      el("button", {
        class: "copy",
        type: "button",
        textContent: "Copy",
        onclick: async (event) => {
          await navigator.clipboard.writeText(code ? code.textContent : pre.textContent);
          event.target.textContent = "Copied";
          setTimeout(() => (event.target.textContent = "Copy"), 1200);
        },
      }),
    );
  }
}
