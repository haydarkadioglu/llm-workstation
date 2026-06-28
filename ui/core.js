let chatHistory = [];
let imageGalleryList = [];
let videoGalleryList = [];
let pollingInterval = null;
let activeGenerationController = null;
let isGenerating = false;
let selectedImageBase64 = null;
const vramHistory = [];
const maxHistoryLength = 40;
let lastModelStatus = "";
let lastErrorMessageLogged = "";

// Custom premium toast notification helper
function showToast(desc, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;
    
    const toast = document.createElement("div");
    toast.className = `toast-msg ${type}`;
    
    let iconClass = "fa-solid fa-circle-info";
    let titleText = "Information";
    if (type === "success") {
        iconClass = "fa-solid fa-circle-check";
        titleText = "Success";
    } else if (type === "error") {
        iconClass = "fa-solid fa-circle-xmark";
        titleText = "Error";
    } else if (type === "warning") {
        iconClass = "fa-solid fa-triangle-exclamation";
        titleText = "Warning";
    }
    
    toast.innerHTML = `
        <div class="toast-icon">
            <i class="${iconClass}"></i>
        </div>
        <div class="toast-content">
            <div class="toast-title">${titleText}</div>
            <div class="toast-desc">${desc}</div>
        </div>
        <button class="toast-close" onclick="this.parentElement.remove()">
            <i class="fa-solid fa-xmark"></i>
        </button>
    `;
    
    container.appendChild(toast);
    
    requestAnimationFrame(() => {
        toast.classList.add("show");
    });
    
    setTimeout(() => {
        if (toast.parentElement) {
            toast.classList.remove("show");
            toast.addEventListener("transitionend", () => {
                toast.remove();
            });
        }
    }, 5000);
}

// Save parameters to local storage on change
function saveSettings() {
    localStorage.setItem("sys_prompt", document.getElementById("sysPrompt").value);
    localStorage.setItem("temp", document.getElementById("tempSlider").value);
    localStorage.setItem("top_p", document.getElementById("topPSlider").value);
    localStorage.setItem("max_tokens", document.getElementById("maxTokensSlider").value);
    localStorage.setItem("model_input", document.getElementById("modelInput").value);
    localStorage.setItem("model_hf_token", document.getElementById("modelHfTokenInput").value);
    localStorage.setItem("agent_mode", document.getElementById("agentToggle").checked);
}

function restoreSettings() {
    const sys = localStorage.getItem("sys_prompt");
    const temp = localStorage.getItem("temp");
    const topP = localStorage.getItem("top_p");
    const max = localStorage.getItem("max_tokens");
    const model = localStorage.getItem("model_input");
    const modelToken = localStorage.getItem("model_hf_token");
    const agent = localStorage.getItem("agent_mode");

    if (sys !== null) document.getElementById("sysPrompt").value = sys;
    if (temp !== null) {
        document.getElementById("tempSlider").value = temp;
        document.getElementById("tempVal").innerText = temp;
    }
    if (topP !== null) {
        document.getElementById("topPSlider").value = topP;
        document.getElementById("topPVal").innerText = topP;
    }
    if (max !== null) {
        document.getElementById("maxTokensSlider").value = max;
        document.getElementById("maxTokensVal").innerText = max;
    }
    if (model !== null) document.getElementById("modelInput").value = model;
    if (modelToken !== null) document.getElementById("modelHfTokenInput").value = modelToken;
    if (agent !== null) {
        document.getElementById("agentToggle").checked = (agent === "true");
    } else {
        document.getElementById("agentToggle").checked = true;
    }
}

function bindSettingListeners() {
    document.getElementById("sysPrompt").addEventListener("change", saveSettings);
    document.getElementById("tempSlider").addEventListener("change", saveSettings);
    document.getElementById("topPSlider").addEventListener("change", saveSettings);
    document.getElementById("maxTokensSlider").addEventListener("change", saveSettings);
    document.getElementById("modelInput").addEventListener("input", saveSettings);
    document.getElementById("modelHfTokenInput").addEventListener("input", saveSettings);
    document.getElementById("agentToggle").addEventListener("change", saveSettings);
}

document.addEventListener("DOMContentLoaded", () => {
    // Auto grow textarea
    const tx = document.getElementById("userInput");
    tx.addEventListener("input", function() {
        this.style.height = "auto";
        this.style.height = (this.scrollHeight) + "px";
    });

    // Load saved HF Token if exists
    const savedToken = localStorage.getItem("hf_token");
    if (savedToken) {
        document.getElementById("hfTokenInput").value = savedToken;
    }

    restoreSettings();
    restoreChatHistory();
    bindSettingListeners();
    fetchConnectedClients();
    fetchCachedModels();
    fetchDriveStatus();
    startTelemetry();
    
    window.addEventListener('resize', () => {
        if (window.location.hash === '#dashboard') {
            drawVramChart();
        }
    });
});

