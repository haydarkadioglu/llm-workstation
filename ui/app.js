let chatHistory = [];
        let imageGalleryList = [];
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
            
            // Trigger animation frame to slide in
            requestAnimationFrame(() => {
                toast.classList.add("show");
            });
            
            // Auto dismiss after 5s
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

        // Restore chat messages from storage
        function restoreChatHistory() {
            const saved = localStorage.getItem("chat_history");
            if (!saved) return;
            try {
                chatHistory = JSON.parse(saved);
                chatHistory.forEach(msg => {
                    renderChatMessage(msg.role, msg.content);
                });
            } catch (err) {
                console.error("Failed to parse chat history:", err);
                chatHistory = [];
            }
        }

        function renderChatMessage(role, content) {
            const chatContainer = document.getElementById("chatContainer");
            if (role === "system") return; // System prompt is not rendered in bubble
            
            const card = document.createElement("div");
            
            if (role === "user") {
                card.className = "flex justify-end max-w-3xl ml-auto";
                let text = "";
                let imageTagHtml = "";
                
                if (Array.isArray(content)) {
                    for (const part of content) {
                        if (part.type === "text") text = part.text;
                        else if (part.type === "image_url") {
                            imageTagHtml = `<img src="${part.image_url.url}" class="max-w-[260px] max-h-[200px] object-contain rounded-xl border border-slate-700/80 mb-2.5 shadow-md">`;
                        }
                    }
                } else {
                    text = content;
                }
                
                card.innerHTML = `
                    <div class="bg-indigo-600/35 border border-indigo-500/50 rounded-2xl p-4 shadow-lg text-slate-100 max-w-full">
                        <p class="text-xs font-semibold text-indigo-300 mb-1.5">You</p>
                        ${imageTagHtml}
                        <p class="text-sm leading-relaxed whitespace-pre-wrap">${text}</p>
                    </div>
                `;
            } else {
                card.className = "flex justify-start max-w-3xl";
                const responseId = "assistant-res-" + Math.random().toString(36).substring(7);
                card.innerHTML = `
                    <div class="bg-slate-800/40 border border-slate-700/50 rounded-2xl p-4 shadow-lg text-slate-100 max-w-full">
                        <p class="text-xs font-semibold text-purple-400 mb-1.5">Assistant</p>
                        <p id="${responseId}" class="text-sm leading-relaxed text-slate-300 font-normal"></p>
                    </div>
                `;
                chatContainer.appendChild(card);
                document.getElementById(responseId).innerHTML = renderContent(content);
                return;
            }
            chatContainer.appendChild(card);
            chatContainer.scrollTop = chatContainer.scrollHeight;
        }

        function clearChat() {
            chatHistory = [];
            localStorage.removeItem("chat_history");
            const chatContainer = document.getElementById("chatContainer");
            // Keep only the first child (welcome message)
            while (chatContainer.children.length > 1) {
                chatContainer.removeChild(chatContainer.lastChild);
            }
            appendLog("Chat history cleared.");
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

            // Restore settings & input IDs
            restoreSettings();

            // Load saved Chat History
            restoreChatHistory();

            // Save parameter changes dynamically
            bindSettingListeners();

            // Fetch external MCP client list on load
            fetchConnectedClients();

            // Fetch Hugging Face cache models list on load
            fetchCachedModels();

            // Start telemetry pooling
            startTelemetry();
            
            // Set up resize handler for the canvas chart
            window.addEventListener('resize', () => {
                if (window.location.hash === '#dashboard') {
                    drawVramChart();
                }
            });
        });

        // Hash Routing Logic
        function routePage() {
            const hash = window.location.hash || "#chat";
            const tabs = ["chat", "image", "dashboard", "mcp", "settings"];
            
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

            // Toggle main sidebar: show ONLY on Chat tab to prevent overlapping
            const mainSidebar = document.getElementById("mainSidebar");
            if (mainSidebar) {
                if (hash === "#chat") {
                    mainSidebar.classList.remove("hidden");
                } else {
                    mainSidebar.classList.add("hidden");
                }
            }

            if (hash === "#dashboard") {
                // Short timeout to let the canvas resize/mount properly
                setTimeout(() => { drawVramChart(); }, 50);
            }
        }
        window.addEventListener("hashchange", routePage);
        window.addEventListener("load", routePage);

        // Save HF Token
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

        // Quick template loader selection
        function selectPreset(modelId) {
            document.getElementById("modelInput").value = modelId;
            appendLog(`Selected Preset: ${modelId}`);
        }

        // Copy Tunnel URL
        function copyTunnelUrl() {
            const urlText = document.getElementById("cfUrl").innerText;
            if (urlText && urlText !== "Detecting..." && urlText !== "Disabled") {
                navigator.clipboard.writeText(urlText);
                appendLog("Cloudflare Tunnel URL copied to clipboard.");
            }
        }

        // Real-Time Canvas Line Chart Drawing
        function drawVramChart(totalVram = 16000) {
            const canvas = document.getElementById("vramChartCanvas");
            if (!canvas) return;
            const ctx = canvas.getContext("2d");
            const width = canvas.clientWidth;
            const height = canvas.clientHeight;
            
            // Set scale for high-definition displays
            canvas.width = width * window.devicePixelRatio;
            canvas.height = height * window.devicePixelRatio;
            ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
            
            ctx.clearRect(0, 0, width, height);
            
            if (vramHistory.length === 0) return;
            
            // Draw horizontal grid lines
            ctx.strokeStyle = "rgba(51, 65, 85, 0.35)";
            ctx.lineWidth = 1;
            for (let i = 1; i < 4; i++) {
                const y = (height / 4) * i;
                ctx.beginPath();
                ctx.moveTo(0, y);
                ctx.lineTo(width, y);
                ctx.stroke();
            }
            
            // Calculate coordinates
            const points = [];
            const stepX = width / (maxHistoryLength - 1);
            
            for (let i = 0; i < vramHistory.length; i++) {
                const val = vramHistory[i];
                const x = i * stepX;
                const pct = val / totalVram;
                // Leave padding at top/bottom
                const y = height - (pct * (height - 30)) - 15;
                points.push({ x, y });
            }
            
            // Draw Area under curve (gradient fill)
            if (points.length > 1) {
                ctx.beginPath();
                ctx.moveTo(points[0].x, height);
                for (const p of points) {
                    ctx.lineTo(p.x, p.y);
                }
                ctx.lineTo(points[points.length - 1].x, height);
                ctx.closePath();
                
                const grad = ctx.createLinearGradient(0, 0, 0, height);
                grad.addColorStop(0, "rgba(99, 102, 241, 0.2)");
                grad.addColorStop(1, "rgba(99, 102, 241, 0.0)");
                ctx.fillStyle = grad;
                ctx.fill();
            }
            
            // Draw Line
            ctx.beginPath();
            ctx.strokeStyle = "#6366f1";
            ctx.lineWidth = 3;
            ctx.lineCap = "round";
            ctx.lineJoin = "round";
            
            if (points.length > 0) {
                ctx.moveTo(points[0].x, points[0].y);
                for (let i = 1; i < points.length; i++) {
                    ctx.lineTo(points[i].x, points[i].y);
                }
                ctx.stroke();
            }
            
            // Draw last point dot
            if (points.length > 0) {
                const last = points[points.length - 1];
                ctx.beginPath();
                ctx.arc(last.x, last.y, 5, 0, Math.PI * 2);
                ctx.fillStyle = "#a78bfa";
                ctx.fill();
                ctx.lineWidth = 1.5;
                ctx.strokeStyle = "#ffffff";
                ctx.stroke();
            }
        }

        // Fetch Telemetry from FastAPI
        async function fetchTelemetry() {
            try {
                const res = await fetch("/api/telemetry");
                if (!res.ok) throw new Error("Telemetry response not ok");
                const data = await res.json();
                
                // Update Cloudflare Tunnel URL
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

                // Update active model header and state indicator
                const statusLabel = document.getElementById("headerStatus");
                const modelLabel = document.getElementById("activeModelLabel");
                const indicator = document.getElementById("modelIndicator");
                const pulse = document.getElementById("modelIndicatorPulse");
                const vBadge = document.getElementById("multimodalBadge");
                
                statusLabel.innerText = data.model_status;
                
                // Multimodal Badge visibility
                if (data.is_vision && data.model_status === "Ready") {
                    vBadge.classList.remove("hidden");
                    document.getElementById("dashQuantState").innerText = "4-bit NF4 + Vision";
                } else {
                    vBadge.classList.add("hidden");
                    document.getElementById("dashQuantState").innerText = "4-bit NF4";
                }

                // Download/Loading Progress Panel Control
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

                // Sync Image Models Header status
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

                // Token speed update
                document.getElementById("tokenSpeed").innerText = `${data.tokens_per_sec.toFixed(1)} tokens/s`;
                document.getElementById("dashSpeed").innerText = `${data.tokens_per_sec.toFixed(1)} tokens/s`;

                // Update GPU list details & Radial Gauge & History
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
                    
                    // Fallback to host RAM metrics
                    usedVramVal = data.system.ram_used_gb * 1024; // convert to MB
                    totalVramVal = data.system.ram_total_gb * 1024;
                    pct = data.system.ram_pct;
                    
                    document.getElementById("vramUsedTotal").innerText = `${data.system.ram_used_gb.toFixed(1)} / ${data.system.ram_total_gb.toFixed(1)} GB`;
                    document.getElementById("dashVram").innerText = `${data.system.ram_used_gb.toFixed(1)} / ${data.system.ram_total_gb.toFixed(1)} GB (RAM)`;
                    document.getElementById("vramPct").innerText = `${pct.toFixed(0)}%`;
                    
                    const offset = 314.15 - (314.15 * pct / 100);
                    document.getElementById("vramCircle").style.strokeDashoffset = offset;
                }
            // Update Disk space telemetry
                if (data.system.disk) {
                    document.getElementById("diskUsedTotal").innerText = `${data.system.disk.used_gb} / ${data.system.disk.total_gb} GB`;
                    document.getElementById("diskProgressBar").style.width = `${data.system.disk.pct}%`;
                    
                    const imgDiskText = document.getElementById("imageDiskUsedTotal");
                    if (imgDiskText) {
                        imgDiskText.innerText = `${data.system.disk.used_gb} / ${data.system.disk.total_gb} GB`;
                    }
                }

                // Update VRAM telemetry inside Image Model Manager sidebar
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

                // Update Image Model Load Progress and Error alerts
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

                // Refresh cached models list when model state transitions to Ready
                if (data.model_status === "Ready" && lastModelStatus !== "Ready") {
                    fetchCachedModels();
                }
                lastModelStatus = data.model_status;

                // Append to VRAM history and redraw chart
                vramHistory.push(usedVramVal);
                if (vramHistory.length > maxHistoryLength) {
                    vramHistory.shift();
                }
                
                if (window.location.hash === "#dashboard") {
                    document.getElementById("vramChartLegend").innerText = `${usedVramVal.toFixed(0)} / ${totalVramVal.toFixed(0)} MB (${pct.toFixed(0)}%)`;
                    drawVramChart(totalVramVal);
                }

                // Dashboard environment details
                document.getElementById("dashOs").innerText = data.system.os || "Unknown";
                document.getElementById("dashRam").innerText = `${data.system.ram_used_gb.toFixed(1)} / ${data.system.ram_total_gb.toFixed(1)} GB`;
                document.getElementById("dashPyVersion").innerText = data.system.python_version || "3.x";

                // Update Claude Desktop Config Code with dynamic path
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

        // Trigger Async Model Loading
        async function triggerModelLoad() {
            const input = document.getElementById("modelInput").value.trim();
            if (!input) {
                showToast("Please enter a valid Hugging Face Model ID.", "warning");
                return;
            }
            
            // Read optional token from sidebar field, or fallback to saved token in settings
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

        // Unload Active Model
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

        // Trigger file input dialog
        function triggerImageUpload() {
            document.getElementById("imageInput").click();
        }

        // Handle image selection
        function handleImageSelection(event) {
            const file = event.target.files[0];
            if (!file) return;

            if (!file.type.startsWith("image/")) {
                showToast("Please select a valid image file.", "warning");
                return;
            }

            const reader = new FileReader();
            reader.onload = function(e) {
                selectedImageBase64 = e.target.result;
                
                // Show preview container
                document.getElementById("imagePreview").src = selectedImageBase64;
                document.getElementById("imageFileName").innerText = file.name;
                document.getElementById("imagePreviewContainer").classList.remove("hidden");
                
                appendLog(`Attached image: ${file.name}`);
            };
            reader.readAsDataURL(file);
        }

        // Clear attached image
        function clearSelectedImage() {
            selectedImageBase64 = null;
            document.getElementById("imageInput").value = "";
            document.getElementById("imagePreviewContainer").classList.add("hidden");
            document.getElementById("imagePreview").src = "";
        }

        // Handle Textarea Enter Keydown
        function handleTextareaKeydown(event) {
            if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                sendMessage();
            }
        }

        // Render message content with reasoning/thought block detection & tool styling
        function renderContent(content) {
            let processed = content;
            
            // Remove raw JSON tool call blocks entirely to clean up the chat log
            processed = processed.replace(/```(?:json)?\s*\{\s*"tool"[\s\S]*?\}\s*```/g, "");
            processed = processed.replace(/```(?:json)?\s*\{\s*"tool"[\s\S]*?$/g, ""); // partial stream
            
            const widgets = [];
            
            // Replace completed think blocks with placeholders
            processed = processed.replace(/<think>([\s\S]*?)<\/think>/g, function(match, thinkingText) {
                const id = widgets.length;
                widgets.push(`
                    <details class="group mb-3 bg-[#0c101b]/60 border border-slate-800/80 rounded-xl overflow-hidden shadow-md">
                        <summary class="cursor-pointer px-4 py-2 text-xs font-semibold text-slate-400 bg-[#131929]/50 hover:bg-[#1a2136]/50 transition flex items-center justify-between select-none">
                            <span class="flex items-center gap-1.5 font-mono"><i class="fa-solid fa-brain text-indigo-400"></i> Thinking Process</span>
                            <i class="fa-solid fa-chevron-down text-[9px] text-slate-500 group-open:rotate-180 transition-transform duration-200"></i>
                        </summary>
                        <div class="p-4 text-[11px] leading-relaxed text-slate-400 border-t border-slate-800/40 font-mono whitespace-pre-wrap bg-[#080B13]/30">${parseMarkdown(thinkingText.trim())}</div>
                    </details>
                `);
                return `WIDGETID_${id}_TOKEN`;
            });
            
            // Replace active think block with placeholder
            const openThinkIdx = processed.indexOf("<think>");
            if (openThinkIdx !== -1) {
                const thinkingText = processed.substring(openThinkIdx + 7);
                processed = processed.substring(0, openThinkIdx);
                const id = widgets.length;
                widgets.push(`
                    <details class="group mb-3 bg-[#0c101b]/60 border border-indigo-500/20 rounded-xl overflow-hidden shadow-md" open>
                        <summary class="cursor-pointer px-4 py-2 text-xs font-semibold text-slate-350 bg-[#131929]/50 hover:bg-[#1a2136]/50 transition flex items-center justify-between select-none">
                            <span class="flex items-center gap-1.5 font-mono"><i class="fa-solid fa-brain text-indigo-400 animate-pulse"></i> Thinking...</span>
                            <i class="fa-solid fa-chevron-down text-[9px] text-slate-500 group-open:rotate-180 transition-transform duration-200"></i>
                        </summary>
                        <div class="p-4 text-[11px] leading-relaxed text-slate-400 border-t border-slate-800/40 font-mono whitespace-pre-wrap bg-[#080B13]/30">${parseMarkdown(thinkingText.trim())}</div>
                    </details>
                `);
                processed += `WIDGETID_${id}_TOKEN`;
            }
            
            // Replace Tool Call logs with placeholders
            processed = processed.replace(/⚙️ \*\*\[Calling Tool: `([^`]+)` with arguments ([^\]]+)\]\*\*/g, function(match, name, args) {
                const id = widgets.length;
                widgets.push(`
                <div class="my-3.5 bg-[#090d16] border border-indigo-500/20 rounded-xl p-3 shadow-md">
                    <div class="flex items-center gap-2 text-xs font-semibold text-indigo-400 font-mono">
                        <i class="fa-solid fa-gear animate-spin text-[11px]"></i>
                        <span>CALLING TOOL: ${name}</span>
                    </div>
                    <pre class="mt-2 text-[10px] text-indigo-300 font-mono whitespace-pre-wrap bg-slate-950/40 p-2.5 rounded-lg border border-slate-800/40 overflow-x-auto">${args}</pre>
                </div>`);
                return `WIDGETID_${id}_TOKEN`;
            });
            
            // Replace Tool Result logs with placeholders
            processed = processed.replace(/📊 \*\*\[Tool Result: `([\s\S]*?)`\]\*\*/g, function(match, result) {
                let displayResult = result;
                try {
                    const parsed = JSON.parse(result);
                    displayResult = JSON.stringify(parsed, null, 2);
                } catch(e) {}
                
                const id = widgets.length;
                widgets.push(`
                <div class="my-3.5 bg-[#070b12] border border-emerald-500/20 rounded-xl p-3 shadow-md">
                    <details class="group" open>
                        <summary class="cursor-pointer flex items-center justify-between text-xs font-semibold text-emerald-400 font-mono select-none">
                            <span class="flex items-center gap-2">
                                <i class="fa-solid fa-database text-[11px]"></i>
                                <span>TOOL EXECUTION SUCCESSFUL</span>
                            </span>
                            <i class="fa-solid fa-chevron-down text-[9px] text-slate-500 group-open:rotate-180 transition-transform duration-200"></i>
                        </summary>
                        <div class="mt-2.5 border-t border-slate-800/40 pt-2">
                            <pre class="text-[10px] text-slate-300 font-mono whitespace-pre-wrap bg-slate-950/40 p-2.5 rounded-lg border border-slate-800/40 overflow-x-auto max-h-52 overflow-y-auto">${displayResult}</pre>
                        </div>
                    </details>
                </div>`);
                return `WIDGETID_${id}_TOKEN`;
            });
            
            // Replace Tool Failed logs with placeholders
            processed = processed.replace(/❌ \*\*\[Tool Execution Failed: ([^\]]+)\]\*\*/g, function(match, err) {
                const id = widgets.length;
                widgets.push(`
                <div class="my-3.5 bg-red-950/20 border border-red-500/20 rounded-xl p-3 shadow-md">
                    <div class="flex items-center gap-2 text-xs font-semibold text-red-400 font-mono">
                        <i class="fa-solid fa-triangle-exclamation text-[11px]"></i>
                        <span>TOOL EXECUTION FAILED</span>
                    </div>
                    <p class="mt-2 text-[10px] text-red-300 font-mono bg-slate-950/30 p-2 rounded border border-red-950">${err}</p>
                </div>`);
                return `WIDGETID_${id}_TOKEN`;
            });
            
            // Convert markdown for the conversational parts (keeping WIDGETID_X_TOKEN intact)
            let finalHtml = parseMarkdown(processed);
            
            // Replace placeholders back with their unescaped rich HTML widgets
            for (let i = 0; i < widgets.length; i++) {
                finalHtml = finalHtml.replace(`WIDGETID_${i}_TOKEN`, widgets[i]);
            }
            
            // If active thinking is happening, append a cursor
            if (content.includes("<think>") && !content.includes("</think>") && isGenerating) {
                finalHtml += `<span class="inline-block w-1.5 h-4 bg-slate-400 animate-pulse"></span>`;
            }
            
            return finalHtml;
        }

        // Simple HTML Markdown parser
        function parseMarkdown(text) {
            let html = text
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;");
            
            // Format code blocks
            html = html.replace(/```([\s\S]*?)```/g, function(match, code) {
                return `<pre class="bg-slate-950/80 border border-slate-800 rounded-xl p-4 my-2.5 overflow-x-auto text-xs font-mono text-emerald-400"><code>${code.trim()}</code></pre>`;
            });
            
            // Format inline code
            html = html.replace(/`([^`]+)`/g, '<code class="bg-slate-900 border border-slate-800/80 text-pink-400 px-1.5 py-0.5 rounded font-mono text-xs">$1</code>');
            
            // Bold
            html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
            
            // Italics
            html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
            
            // Unordered list
            html = html.replace(/^[\s]*-[\s]+(.+)/gm, '<li class="list-disc ml-5 my-1 text-slate-300">$1</li>');
            
            // Newlines to line breaks
            html = html.replace(/\n/g, '<br>');
            
            return html;
        }

        // Stop generation
        function stopGeneration() {
            if (activeGenerationController) {
                activeGenerationController.abort();
                appendLog("Generation request cancelled by user.");
                setGeneratingState(false);
            }
        }

        function setGeneratingState(generating) {
            isGenerating = generating;
            const stopBtn = document.getElementById("stopBtn");
            const sendBtn = document.getElementById("sendBtn");
            if (generating) {
                stopBtn.classList.remove("hidden");
                sendBtn.disabled = true;
                sendBtn.classList.add("opacity-50");
            } else {
                stopBtn.classList.add("hidden");
                sendBtn.disabled = false;
                sendBtn.classList.remove("opacity-50");
            }
        }

        // Send Message
        async function sendMessage() {
            if (isGenerating) return;
            
            const textInput = document.getElementById("userInput");
            const text = textInput.value.trim();
            if (!text && !selectedImageBase64) return;

            // Clear input & reset heights
            textInput.value = "";
            textInput.style.height = "auto";

            const chatContainer = document.getElementById("chatContainer");

            // Build user message payload & UI
            const userCard = document.createElement("div");
            userCard.className = "flex justify-end max-w-3xl ml-auto";
            
            let messageContentPayload;
            let imageTagHtml = "";
            
            if (selectedImageBase64) {
                imageTagHtml = `<img src="${selectedImageBase64}" class="max-w-[260px] max-h-[200px] object-contain rounded-xl border border-slate-700/80 mb-2.5 shadow-md">`;
                
                // Multimodal JSON structure
                messageContentPayload = [
                    { "type": "text", "text": text },
                    { "type": "image_url", "image_url": { "url": selectedImageBase64 } }
                ];
                
                chatHistory.push({ role: "user", content: messageContentPayload });
            } else {
                messageContentPayload = text;
                chatHistory.push({ role: "user", content: messageContentPayload });
            }
            localStorage.setItem("chat_history", JSON.stringify(chatHistory));

            userCard.innerHTML = `
                <div class="bg-indigo-600/35 border border-indigo-500/50 rounded-2xl p-4 shadow-lg text-slate-100 max-w-full">
                    <p class="text-xs font-semibold text-indigo-300 mb-1.5">You</p>
                    ${imageTagHtml}
                    <p class="text-sm leading-relaxed whitespace-pre-wrap">${text}</p>
                </div>
            `;
            chatContainer.appendChild(userCard);
            chatContainer.scrollTop = chatContainer.scrollHeight;

            clearSelectedImage();

            // Create Assistant empty response card
            const assistantCard = document.createElement("div");
            assistantCard.className = "flex justify-start max-w-3xl";
            const responseId = "assistant-res-" + Date.now();
            assistantCard.innerHTML = `
                <div class="bg-slate-800/40 border border-slate-700/50 rounded-2xl p-4 shadow-lg text-slate-100 max-w-full">
                    <p class="text-xs font-semibold text-purple-400 mb-1.5">Assistant</p>
                    <p id="${responseId}" class="text-sm leading-relaxed text-slate-300 font-normal">
                        <span class="inline-block w-1.5 h-4 bg-slate-400 animate-pulse"></span>
                    </p>
                </div>
            `;
            chatContainer.appendChild(assistantCard);
            chatContainer.scrollTop = chatContainer.scrollHeight;

            setGeneratingState(true);
            activeGenerationController = new AbortController();

            // Fetch settings parameters
            const sysPrompt = document.getElementById("sysPrompt").value.trim();
            const temp = document.getElementById("tempSlider").value;
            const topP = document.getElementById("topPSlider").value;
            const maxTokens = document.getElementById("maxTokensSlider").value;
            const agentMode = document.getElementById("agentToggle").checked;

            try {
                const response = await fetch("/api/chat", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        messages: chatHistory,
                        temperature: parseFloat(temp),
                        top_p: parseFloat(topP),
                        max_tokens: parseInt(maxTokens),
                        system_prompt: sysPrompt,
                        agent_mode: agentMode
                    }),
                    signal: activeGenerationController.signal
                });

                if (!response.ok) {
                    throw new Error("Generation endpoint failed.");
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                const textElement = document.getElementById(responseId);
                let assistantResponse = "";

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    
                    const chunk = decoder.decode(value, { stream: true });
                    assistantResponse += chunk;
                    
                    textElement.innerHTML = renderContent(assistantResponse);
                    chatContainer.scrollTop = chatContainer.scrollHeight;
                }

                chatHistory.push({ role: "assistant", content: assistantResponse });
                localStorage.setItem("chat_history", JSON.stringify(chatHistory));

            } catch (err) {
                if (err.name === "AbortError") {
                    const textElement = document.getElementById(responseId);
                    textElement.innerHTML += '<span class="text-xs text-yellow-500 font-semibold block mt-2">[Generation Stopped]</span>';
                } else {
                    const textElement = document.getElementById(responseId);
                    textElement.innerHTML = `<span class="text-red-400">Error: ${err.message}. Please check console logs or confirm model status.</span>`;
                    appendLog(`Streaming failed: ${err.message}`, true);
                }
            } finally {
                setGeneratingState(false);
                activeGenerationController = null;
            }
        }

        // MCP Client UI Control functions
        function toggleMcpSubpage(type) {
            const serverSub = document.getElementById("mcpServerSubpage");
            const clientSub = document.getElementById("mcpClientSubpage");
            const serverBtn = document.getElementById("mcpServerBtn");
            const clientBtn = document.getElementById("mcpClientBtn");

            if (type === 'mcpServer') {
                serverSub.classList.remove("hidden");
                clientSub.classList.add("hidden");
                serverBtn.className = "pb-3 text-sm font-semibold border-b-2 border-indigo-500 text-indigo-400";
                clientBtn.className = "pb-3 text-sm font-medium border-b-2 border-transparent text-slate-400 hover:text-slate-200";
            } else {
                serverSub.classList.add("hidden");
                clientSub.classList.remove("hidden");
                serverBtn.className = "pb-3 text-sm font-medium border-b-2 border-transparent text-slate-400 hover:text-slate-200";
                clientBtn.className = "pb-3 text-sm font-semibold border-b-2 border-indigo-500 text-indigo-400";
                fetchConnectedClients();
            }
        }

        function toggleMcpFields() {
            const type = document.getElementById("mcpConnType").value;
            const stdioCmd = document.getElementById("stdioFieldsCmd");
            const stdioArgs = document.getElementById("stdioFieldsArgs");
            const sseUrl = document.getElementById("sseFieldsUrl");

            if (type === 'stdio') {
                stdioCmd.classList.remove("hidden");
                stdioArgs.classList.remove("hidden");
                sseUrl.classList.add("hidden");
            } else {
                stdioCmd.classList.add("hidden");
                stdioArgs.classList.add("hidden");
                sseUrl.classList.remove("hidden");
            }
        }

        async function fetchConnectedClients() {
            try {
                const response = await fetch("/api/mcp/clients");
                const clients = await response.json();
                renderConnectedClients(clients);
            } catch (err) {
                appendLog(`Failed to fetch connected MCP clients: ${err.message}`, true);
            }
        }

        function renderConnectedClients(clients) {
            const container = document.getElementById("clientServersList");
            if (clients.length === 0) {
                container.innerHTML = `<p class="text-xs text-slate-500 italic py-2">No external MCP servers connected. Connect one above to expose tools to your LLM.</p>`;
                return;
            }

            container.innerHTML = "";
            clients.forEach(client => {
                const card = document.createElement("div");
                card.className = "p-4 bg-[#131929]/50 border border-slate-800 rounded-xl space-y-3.5";
                
                let detailsHtml = "";
                if (client.type === 'stdio') {
                    detailsHtml = `Command: <span class="text-slate-300 font-mono">${client.config.command} ${client.config.args || ''}</span>`;
                } else {
                    detailsHtml = `SSE URL: <span class="text-slate-300 font-mono">${client.config.url}</span>`;
                }

                let toolsHtml = "";
                if (client.tools && client.tools.length > 0) {
                    toolsHtml = `
                        <div class="mt-2.5 pt-2.5 border-t border-slate-800/40">
                            <span class="text-[10px] font-bold text-indigo-400 uppercase tracking-wider block mb-1.5">Exposed Tools (${client.tools.length}):</span>
                            <div class="flex flex-wrap gap-1.5">
                                ${client.tools.map(t => `<span class="px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 text-[10px] font-mono" title="${t.description || ''}">${t.name}</span>`).join('')}
                            </div>
                        </div>
                    `;
                } else {
                    toolsHtml = `<p class="text-[10px] text-slate-500 italic mt-2.5 pt-2.5 border-t border-slate-800/40">No tools found on this server.</p>`;
                }

                card.innerHTML = `
                    <div class="flex justify-between items-start gap-4">
                        <div class="space-y-1">
                            <div class="flex items-center gap-2">
                                <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
                                <span class="text-xs font-semibold text-slate-200">ID: ${client.id} (${client.type.toUpperCase()})</span>
                            </div>
                            <p class="text-[11px] text-slate-400 font-sans">${detailsHtml}</p>
                        </div>
                        <button onclick="disconnectExternalMcpServer('${client.id}')" class="text-[10px] font-semibold text-red-400 hover:text-red-300 bg-red-950/20 hover:bg-red-950/40 border border-red-900/30 rounded-lg px-2.5 py-1 transition flex items-center gap-1">
                            <i class="fa-solid fa-plug-circle-xmark"></i> Disconnect
                        </button>
                    </div>
                    ${toolsHtml}
                `;
                container.appendChild(card);
            });
        }

        async function connectExternalMcpServer() {
            const type = document.getElementById("mcpConnType").value;
            const command = document.getElementById("mcpConnCmd").value.trim();
            const args = document.getElementById("mcpConnArgs").value.trim();
            const url = document.getElementById("mcpConnUrl").value.trim();

            const payload = { type };
            if (type === 'stdio') {
                if (!command) {
                    showToast("Please specify a command (e.g. npx)", "warning");
                    return;
                }
                payload.command = command;
                payload.args = args;
            } else {
                if (!url) {
                    showToast("Please specify an SSE URL", "warning");
                    return;
                }
                payload.url = url;
            }

            appendLog(`Attempting connection to external MCP server (${type})...`);
            try {
                const response = await fetch("/api/mcp/clients/connect", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                
                if (!response.ok) {
                    const data = await response.json();
                    throw new Error(data.detail || "Connection failed.");
                }
                
                appendLog("Successfully connected to external MCP server!");
                
                // Clear fields
                document.getElementById("mcpConnCmd").value = "";
                document.getElementById("mcpConnArgs").value = "";
                document.getElementById("mcpConnUrl").value = "";
                
                fetchConnectedClients();
            } catch (err) {
                showToast(`Error: ${err.message}`, "error");
                appendLog(`External connection failed: ${err.message}`, true);
            }
        }

        async function disconnectExternalMcpServer(connId) {
            if (!confirm("Are you sure you want to disconnect this server?")) return;
            try {
                const response = await fetch("/api/mcp/clients/disconnect", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ conn_id: connId })
                });
                if (response.ok) {
                    appendLog(`Disconnected session ${connId}.`);
                    fetchConnectedClients();
                } else {
                    const data = await response.json();
                    throw new Error(data.detail);
                }
            } catch (err) {
                appendLog(`Disconnect failed: ${err.message}`, true);
            }
        }

        async function fetchCachedModels() {
            const listContainer = document.getElementById("cachedModelsList");
            const imageListContainer = document.getElementById("cachedImageModelsList");
            try {
                const response = await fetch("/api/models/cached");
                if (!response.ok) throw new Error("Failed to scan HF cache");
                const models = await response.json();
                renderCachedModels(models);
            } catch (err) {
                if (listContainer) listContainer.innerHTML = `<span class="text-[10px] text-red-400 italic font-sans">Scan failed: ${err.message}</span>`;
                if (imageListContainer) imageListContainer.innerHTML = `<span class="text-[10px] text-red-400 italic font-sans">Scan failed: ${err.message}</span>`;
            }
        }

        function renderCachedModels(models) {
            const container = document.getElementById("cachedModelsList");
            const imageContainer = document.getElementById("cachedImageModelsList");
            
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