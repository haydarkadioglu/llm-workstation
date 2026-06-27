let selectedVideoIndices = [];
let selectedVideoImageBase64 = null;

function triggerVideoImageUpload() {
    document.getElementById("videoImageInput").click();
}

function handleVideoImageSelection(event) {
    const file = event.target.files[0];
    if (!file) return;

    if (!file.type.startsWith("image/")) {
        showToast("Please select a valid image file.", "warning");
        return;
    }

    const reader = new FileReader();
    reader.onload = function(e) {
        selectedVideoImageBase64 = e.target.result;
        document.getElementById("videoImagePreview").src = selectedVideoImageBase64;
        document.getElementById("videoImageFileName").innerText = file.name;
        document.getElementById("videoImagePreviewContainer").classList.remove("hidden");
        appendLog(`Attached video condition image: ${file.name}`);
    };
    reader.readAsDataURL(file);
}

function clearSelectedVideoImage() {
    selectedVideoImageBase64 = null;
    document.getElementById("videoImageInput").value = "";
    document.getElementById("videoImagePreviewContainer").classList.add("hidden");
    document.getElementById("videoImagePreview").src = "";
}

function updateVideoDurationEst() {
    const framesVal = parseInt(document.getElementById("videoFramesInput").value);
    const fpsVal = parseInt(document.getElementById("videoFpsInput").value);
    const est = (framesVal / fpsVal).toFixed(1);
    const estEl = document.getElementById("videoDurationEst");
    if (estEl) estEl.innerText = est;
}

function selectVideoPreset(modelId) {
    document.getElementById("videoModelInput").value = modelId;
}

async function triggerVideoModelLoad() {
    const modelId = document.getElementById("videoModelInput").value.trim();
    const hfToken = document.getElementById("videoModelToken").value.trim();
    
    if (!modelId) {
        showToast("Please enter a Hugging Face model ID.", "error");
        return;
    }
    
    appendLog(`Initiating load sequence for video model '${modelId}'...`);
    document.getElementById("videoModelLoadErrorCard").classList.add("hidden");
    
    try {
        const response = await fetch("/api/model/load", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                model_id: modelId,
                hf_token: hfToken || null,
                model_type: "video"
            })
        });
        
        if (response.ok) {
            showToast("Video model load sequence started.", "info");
        } else {
            const data = await response.json();
            throw new Error(data.detail);
        }
    } catch (err) {
        appendLog(`Failed to start load sequence: ${err.message}`, true);
        showToast(`Load failed: ${err.message}`, "error");
    }
}

async function generateVideo() {
    const prompt = document.getElementById("videoPrompt").value.trim();
    const negativePrompt = document.getElementById("videoNegativePrompt").value.trim();
    const steps = parseInt(document.getElementById("videoStepsInput").value);
    const frames = parseInt(document.getElementById("videoFramesInput").value);
    const fps = parseInt(document.getElementById("videoFpsInput").value);
    
    if (!prompt) {
        showToast("Please enter a video prompt.", "error");
        return;
    }
    
    const btnGenerate = document.getElementById("btnGenerateVideo");
    const loader = document.getElementById("videoGeneratorLoader");
    const placeholder = document.getElementById("videoPlaceholder");
    const generatedVid = document.getElementById("generatedVideo");
    const actions = document.getElementById("videoActions");
    const downloadBtn = document.getElementById("btnDownloadVideo");
    
    btnGenerate.disabled = true;
    btnGenerate.innerHTML = `<div class="w-4 h-4 border-2 border-slate-400 border-t-white rounded-full animate-spin"></div>Generating...`;
    loader.classList.remove("hidden");
    
    try {
        const response = await fetch("/api/video/generate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                prompt,
                negative_prompt: negativePrompt,
                steps,
                frames,
                fps,
                image: selectedVideoImageBase64
            })
        });
        
        if (response.ok) {
            const data = await response.json();
            const base64Src = data.video_base64;
            
            placeholder.classList.add("hidden");
            generatedVid.src = base64Src;
            generatedVid.classList.remove("hidden");
            generatedVid.load();
            
            actions.classList.remove("hidden");
            downloadBtn.href = base64Src;
            
            addToVideoGallery(base64Src, prompt);
            
            // Clear condition image upload after successful generation
            clearSelectedVideoImage();
            
            appendLog("Video generated successfully.");
            showToast("Video generated successfully!", "success");
        } else {
            const data = await response.json();
            throw new Error(data.detail);
        }
    } catch (err) {
        showToast(`Generation failed: ${err.message}`, "error");
        appendLog(`Video generation failed: ${err.message}`, true);
    } finally {
        loader.classList.add("hidden");
        btnGenerate.disabled = false;
        btnGenerate.innerHTML = `<i class="fa-solid fa-video"></i>Generate Video`;
    }
}

function addToVideoGallery(base64Src, prompt) {
    videoGalleryList.unshift({ src: base64Src, prompt });
    selectedVideoIndices = [];
    updateMergeActionBar();
    renderVideoGallery();
}