// Hash Routing Logic
function routePage() {
    const hash = window.location.hash || "#chat";
    const tabs = ["chat", "image", "video", "dashboard", "mcp", "settings"];
    
    tabs.forEach(tab => {
        const view = document.getElementById(`${tab}View`);
        const tabEl = document.getElementById(`${tab}Tab`);
        if (hash === `#${tab}`) {
            view.classList.remove("hidden");
            tabEl.className = "px-3.5 py-1.5 rounded-lg text-sm font-semibold bg-indigo-600/20 text-indigo-400 border border-indigo-500/30 transition duration-200";
        } else {
            view.classList.add("hidden");
            tabEl.className = "px-3.5 py-1.5 rounded-lg text-sm font-medium text-slate-400 hover:text-slate-200 transition duration-200";
        }
    });

    const mainSidebar = document.getElementById("mainSidebar");
    if (mainSidebar) {
        if (hash === "#chat") {
            mainSidebar.classList.remove("hidden");
        } else {
            mainSidebar.classList.add("hidden");
        }
    }

    if (hash === "#dashboard") {
        setTimeout(() => { drawVramChart(); }, 50);
    }
}
window.addEventListener("hashchange", routePage);
window.addEventListener("load", routePage);

function saveHfToken() {
    const token = document.getElementById("hfTokenInput").value.trim();
    localStorage.setItem("hf_token", token);
    appendLog("Hugging Face Hub Access Token saved locally.");
}

function appendLog(message, isError = false) {
    const container = document.getElementById("consoleLogs");
    const log = document.createElement("div");
    log.className = isError ? "text-red-400" : "text-slate-400";
    log.innerText = `[${new Date().toLocaleTimeString()}] ${message}`;
    container.appendChild(log);
    container.scrollTop = container.scrollHeight;
}

function selectPreset(modelId) {
    document.getElementById("modelInput").value = modelId;
    appendLog(`Selected Preset: ${modelId}`);
}

