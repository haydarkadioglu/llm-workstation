function selectImagePreset(modelId) {
    document.getElementById("imageModelInput").value = modelId;
}

async function triggerImageModelLoad() {
    const modelId = document.getElementById("imageModelInput").value.trim();
    const hfToken = document.getElementById("imageModelToken").value.trim();
    if (!modelId) {
        showToast("Please enter a valid Hugging Face Model ID.", "warning");
        return;
    }
    
    appendLog(`Initiating load sequence for image model: ${modelId}...`);
    showToast(`Loading image model: ${modelId}`, "info");
    
    try {
        const response = await fetch("/api/model/load", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                model_id: modelId,
                hf_token: hfToken || null,
                model_type: "image"
            })
        });
        
        if (response.ok) {
            appendLog(`Load request sent. Monitoring progress...`);
        } else {
            const data = await response.json();
            throw new Error(data.detail);
        }
    } catch (err) {
        showToast(`Load failed: ${err.message}`, "error");
        appendLog(`Image model load request failed: ${err.message}`, true);
    }
}

async function generateImage() {
    const prompt = document.getElementById("imagePrompt").value.trim();
    const negativePrompt = document.getElementById("imageNegativePrompt").value.trim();
    const steps = parseInt(document.getElementById("stepsInput").value);
    const guidanceScale = parseFloat(document.getElementById("cfgInput").value);
    const width = parseInt(document.getElementById("widthInput").value);
    const height = parseInt(document.getElementById("heightInput").value);
    
    if (!prompt) {
        showToast("Please enter a prompt.", "warning");
        return;
    }
    
    const loader = document.getElementById("imageLoader");
    const placeholder = document.getElementById("imagePlaceholder");
    const generatedImg = document.getElementById("generatedImage");
    const actions = document.getElementById("imageActions");
    const btnGenerate = document.getElementById("btnGenerateImage");
    const downloadBtn = document.getElementById("btnDownloadImage");
    
    loader.classList.remove("hidden");
    btnGenerate.disabled = true;
    btnGenerate.innerHTML = `<i class="fa-solid fa-circle-notch animate-spin"></i>Generating...`;
    
    appendLog(`Generating image with prompt: "${prompt}"...`);
    
    try {
        const response = await fetch("/api/image/generate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                prompt,
                negative_prompt: negativePrompt,
                steps,
                guidance_scale: guidanceScale,
                width,
                height
            })
        });
        
        if (response.ok) {
            const data = await response.json();
            const base64Src = data.image_base64;
            
            placeholder.classList.add("hidden");
            generatedImg.src = base64Src;
            generatedImg.classList.remove("hidden");
            
            actions.classList.remove("hidden");
            downloadBtn.href = base64Src;
            
            addToImageGallery(base64Src, prompt);
            
            appendLog("Image generated successfully.");
            showToast("Image generated successfully!", "success");
        } else {
            const data = await response.json();
            throw new Error(data.detail);
        }
    } catch (err) {
        showToast(`Generation failed: ${err.message}`, "error");
        appendLog(`Image generation failed: ${err.message}`, true);
    } finally {
        loader.classList.add("hidden");
        btnGenerate.disabled = false;
        btnGenerate.innerHTML = `<i class="fa-regular fa-image"></i>Generate Image`;
    }
}

function addToImageGallery(base64Src, prompt) {
    imageGalleryList.unshift({ src: base64Src, prompt });
    renderImageGallery();
}

function renderImageGallery() {
    const container = document.getElementById("imageHistory");
    const emptyState = document.getElementById("galleryEmptyState");
    
    if (imageGalleryList.length === 0) {
        emptyState.classList.remove("hidden");
        container.innerHTML = "";
        container.appendChild(emptyState);
        return;
    }
    
    emptyState.classList.add("hidden");
    container.innerHTML = "";
    container.appendChild(emptyState);
    
    imageGalleryList.forEach((item, index) => {
        const wrapper = document.createElement("div");
        wrapper.className = "group relative bg-[#131929]/40 border border-slate-800 rounded-xl overflow-hidden cursor-pointer hover:border-slate-600 transition";
        wrapper.onclick = () => {
            document.getElementById("imagePlaceholder").classList.add("hidden");
            const generatedImg = document.getElementById("generatedImage");
            generatedImg.src = item.src;
            generatedImg.classList.remove("hidden");
            
            const actions = document.getElementById("imageActions");
            actions.classList.remove("hidden");
            document.getElementById("btnDownloadImage").href = item.src;
            
            document.getElementById("imagePrompt").value = item.prompt;
        };
        
        wrapper.innerHTML = `
            <img class="w-full h-28 object-cover opacity-80 group-hover:opacity-100 transition" src="${item.src}">
            <button onclick="deleteFromImageGallery(${index}, event)" class="absolute top-2 right-2 w-6 h-6 rounded-lg bg-red-950/80 border border-red-800/40 text-red-400 hover:text-red-300 flex items-center justify-center opacity-0 group-hover:opacity-100 transition duration-150 z-10" title="Delete from gallery">
                <i class="fa-regular fa-trash-can text-[10px]"></i>
            </button>
            <div class="absolute inset-0 bg-gradient-to-t from-[#0d1322] via-transparent to-transparent opacity-0 group-hover:opacity-100 transition p-2 flex items-end">
                <span class="text-[9px] font-mono text-slate-300 truncate w-full" title="${item.prompt}">${item.prompt}</span>
            </div>
        `;
        
        container.appendChild(wrapper);
    });
}

function deleteFromImageGallery(index, event) {
    if (event) event.stopPropagation(); // Prevent loading image on click
    
    const item = imageGalleryList[index];
    if (!item) return;
    
    // Remove from list
    imageGalleryList.splice(index, 1);
    
    // Re-render gallery
    renderImageGallery();
    
    // Reset preview frame if this image was currently active
    const generatedImg = document.getElementById("generatedImage");
    if (generatedImg && generatedImg.src === item.src) {
        generatedImg.src = "";
        generatedImg.classList.add("hidden");
        document.getElementById("imagePlaceholder").classList.remove("hidden");
        document.getElementById("imageActions").classList.add("hidden");
    }
    
    showToast("Image removed from gallery.", "info");
}