function renderVideoGallery() {
    const container = document.getElementById("videoHistory");
    const emptyState = document.getElementById("videoGalleryEmptyState");
    
    if (videoGalleryList.length === 0) {
        emptyState.classList.remove("hidden");
        container.innerHTML = "";
        container.appendChild(emptyState);
        return;
    }
    
    emptyState.classList.add("hidden");
    container.innerHTML = "";
    container.appendChild(emptyState);
    
    videoGalleryList.forEach((item, index) => {
        const wrapper = document.createElement("div");
        wrapper.className = "group relative bg-[#131929]/40 border border-slate-800 rounded-xl overflow-hidden cursor-pointer hover:border-slate-600 transition";
        wrapper.onclick = () => {
            document.getElementById("videoPlaceholder").classList.add("hidden");
            const generatedVid = document.getElementById("generatedVideo");
            generatedVid.src = item.src;
            generatedVid.classList.remove("hidden");
            generatedVid.load();
            
            const actions = document.getElementById("videoActions");
            actions.classList.remove("hidden");
            document.getElementById("btnDownloadVideo").href = item.src;
            
            document.getElementById("videoPrompt").value = item.prompt;
        };
        
        const isChecked = selectedVideoIndices.includes(index);
        
        wrapper.innerHTML = `
            <!-- Selection Checkbox -->
            <div class="absolute top-2 left-2 z-10 transition duration-150 ${isChecked ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}">
                <input type="checkbox" ${isChecked ? 'checked' : ''} onchange="toggleVideoSelection(${index}, event)" class="w-3.5 h-3.5 rounded border-slate-700 bg-slate-900/80 accent-indigo-500 cursor-pointer">
            </div>
            
            <video class="w-full h-28 object-cover opacity-80 group-hover:opacity-100 transition" src="${item.src}" muted playsinline loop autoplay></video>
            
            <button onclick="deleteFromVideoGallery(${index}, event)" class="absolute top-2 right-2 w-6 h-6 rounded-lg bg-red-950/80 border border-red-800/40 text-red-400 hover:text-red-300 flex items-center justify-center opacity-0 group-hover:opacity-100 transition duration-150 z-10" title="Delete from gallery">
                <i class="fa-regular fa-trash-can text-[10px]"></i>
            </button>
            <div class="absolute inset-0 bg-gradient-to-t from-[#0d1322] via-transparent to-transparent opacity-0 group-hover:opacity-100 transition p-2 flex items-end">
                <span class="text-[9px] font-mono text-slate-300 truncate w-full" title="${item.prompt}">${item.prompt}</span>
            </div>
        `;
        
        container.appendChild(wrapper);
    });
}

function toggleVideoSelection(index, event) {
    if (event) event.stopPropagation();
    
    const idx = selectedVideoIndices.indexOf(index);
    if (idx === -1) {
        selectedVideoIndices.push(index);
    } else {
        selectedVideoIndices.splice(idx, 1);
    }
    
    updateMergeActionBar();
    renderVideoGallery();
}

function updateMergeActionBar() {
    const bar = document.getElementById("videoMergeActionBar");
    const countEl = document.getElementById("selectedVideoCount");
    if (!bar || !countEl) return;
    
    if (selectedVideoIndices.length >= 2) {
        countEl.innerText = selectedVideoIndices.length;
        bar.classList.remove("hidden");
    } else {
        bar.classList.add("hidden");
    }
}

async function mergeSelectedVideos() {
    if (selectedVideoIndices.length < 2) return;
    
    const sortedIndices = [...selectedVideoIndices].sort((a, b) => b - a);
    const base64List = sortedIndices.map(idx => videoGalleryList[idx].src);
    
    const btnMerge = document.querySelector("#videoMergeActionBar button");
    const origHtml = btnMerge.innerHTML;
    btnMerge.disabled = true;
    btnMerge.innerHTML = `<i class="fa-solid fa-circle-notch animate-spin"></i> Merging...`;
    
    const fpsVal = parseInt(document.getElementById("videoFpsInput").value) || 8;
    
    appendLog(`Merging ${sortedIndices.length} selected video clips...`);
    
    try {
        const response = await fetch("/api/video/merge", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                video_base64_list: base64List,
                fps: fpsVal
            })
        });
        
        if (response.ok) {
            const data = await response.json();
            const base64Src = data.video_base64;
            
            document.getElementById("videoPlaceholder").classList.add("hidden");
            const generatedVid = document.getElementById("generatedVideo");
            generatedVid.src = base64Src;
            generatedVid.classList.remove("hidden");
            generatedVid.load();
            
            document.getElementById("videoActions").classList.remove("hidden");
            document.getElementById("btnDownloadVideo").href = base64Src;
            
            addToVideoGallery(base64Src, `Merged Video (${sortedIndices.length} clips)`);
            
            selectedVideoIndices = [];
            updateMergeActionBar();
            
            showToast("Videos merged successfully!", "success");
            appendLog("Videos merged successfully.");
        } else {
            const data = await response.json();
            throw new Error(data.detail);
        }
    } catch (err) {
        showToast(`Merge failed: ${err.message}`, "error");
        appendLog(`Video merge failed: ${err.message}`, true);
    } finally {
        btnMerge.disabled = false;
        btnMerge.innerHTML = origHtml;
    }
}

function deleteFromVideoGallery(index, event) {
    if (event) event.stopPropagation(); // Prevent loading video on click
    
    const item = videoGalleryList[index];
    if (!item) return;
    
    // Remove from list
    videoGalleryList.splice(index, 1);
    selectedVideoIndices = [];
    updateMergeActionBar();
    
    // Re-render gallery
    renderVideoGallery();
    
    // Reset preview frame if this video was currently active
    const generatedVid = document.getElementById("generatedVideo");
    if (generatedVid && generatedVid.src === item.src) {
        generatedVid.src = "";
        generatedVid.classList.add("hidden");
        document.getElementById("videoPlaceholder").classList.remove("hidden");
        document.getElementById("videoActions").classList.add("hidden");
    }
    
    showToast("Video removed from gallery.", "info");
}