// Fetch Telemetry from FastAPI
async function fetchTelemetry() {
    try {
        const res = await fetch("/api/telemetry");
        if (!res.ok) throw new Error("Telemetry response not ok");
        const data = await res.json();
        
        const cfUrlEl = document.getElementById("cfUrl");
        const cfLight = document.getElementById("cfStatusLight");
        const mcpSseEl = document.getElementById("mcpSseUrl");
        
        if (data.cloudflare_url) {
            cfUrlEl.innerText = data.cloudflare_url;
            cfUrlEl.classList.remove("text-indigo-400");
            cfUrlEl.classList.add("text-emerald-400");
            cfLight.className = "w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse";
            document.getElementById("endpointUrl").innerText = data.cloudflare_url + "/v1";
            mcpSseEl.innerText = data.cloudflare_url + "/sse";
        } else {
            cfUrlEl.innerText = "Connecting...";
            cfLight.className = "w-2.5 h-2.5 rounded-full bg-yellow-500 animate-pulse";
            mcpSseEl.innerText = "Establishing tunnel...";
        }

        const statusLabel = document.getElementById("headerStatus");
        const modelLabel = document.getElementById("activeModelLabel");
        const indicator = document.getElementById("modelIndicator");
        const pulse = document.getElementById("modelIndicatorPulse");
        const vBadge = document.getElementById("multimodalBadge");
        
        statusLabel.innerText = data.model_status;
        
        if (data.is_vision && data.model_status === "Ready") {
            vBadge.classList.remove("hidden");
            document.getElementById("dashQuantState").innerText = "4-bit NF4 + Vision";
        } else {
            vBadge.classList.add("hidden");
            document.getElementById("dashQuantState").innerText = "4-bit NF4";
        }

        const progCard = document.getElementById("downloadProgressCard");
        if (data.model_status.startsWith("Downloading") || data.model_status.startsWith("Loading")) {
            progCard.classList.remove("hidden");
            const progressVal = data.loading_progress || 0;
            document.getElementById("downloadProgressPct").innerText = `${progressVal}%`;
            document.getElementById("downloadProgressBar").style.width = `${progressVal}%`;
            
            if (data.model_status.startsWith("Loading") && !data.loading_speed) {
                document.getElementById("downloadSpeed").innerText = "loading VRAM shards...";
            } else {
                document.getElementById("downloadSpeed").innerText = data.loading_speed || "estimating...";
            }
        } else {
            progCard.classList.add("hidden");
        }

        if (data.model_status === "Ready") {
            statusLabel.className = "text-emerald-500";
            modelLabel.innerText = data.current_model;
            indicator.className = "relative inline-flex rounded-full h-2 w-2 bg-emerald-500";
            pulse.className = "animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75";
            document.getElementById("modelLoadErrorCard").classList.add("hidden");
        } else if (data.model_status.startsWith("Downloading") || data.model_status.startsWith("Loading") || data.model_status.startsWith("Preparing")) {
            statusLabel.className = "text-yellow-500";
            modelLabel.innerText = data.model_status;
            indicator.className = "relative inline-flex rounded-full h-2 w-2 bg-yellow-500";
            pulse.className = "animate-ping absolute inline-flex h-full w-full rounded-full bg-yellow-400 opacity-75";
            document.getElementById("modelLoadErrorCard").classList.add("hidden");
        } else if (data.model_status === "Error loading model") {
            statusLabel.className = "text-red-500";
            modelLabel.innerText = "Error Loading Last Model";
            indicator.className = "relative inline-flex rounded-full h-2 w-2 bg-red-500";
            pulse.className = "";
            
            const errCard = document.getElementById("modelLoadErrorCard");
            const errText = document.getElementById("modelLoadErrorText");
            const errMsg = data.error_message || "Unknown error occurred loading the model.";
            
            errText.innerText = errMsg;
            errCard.classList.remove("hidden");
            
            if (data.error_message && data.error_message !== lastErrorMessageLogged) {
                appendLog(`Model Load Failure: ${data.error_message}`, true);
                lastErrorMessageLogged = data.error_message;
            }
        } else {
            statusLabel.className = "text-slate-500";
            modelLabel.innerText = "No model loaded";
            indicator.className = "relative inline-flex rounded-full h-2 w-2 bg-slate-500";
            pulse.className = "";
            document.getElementById("modelLoadErrorCard").classList.add("hidden");
        }

        const imgModelLabel = document.getElementById("activeImageModelLabel");
        const imgIndicator = document.getElementById("imageModelIndicator");
        const imgPulse = document.getElementById("imageModelIndicatorPulse");
        
        if (imgModelLabel && imgIndicator && imgPulse) {
            if (data.model_status === "Ready") {
                imgModelLabel.innerText = data.current_model || "No model loaded";
                imgIndicator.className = "relative inline-flex rounded-full h-2 w-2 bg-emerald-500";
                imgPulse.className = "animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75";
            } else if (data.model_status.startsWith("Downloading") || data.model_status.startsWith("Loading") || data.model_status.startsWith("Preparing")) {
                imgModelLabel.innerText = data.model_status;
                imgIndicator.className = "relative inline-flex rounded-full h-2 w-2 bg-yellow-500";
                imgPulse.className = "animate-ping absolute inline-flex h-full w-full rounded-full bg-yellow-400 opacity-75";
            } else if (data.model_status === "Error loading model") {
                imgModelLabel.innerText = "Error Loading Last Model";
                imgIndicator.className = "relative inline-flex rounded-full h-2 w-2 bg-red-500";
                imgPulse.className = "";
            } else {
                imgModelLabel.innerText = "No model loaded";
                imgIndicator.className = "relative inline-flex rounded-full h-2 w-2 bg-slate-500";
                imgPulse.className = "";
            }
        }

        const vidModelLabel = document.getElementById("activeVideoModelLabel");
        const vidIndicator = document.getElementById("videoModelIndicator");
        const vidPulse = document.getElementById("videoModelIndicatorPulse");
        
        if (vidModelLabel && vidIndicator && vidPulse) {
            if (data.model_status === "Ready") {
                vidModelLabel.innerText = data.current_model || "No model loaded";
                vidIndicator.className = "relative inline-flex rounded-full h-2 w-2 bg-emerald-500";
                vidPulse.className = "animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75";
            } else if (data.model_status.startsWith("Downloading") || data.model_status.startsWith("Loading") || data.model_status.startsWith("Preparing")) {
                vidModelLabel.innerText = data.model_status;
                vidIndicator.className = "relative inline-flex rounded-full h-2 w-2 bg-yellow-500";
                vidPulse.className = "animate-ping absolute inline-flex h-full w-full rounded-full bg-yellow-400 opacity-75";
            } else if (data.model_status === "Error loading model") {
                vidModelLabel.innerText = "Error Loading Last Model";
                vidIndicator.className = "relative inline-flex rounded-full h-2 w-2 bg-red-500";
                vidPulse.className = "";
            } else {
                vidModelLabel.innerText = "No model loaded";
                vidIndicator.className = "relative inline-flex rounded-full h-2 w-2 bg-slate-500";
                vidPulse.className = "";
            }
        }

        document.getElementById("tokenSpeed").innerText = `${data.tokens_per_sec.toFixed(1)} tokens/s`;
        document.getElementById("dashSpeed").innerText = `${data.tokens_per_sec.toFixed(1)} tokens/s`;

        let totalVramVal = 16000;
        let usedVramVal = 0;
        let pct = 0;

        if (data.system.gpu_available && data.system.gpus.length > 0) {
            const gpu = data.system.gpus[0];
            document.getElementById("gpuName").innerText = gpu.name;
            document.getElementById("dashGpuName").innerText = gpu.name;
            
            usedVramVal = gpu.used_vram;
            totalVramVal = gpu.total_vram;
            pct = gpu.vram_pct;
            
            document.getElementById("vramUsedTotal").innerText = `${usedVramVal.toFixed(0)} / ${totalVramVal.toFixed(0)} MB`;
            document.getElementById("dashVram").innerText = `${usedVramVal.toFixed(0)} / ${totalVramVal.toFixed(0)} MB`;
            document.getElementById("vramPct").innerText = `${pct.toFixed(0)}%`;
            
            const offset = 314.15 - (314.15 * pct / 100);
            document.getElementById("vramCircle").style.strokeDashoffset = offset;
        } else {
            document.getElementById("gpuName").innerText = "No GPU / CUDA";
            document.getElementById("dashGpuName").innerText = "No CUDA GPU Detected";
            
            usedVramVal = data.system.ram_used_gb * 1024;
            totalVramVal = data.system.ram_total_gb * 1024;
            pct = data.system.ram_pct;
            
            document.getElementById("vramUsedTotal").innerText = `${data.system.ram_used_gb.toFixed(1)} / ${data.system.ram_total_gb.toFixed(1)} GB`;
            document.getElementById("dashVram").innerText = `${data.system.ram_used_gb.toFixed(1)} / ${data.system.ram_total_gb.toFixed(1)} GB (RAM)`;
            document.getElementById("vramPct").innerText = `${pct.toFixed(0)}%`;
            
            const offset = 314.15 - (314.15 * pct / 100);
            document.getElementById("vramCircle").style.strokeDashoffset = offset;
        }

        if (data.system.disk) {
            document.getElementById("diskUsedTotal").innerText = `${data.system.disk.used_gb} / ${data.system.disk.total_gb} GB`;
            document.getElementById("diskProgressBar").style.width = `${data.system.disk.pct}%`;
            
            const imgDiskText = document.getElementById("imageDiskUsedTotal");
            if (imgDiskText) {
                imgDiskText.innerText = `${data.system.disk.used_gb} / ${data.system.disk.total_gb} GB`;
            }
            const vidDiskText = document.getElementById("videoDiskUsedTotal");
            if (vidDiskText) {
                vidDiskText.innerText = `${data.system.disk.used_gb} / ${data.system.disk.total_gb} GB`;
            }
        }

        const imgVramPct = document.getElementById("imageVramPct");
        const imgVramBar = document.getElementById("imageVramProgressBar");
        const imgVramText = document.getElementById("imageVramUsedTotal");
        if (imgVramPct && imgVramBar && imgVramText) {
            imgVramPct.innerText = `${pct.toFixed(0)}%`;
            imgVramBar.style.width = `${pct}%`;
            if (data.system.gpu_available && data.system.gpus.length > 0) {
                imgVramText.innerText = `${usedVramVal.toFixed(0)} / ${totalVramVal.toFixed(0)} MB`;
            } else {
                imgVramText.innerText = `${data.system.ram_used_gb.toFixed(1)} / ${data.system.ram_total_gb.toFixed(1)} GB`;
            }
        }

        const vidVramPct = document.getElementById("videoVramPct");
        const vidVramBar = document.getElementById("videoVramProgressBar");
        const vidVramText = document.getElementById("videoVramUsedTotal");
        if (vidVramPct && vidVramBar && vidVramText) {
            vidVramPct.innerText = `${pct.toFixed(0)}%`;
            vidVramBar.style.width = `${pct}%`;
            if (data.system.gpu_available && data.system.gpus.length > 0) {
                vidVramText.innerText = `${usedVramVal.toFixed(0)} / ${totalVramVal.toFixed(0)} MB`;
            } else {
                vidVramText.innerText = `${data.system.ram_used_gb.toFixed(1)} / ${data.system.ram_total_gb.toFixed(1)} GB`;
            }
        }

        const imgProgCard = document.getElementById("imageDownloadProgressCard");
        const imgErrCard = document.getElementById("imageModelLoadErrorCard");
        const imgErrText = document.getElementById("imageModelLoadErrorText");
        
        if (data.model_status.startsWith("Downloading") || data.model_status.startsWith("Loading") || data.model_status.startsWith("Preparing")) {
            if (imgProgCard) {
                imgProgCard.classList.remove("hidden");
                const progressVal = data.loading_progress || 0;
                document.getElementById("imageDownloadPctText").innerText = `${progressVal}%`;
                document.getElementById("imageDownloadProgressBar").style.width = `${progressVal}%`;
                
                if (data.model_status.startsWith("Loading") && !data.loading_speed) {
                    document.getElementById("imageDownloadStatusText").innerText = "loading VRAM shards...";
                } else {
                    document.getElementById("imageDownloadStatusText").innerText = data.loading_speed || data.model_status;
                }
            }
            if (imgErrCard) imgErrCard.classList.add("hidden");
        } else {
            if (imgProgCard) imgProgCard.classList.add("hidden");
        }
        
        if (data.model_status === "Error loading model") {
            if (imgErrText) imgErrText.innerText = data.error_message || "Unknown error occurred.";
            if (imgErrCard) imgErrCard.classList.remove("hidden");
        } else {
            if (imgErrCard && data.model_status === "Ready") {
                imgErrCard.classList.add("hidden");
            }
        }

        const vidProgCard = document.getElementById("videoDownloadProgressCard");
        const vidErrCard = document.getElementById("videoModelLoadErrorCard");
        const vidErrText = document.getElementById("videoModelLoadErrorText");
        
        if (data.model_status.startsWith("Downloading") || data.model_status.startsWith("Loading") || data.model_status.startsWith("Preparing")) {
            if (vidProgCard) {
                vidProgCard.classList.remove("hidden");
                const progressVal = data.loading_progress || 0;
                document.getElementById("videoDownloadPctText").innerText = `${progressVal}%`;
                document.getElementById("videoDownloadProgressBar").style.width = `${progressVal}%`;
                
                if (data.model_status.startsWith("Loading") && !data.loading_speed) {
                    document.getElementById("videoDownloadStatusText").innerText = "loading VRAM shards...";
                } else {
                    document.getElementById("videoDownloadStatusText").innerText = data.loading_speed || data.model_status;
                }
            }
            if (vidErrCard) vidErrCard.classList.add("hidden");
        } else {
            if (vidProgCard) {
                vidProgCard.classList.add("hidden");
            }
        }
        
        if (data.model_status === "Error loading model") {
            if (vidErrText) vidErrText.innerText = data.error_message || "Unknown error occurred.";
            if (vidErrCard) vidErrCard.classList.remove("hidden");
        } else {
            if (vidErrCard && data.model_status === "Ready") {
                vidErrCard.classList.add("hidden");
            }
        }

        if (data.model_status === "Ready" && lastModelStatus !== "Ready") {
            fetchCachedModels();
        }
        lastModelStatus = data.model_status;

        vramHistory.push(usedVramVal);
        if (vramHistory.length > maxHistoryLength) {
            vramHistory.shift();
        }
        
        if (window.location.hash === "#dashboard") {
            document.getElementById("vramChartLegend").innerText = `${usedVramVal.toFixed(0)} / ${totalVramVal.toFixed(0)} MB (${pct.toFixed(0)}%)`;
            drawVramChart(totalVramVal);
        }

        document.getElementById("dashOs").innerText = data.system.os || "Unknown";
        document.getElementById("dashRam").innerText = `${data.system.ram_used_gb.toFixed(1)} / ${data.system.ram_total_gb.toFixed(1)} GB`;
        document.getElementById("dashPyVersion").innerText = data.system.python_version || "3.x";

        document.getElementById("mcpConfigCode").innerText = JSON.stringify({
            mcpServers: {
                "llm-workstation": {
                    command: "python",
                    args: [data.system.workstation_path || "/path/to/app.py", "--mcp-stdio"]
                }
            }
        }, null, 2);

    } catch (err) {
        console.error("Telemetry error:", err);
    }
}

