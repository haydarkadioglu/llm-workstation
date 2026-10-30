import { $, api, el, readFileAsDataUrl, showError } from "./util.js";

const selected = new Set();
let videoTask = null;

function download(url, name) {
  return el("a", { href: url, download: name, textContent: "Save" });
}

async function generateImage(event) {
  event.preventDefault();
  $("imageGo").disabled = true;
  $("imageGo").textContent = "Generating...";
  const prompt = $("imagePrompt").value.trim();
  try {
    const { image_base64: image } = await api.post("/api/image/generate", {
      prompt,
      negative_prompt: $("imageNegative").value,
      steps: Number($("imageSteps").value),
      guidance_scale: Number($("imageGuidance").value),
      width: Number($("imageWidth").value),
      height: Number($("imageHeight").value),
    });
    $("imageGallery").prepend(
      el(
        "figure",
        {},
        el("img", { src: image, alt: prompt }),
        el("figcaption", {}, el("span", { textContent: prompt, title: prompt }), download(image, "image.png")),
      ),
    );
  } catch (err) {
    showError(err);
  }
  $("imageGo").disabled = false;
  $("imageGo").textContent = "Generate";
}

function addVideo(url, caption) {
  const checkbox = el("input", {
    type: "checkbox",
    title: "Select for merging",
    onchange: (event) => {
      event.target.checked ? selected.add(url) : selected.delete(url);
      $("mergeRow").classList.toggle("hidden", selected.size < 2);
    },
  });
  checkbox.setAttribute("aria-label", `Select ${caption}`);
  $("videoGallery").prepend(
    el(
      "figure",
      {},
      el("video", { src: url, controls: true, loop: true }),
      el("figcaption", {}, checkbox, el("span", { textContent: caption, title: caption }), download(url, "video.mp4")),
    ),
  );
}

function setVideoBusy(busy) {
  $("videoGo").disabled = busy;
  $("videoCancel").classList.toggle("hidden", !busy);
  $("videoBar").classList.toggle("hidden", !busy);
  $("videoStatus").classList.toggle("hidden", !busy);
}

async function pollVideo(taskId, prompt) {
  try {
    const task = await api.get(`/api/video/status/${taskId}`);
    $("videoBarFill").style.width = `${task.progress_pct}%`;
    $("videoStatus").textContent = `Step ${task.current_step} of ${task.total_steps}`;
    if (task.status === "processing") {
      setTimeout(() => pollVideo(taskId, prompt), 1500);
      return;
    }
    if (task.status === "completed") addVideo(task.result, prompt);
    else showError(new Error(task.error));
  } catch (err) {
    showError(err);
  }
  videoTask = null;
  setVideoBusy(false);
}

async function generateVideo(event) {
  event.preventDefault();
  const prompt = $("videoPrompt").value.trim();
  const [file] = $("videoImage").files;
  try {
    const { task_id: taskId } = await api.post("/api/video/generate", {
      prompt,
      negative_prompt: $("videoNegative").value,
      steps: Number($("videoSteps").value),
      frames: Number($("videoFrames").value),
      fps: Number($("videoFps").value),
      image: file ? await readFileAsDataUrl(file) : null,
    });
    videoTask = taskId;
    $("videoBarFill").style.width = "0";
    $("videoStatus").textContent = "Starting...";
    setVideoBusy(true);
    pollVideo(taskId, prompt);
  } catch (err) {
    showError(err);
  }
}

async function mergeVideos() {
  $("mergeVideos").disabled = true;
  try {
    const { video_base64: video } = await api.post("/api/video/merge", {
      video_base64_list: [...selected],
      fps: Number($("videoFps").value),
    });
    addVideo(video, "Merged video");
  } catch (err) {
    showError(err);
  }
  $("mergeVideos").disabled = false;
}

export function initMedia() {
  $("imageForm").addEventListener("submit", generateImage);
  $("videoForm").addEventListener("submit", generateVideo);
  $("videoCancel").addEventListener("click", () => {
    if (videoTask) api.post(`/api/video/cancel/${videoTask}`).catch(showError);
  });
  $("mergeVideos").addEventListener("click", mergeVideos);
}