function startTelemetry() {
    fetchTelemetry();
    pollingInterval = setInterval(fetchTelemetry, 1500);
}

async function triggerModelLoad() {
    const input = document.getElementById("modelInput").value.trim();
    if (!input) {
        showToast("Please enter a valid Hugging Face Model ID.", "warning");
        return;
    }
    
    const hfToken = document.getElementById("modelHfTokenInput").value.trim() || localStorage.getItem("hf_token") || "";
    
    appendLog(`Initiating load request for: ${input}`);
    try {
        const res = await fetch("/api/model/load", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ 
                model_id: input,
                hf_token: hfToken
            })
        });
        const data = await res.json();
        appendLog(data.message);
    } catch (err) {
        appendLog(`Failed to load request: ${err.message}`, true);
    }
}

async function unloadModel() {
    appendLog("Initiating unload request...");
    try {
        const res = await fetch("/api/model/unload", { method: "POST" });
        const data = await res.json();
        appendLog(data.message);
    } catch (err) {
        appendLog(`Failed to unload model: ${err.message}`, true);
    }
}

async function fetchCachedModels() {
    const listContainer = document.getElementById("cachedModelsList");
    const imageListContainer = document.getElementById("cachedImageModelsList");
    const videoListContainer = document.getElementById("cachedVideoModelsList");
    try {
        const response = await fetch("/api/models/cached");
        if (!response.ok) throw new Error("Failed to scan HF cache");
        const models = await response.json();
        renderCachedModels(models);
    } catch (err) {
        if (listContainer) listContainer.innerHTML = `<span class="text-[10px] text-red-400 italic font-sans">Scan failed: ${err.message}</span>`;
        if (imageListContainer) imageListContainer.innerHTML = `<span class="text-[10px] text-red-400 italic font-sans">Scan failed: ${err.message}</span>`;
        if (videoListContainer) videoListContainer.innerHTML = `<span class="text-[10px] text-red-400 italic font-sans">Scan failed: ${err.message}</span>`;
    }
}

function renderCachedModels(models) {
    const container = document.getElementById("cachedModelsList");
    const imageContainer = document.getElementById("cachedImageModelsList");
    const videoContainer = document.getElementById("cachedVideoModelsList");
    
    // Render for LLM Sidebar
    if (container) {
        if (models.length === 0) {
            container.innerHTML = `<span class="text-[10px] text-slate-500 italic block py-1 font-sans">No models downloaded.</span>`;
        } else {
            container.innerHTML = "";
            models.forEach(model => {
                const card = document.createElement("div");
                card.className = "flex items-center justify-between p-2 bg-[#131929]/40 border border-slate-800 rounded-xl text-[10px] gap-2 hover:border-slate-700 transition";
                card.innerHTML = `
                    <div class="min-w-0 flex-1">
                        <p class="text-slate-300 font-mono leading-tight truncate" title="${model.repo_id}">${model.repo_id}</p>
                        <span class="text-slate-500 font-mono text-[9px]">${model.size_gb} GB • ${model.nb_files} files</span>
                    </div>
                    <div class="flex items-center gap-1.5 shrink-0 font-sans">
                        <button onclick="selectPreset('${model.repo_id}'); triggerModelLoad();" class="text-indigo-400 hover:text-indigo-300 transition p-1" title="Load Model">
                            <i class="fa-solid fa-play text-[10px]"></i>
                        </button>
                        <button onclick="deleteModelFromCache('${model.repo_id}')" class="text-red-400 hover:text-red-300 transition p-1" title="Delete Model">
                            <i class="fa-regular fa-trash-can text-[10px]"></i>
                        </button>
                    </div>
                `;
                container.appendChild(card);
            });
        }
    }
    
    // Render for Image Sidebar
    if (imageContainer) {
        if (models.length === 0) {
            imageContainer.innerHTML = `<span class="text-[10px] text-slate-500 italic block py-1 font-sans">No models downloaded.</span>`;
        } else {
            imageContainer.innerHTML = "";
            models.forEach(model => {
                const card = document.createElement("div");
                card.className = "flex items-center justify-between p-2 bg-[#131929]/40 border border-slate-800 rounded-xl text-[10px] gap-2 hover:border-slate-700 transition";
                card.innerHTML = `
                    <div class="min-w-0 flex-1">
                        <p class="text-slate-300 font-mono leading-tight truncate" title="${model.repo_id}">${model.repo_id}</p>
                        <span class="text-slate-500 font-mono text-[9px]">${model.size_gb} GB • ${model.nb_files} files</span>
                    </div>
                    <div class="flex items-center gap-1.5 shrink-0 font-sans">
                        <button onclick="selectImagePreset('${model.repo_id}'); triggerImageModelLoad();" class="text-indigo-400 hover:text-indigo-300 transition p-1" title="Load Model">
                            <i class="fa-solid fa-play text-[10px]"></i>
                        </button>
                        <button onclick="deleteModelFromCache('${model.repo_id}')" class="text-red-400 hover:text-red-300 transition p-1" title="Delete Model">
                            <i class="fa-regular fa-trash-can text-[10px]"></i>
                        </button>
                    </div>
                `;
                imageContainer.appendChild(card);
            });
        }
    }

    // Render for Video Sidebar
    if (videoContainer) {
        if (models.length === 0) {
            videoContainer.innerHTML = `<span class="text-[10px] text-slate-500 italic block py-1 font-sans">No models downloaded.</span>`;
        } else {
            videoContainer.innerHTML = "";
            models.forEach(model => {
                const card = document.createElement("div");
                card.className = "flex items-center justify-between p-2 bg-[#131929]/40 border border-slate-800 rounded-xl text-[10px] gap-2 hover:border-slate-700 transition";
                card.innerHTML = `
                    <div class="min-w-0 flex-1">
                        <p class="text-slate-300 font-mono leading-tight truncate" title="${model.repo_id}">${model.repo_id}</p>
                        <span class="text-slate-500 font-mono text-[9px]">${model.size_gb} GB • ${model.nb_files} files</span>
                    </div>
                    <div class="flex items-center gap-1.5 shrink-0 font-sans">
                        <button onclick="selectVideoPreset('${model.repo_id}'); triggerVideoModelLoad();" class="text-indigo-400 hover:text-indigo-300 transition p-1" title="Load Model">
                            <i class="fa-solid fa-play text-[10px]"></i>
                        </button>
                        <button onclick="deleteModelFromCache('${model.repo_id}')" class="text-red-400 hover:text-red-300 transition p-1" title="Delete Model">
                            <i class="fa-regular fa-trash-can text-[10px]"></i>
                        </button>
                    </div>
                `;
                videoContainer.appendChild(card);
            });
        }
    }
}

async function deleteModelFromCache(repoId) {
    if (!confirm(`Are you sure you want to delete ${repoId} from disk cache? This will free up storage space.`)) return;
    appendLog(`Deleting cached model ${repoId}...`);
    try {
        const response = await fetch("/api/models/cached/delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ repo_id: repoId })
        });
        if (response.ok) {
            appendLog(`Successfully deleted ${repoId} from cache.`);
            fetchCachedModels();
        } else {
            const data = await response.json();
            throw new Error(data.detail);
        }
    } catch (err) {
        appendLog(`Failed to delete model: ${err.message}`, true);
        showToast(`Error: ${err.message}`, "error");
    }
}
// --- Hugging Face Search Modal Logic ---
let hfSearchTimeout = null;
function debounceHfSearch() {
    clearTimeout(hfSearchTimeout);
    hfSearchTimeout = setTimeout(() => {
        submitHfSearch();
    }, 500);
}

function openHfSearchModal() {
    document.getElementById('hfSearchModal').classList.remove('hidden');
    document.getElementById('hfSearchInput').focus();
    
    // Auto trigger search if list is unpopulated
    if (document.getElementById('hfRepoList').innerHTML.includes('Enter a query') || document.getElementById('hfSearchInput').value.trim() === "") {
        submitHfSearch();
    }
}

function closeHfSearchModal() {
    document.getElementById('hfSearchModal').classList.add('hidden');
}

async function submitHfSearch() {
    const query = document.getElementById('hfSearchInput').value.trim();

    
    document.getElementById('hfRepoLoader').classList.remove('hidden');
    document.getElementById('hfRepoList').innerHTML = '';
    document.getElementById('hfSelectedRepoTitle').innerText = 'Select a repository';
    document.getElementById('hfFileList').innerHTML = `<div class="text-center py-10 text-slate-500 text-xs font-mono"><i class="fa-solid fa-file-code text-2xl mb-3 opacity-20"></i><br>No repository selected</div>`;
    
    try {
        const response = await fetch(`${API_BASE_URL}/api/hf/search?query=${encodeURIComponent(query)}`);
        const data = await response.json();
        
        document.getElementById('hfRepoLoader').classList.add('hidden');
        
        if (data.models && data.models.length > 0) {
            let html = '';
            data.models.forEach(repo => {
                const nameParts = repo.repo_id.split('/');
                const author = nameParts[0];
                const modelName = nameParts.slice(1).join('/');
                html += `
                    <button onclick="fetchHfRepoFiles('${repo.repo_id}')" class="w-full text-left p-3 bg-[#131929]/50 hover:bg-[#1a2136] border border-slate-800 rounded-xl transition group">
                        <div class="flex justify-between items-start">
                            <div class="overflow-hidden">
                                <p class="text-[10px] text-slate-500 font-mono truncate">${author}</p>
                                <p class="text-xs font-bold text-slate-300 truncate group-hover:text-indigo-300 transition">${modelName}</p>
                            </div>
                            <div class="flex items-center gap-2 text-[10px] text-slate-500 shrink-0">
                                <span><i class="fa-solid fa-download text-indigo-500/50 mr-1"></i>${repo.downloads.toLocaleString()}</span>
                            </div>
                        </div>
                    </button>
                `;
            });
            document.getElementById('hfRepoList').innerHTML = html;
        } else {
            document.getElementById('hfRepoList').innerHTML = `<div class="text-center py-10 text-slate-500 text-xs font-mono">No models found for "${query}"</div>`;
        }
    } catch (e) {
        document.getElementById('hfRepoLoader').classList.add('hidden');
        document.getElementById('hfRepoList').innerHTML = `<div class="text-center py-10 text-red-400 text-xs font-mono">Error fetching models.</div>`;
    }
}

async function fetchHfRepoFiles(repoId) {
    document.getElementById('hfSelectedRepoTitle').innerText = repoId;
    document.getElementById('hfFileLoader').classList.remove('hidden');
    document.getElementById('hfFileList').innerHTML = '';
    
    try {
        const response = await fetch(`${API_BASE_URL}/api/hf/repo_files?repo_id=${encodeURIComponent(repoId)}`);
        const data = await response.json();
        
        document.getElementById('hfFileLoader').classList.add('hidden');
        
        if (data.files && data.files.length > 0) {
            let html = '';
            data.files.forEach(file => {
                const sizeMb = (file.size / (1024 * 1024)).toFixed(1);
                // Try to extract quantization string (e.g. Q4_K_M)
                const quantMatch = file.filename.match(/(Q[0-9]_[K_]?[A-Z0-9_]*|IQ[0-9]_[K_]?[A-Z0-9_]*|fp16|bf16)/i);
                const quantBadge = quantMatch ? `<span class="bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 px-1.5 py-0.5 rounded text-[9px] font-bold font-mono ml-2">${quantMatch[0].toUpperCase()}</span>` : '';
                
                html += `
                    <div class="p-3 bg-[#131929]/50 border border-slate-800 rounded-xl flex items-center justify-between gap-3 hover:border-slate-700 transition">
                        <div class="flex-1 overflow-hidden flex items-center">
                            <i class="fa-regular fa-file-lines text-slate-500 mr-2 text-sm"></i>
                            <div class="flex-1 min-w-0">
                                <div class="flex items-center">
                                    <p class="text-xs font-mono text-slate-300 truncate" title="${file.filename}">${file.filename}</p>
                                    ${quantBadge}
                                </div>
                                <p class="text-[10px] text-slate-500 mt-0.5">${sizeMb} MB</p>
                            </div>
                        </div>
                        <button onclick="selectHfFileAndLoad('${repoId}', '${file.filename}')" class="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-[10px] font-bold tracking-wide transition shrink-0 shadow-md">
                            Select
                        </button>
                    </div>
                `;
            });
            document.getElementById('hfFileList').innerHTML = html;
        } else {
            document.getElementById('hfFileList').innerHTML = `<div class="text-center py-10 text-slate-500 text-xs font-mono">No .gguf files found in this repo.</div>`;
        }
    } catch (e) {
        document.getElementById('hfFileLoader').classList.add('hidden');
        document.getElementById('hfFileList').innerHTML = `<div class="text-center py-10 text-red-400 text-xs font-mono">Error fetching files.</div>`;
    }
}

function selectHfFileAndLoad(repoId, filename) {
    const fullPath = `${repoId}/${filename}`;
    document.getElementById('modelInput').value = fullPath;
    closeHfSearchModal();
    // Optional: flash or pulse the input so the user notices
    const inputEl = document.getElementById('modelInput');
    inputEl.classList.add('ring-2', 'ring-indigo-500');
    setTimeout(() => {
        inputEl.classList.remove('ring-2', 'ring-indigo-500');
    }, 1000);
}
