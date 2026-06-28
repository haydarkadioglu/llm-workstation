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
    while (chatContainer.children.length > 1) {
        chatContainer.removeChild(chatContainer.lastChild);
    }
    appendLog("Chat history cleared.");
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
            <details class="group mb-1.5 bg-[#0c101b]/60 border border-slate-800/80 rounded-xl overflow-hidden shadow-md">
                <summary class="cursor-pointer px-3.5 py-1.5 text-xs font-semibold text-slate-400 bg-[#131929]/50 hover:bg-[#1a2136]/50 transition flex items-center justify-between select-none">
                    <span class="flex items-center gap-1.5 font-mono"><i class="fa-solid fa-brain text-indigo-400"></i> Thinking Process</span>
                    <i class="fa-solid fa-chevron-down text-[9px] text-slate-500 group-open:rotate-180 transition-transform duration-200"></i>
                </summary>
                <div class="p-3.5 text-[11px] leading-relaxed text-slate-400 border-t border-slate-800/40 font-mono whitespace-pre-wrap bg-[#080B13]/30">${parseMarkdown(thinkingText.trim())}</div>
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
            <details class="group mb-1.5 bg-[#0c101b]/60 border border-indigo-500/20 rounded-xl overflow-hidden shadow-md" open>
                <summary class="cursor-pointer px-3.5 py-1.5 text-xs font-semibold text-slate-350 bg-[#131929]/50 hover:bg-[#1a2136]/50 transition flex items-center justify-between select-none">
                    <span class="flex items-center gap-1.5 font-mono"><i class="fa-solid fa-brain text-indigo-400 animate-pulse"></i> Thinking...</span>
                    <i class="fa-solid fa-chevron-down text-[9px] text-slate-500 group-open:rotate-180 transition-transform duration-200"></i>
                </summary>
                <div class="p-3.5 text-[11px] leading-relaxed text-slate-400 border-t border-slate-800/40 font-mono whitespace-pre-wrap bg-[#080B13]/30">${parseMarkdown(thinkingText.trim())}</div>
            </details>
        `);
        processed += `WIDGETID_${id}_TOKEN`;
    }
    
    // Replace Tool Call logs with placeholders
    processed = processed.replace(/⚙️ \*\*\[Calling Tool: `([^`]+)` with arguments ([^\]]+)\]\*\*/g, function(match, name, args) {
        const id = widgets.length;
        widgets.push(`
        <div class="my-1.5 bg-[#090d16] border border-indigo-500/20 rounded-xl p-2.5 shadow-md">
            <div class="flex items-center gap-2 text-xs font-semibold text-indigo-400 font-mono">
                <i class="fa-solid fa-gear animate-spin text-[11px]"></i>
                <span>CALLING TOOL: ${name}</span>
            </div>
            <pre class="mt-1.5 text-[10px] text-indigo-300 font-mono whitespace-pre-wrap bg-slate-950/40 p-2 rounded-lg border border-slate-800/40 overflow-x-auto">${args}</pre>
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
        <div class="my-1.5 bg-[#070b12] border border-emerald-500/20 rounded-xl p-2.5 shadow-md">
            <details class="group" open>
                <summary class="cursor-pointer flex items-center justify-between text-xs font-semibold text-emerald-400 font-mono select-none">
                    <span class="flex items-center gap-2">
                        <i class="fa-solid fa-database text-[11px]"></i>
                        <span>TOOL EXECUTION SUCCESSFUL</span>
                    </span>
                    <i class="fa-solid fa-chevron-down text-[9px] text-slate-500 group-open:rotate-180 transition-transform duration-200"></i>
                </summary>
                <div class="mt-2 border-t border-slate-800/40 pt-2">
                    <pre class="text-[10px] text-slate-300 font-mono whitespace-pre-wrap bg-slate-950/40 p-2 rounded-lg border border-slate-800/40 overflow-x-auto max-h-52 overflow-y-auto">${displayResult}</pre>
                </div>
            </details>
        </div>`);
        return `WIDGETID_${id}_TOKEN`;
    });
    
    // Replace Tool Failed logs with placeholders
    processed = processed.replace(/❌ \*\*\[Tool Execution Failed: ([^\]]+)\]\*\*/g, function(match, err) {
        const id = widgets.length;
        widgets.push(`
        <div class="my-1.5 bg-red-950/20 border border-red-500/20 rounded-xl p-2.5 shadow-md">
            <div class="flex items-center gap-2 text-xs font-semibold text-red-400 font-mono">
                <i class="fa-solid fa-triangle-exclamation text-[11px]"></i>
                <span>TOOL EXECUTION FAILED</span>
            </div>
            <p class="mt-1.5 text-[10px] text-red-300 font-mono bg-slate-950/30 p-2 rounded border border-red-950">${err}</p>
        </div>`);
        return `WIDGETID_${id}_TOKEN`;
    });
    
    let finalHtml = parseMarkdown(processed);
    
    for (let i = 0; i < widgets.length; i++) {
        finalHtml = finalHtml.replace(`WIDGETID_${i}_TOKEN`, widgets[i]);
    }
    
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

    textInput.value = "";
    textInput.style.height = "auto";

    const chatContainer = document.getElementById("chatContainer");
    const userCard = document.createElement("div");
    userCard.className = "flex justify-end max-w-3xl ml-auto";
    
    let messageContentPayload;
    let imageTagHtml = "";
    
    if (selectedImageBase64) {
        imageTagHtml = `<img src="${selectedImageBase64}" class="max-w-[260px] max-h-[200px] object-contain rounded-xl border border-slate-700/80 mb-2.5 shadow-md">`;
        
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
        updateMcpStatusBadge(clients);
    } catch (err) {
        appendLog(`Failed to fetch connected MCP clients: ${err.message}`, true);
    }
}

function updateMcpStatusBadge(clients) {
    const badge = document.getElementById("mcpChatBadge");
    const label = document.getElementById("mcpChatBadgeLabel");
    const dot = document.getElementById("mcpChatBadgeDot");
    if (!badge) return;
    const totalTools = clients.reduce((sum, c) => sum + (c.tools ? c.tools.length : 0), 0);
    const serverCount = clients.length;
    if (serverCount === 0) {
        badge.className = "flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-slate-800/30 border border-slate-800 text-xs text-slate-500 font-mono";
        dot.className = "w-1.5 h-1.5 rounded-full bg-slate-600";
        label.textContent = "MCP: Not Connected";
    } else {
        badge.className = "flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-emerald-950/30 border border-emerald-700/40 text-xs text-emerald-400 font-mono transition";
        dot.className = "w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse";
        label.textContent = `MCP: ${serverCount} server${serverCount > 1 ? 's' : ''} · ${totalTools} tool${totalTools !== 1 ? 's' : ''}`;
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

// ─── Google Drive Integration ─────────────────────────────────────────────────

async function fetchDriveStatus() {
    try {
        const resp = await fetch("/api/drive/status");
        const data = await resp.json();
        updateDriveBadges(data);
        applyDriveStatusToUI(data);
    } catch (err) {
        // Non-critical, Drive may not be available
    }
}

function updateDriveBadges(data) {
    // Chat header badge
    const badge = document.getElementById("driveChatBadge");
    const dot   = document.getElementById("driveChatBadgeDot");
    const label = document.getElementById("driveChatBadgeLabel");
    if (!badge) return;

    if (!data.mounted) {
        badge.className = "flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-slate-800/30 border border-slate-800 text-xs text-slate-500 font-mono";
        dot.className   = "w-1.5 h-1.5 rounded-full bg-slate-600";
        label.textContent = "Drive: Not Mounted";
    } else if (!data.drive_mode) {
        badge.className = "flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-yellow-950/20 border border-yellow-700/30 text-xs text-yellow-600 font-mono";
        dot.className   = "w-1.5 h-1.5 rounded-full bg-yellow-600";
        label.textContent = "Drive: Mounted";
    } else {
        const count = data.cached_models ? data.cached_models.length : 0;
        badge.className = "flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-yellow-950/30 border border-yellow-500/40 text-xs text-yellow-400 font-mono";
        dot.className   = "w-1.5 h-1.5 rounded-full bg-yellow-400 animate-pulse";
        label.textContent = `Drive: ${count} model${count !== 1 ? "s" : ""}`;
    }

    // Settings page badge
    const settingsDot   = document.getElementById("driveMountDot");
    const settingsLabel = document.getElementById("driveMountLabel");
    const settingsBadge = document.getElementById("driveMountBadge");
    if (settingsDot && settingsLabel && settingsBadge) {
        if (data.mounted) {
            settingsBadge.className = "flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-emerald-950/30 border border-emerald-700/40 text-[10px] font-mono text-emerald-400";
            settingsDot.className   = "w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse";
            settingsLabel.textContent = "Drive Mounted";
        } else {
            settingsBadge.className = "flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-slate-800/60 border border-slate-700 text-[10px] font-mono text-slate-500";
            settingsDot.className   = "w-1.5 h-1.5 rounded-full bg-slate-600";
            settingsLabel.textContent = "Drive Not Found";
        }
    }
}

function applyDriveStatusToUI(data) {
    const toggle = document.getElementById("driveModeToggle");
    const pathRow = document.getElementById("drivePathRow");
    const cachedSection = document.getElementById("driveCachedSection");

    if (toggle) {
        toggle.disabled = !data.mounted;
        toggle.checked  = data.drive_mode;
    }
    if (pathRow) {
        if (data.drive_mode && data.drive_path) {
            pathRow.classList.remove("hidden");
            const pathLabel = document.getElementById("drivePathLabel");
            if (pathLabel) pathLabel.textContent = data.drive_path;
        } else {
            pathRow.classList.add("hidden");
        }
    }
    if (cachedSection) {
        if (data.drive_mode) {
            cachedSection.classList.remove("hidden");
            renderDriveCachedModels(data.cached_models || []);
        } else {
            cachedSection.classList.add("hidden");
        }
    }
}

async function toggleDriveMode(enabled) {
    try {
        const resp = await fetch("/api/drive/mode", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled })
        });
        if (!resp.ok) {
            const err = await resp.json();
            showToast(err.detail || "Failed to toggle Drive mode", "error");
            // Revert toggle
            const toggle = document.getElementById("driveModeToggle");
            if (toggle) toggle.checked = !enabled;
            return;
        }
        showToast(enabled ? "Drive mode enabled — models will be saved to Drive" : "Drive mode disabled", enabled ? "success" : "info");
        await fetchDriveStatus();
        if (enabled) fetchDriveCachedModels();
    } catch (err) {
        showToast(`Drive toggle error: ${err.message}`, "error");
    }
}

async function fetchDriveCachedModels() {
    const list = document.getElementById("driveCachedList");
    if (!list) return;
    list.innerHTML = `<span class="text-[10px] text-slate-500 italic animate-pulse">Scanning Drive...</span>`;
    try {
        const resp = await fetch("/api/drive/cached-models");
        const data = await resp.json();
        renderDriveCachedModels(data.models || []);
    } catch (err) {
        list.innerHTML = `<span class="text-[10px] text-red-400 italic">Failed to scan Drive: ${err.message}</span>`;
    }
}

function renderDriveCachedModels(models) {
    const list = document.getElementById("driveCachedList");
    if (!list) return;
    if (!models || models.length === 0) {
        list.innerHTML = `<span class="text-[10px] text-slate-500 italic">No models found in Drive yet.</span>`;
        return;
    }
    list.innerHTML = models.map(modelId => `
        <div class="flex items-center justify-between bg-[#080B13] border border-slate-800/60 rounded-xl px-3 py-2">
            <div class="flex items-center gap-2 min-w-0">
                <i class="fa-solid fa-box-archive text-yellow-400 text-[10px] shrink-0"></i>
                <span class="text-[11px] font-mono text-slate-300 truncate" title="${modelId}">${modelId}</span>
            </div>
            <button
                onclick="loadModelFromDrive('${modelId}')"
                class="shrink-0 ml-2 text-[10px] font-semibold text-indigo-400 hover:text-indigo-300 bg-indigo-950/30 hover:bg-indigo-950/50 border border-indigo-700/30 rounded-lg px-2.5 py-1 transition flex items-center gap-1">
                <i class="fa-solid fa-upload text-[9px]"></i> Load
            </button>
        </div>
    `).join('');
}

function loadModelFromDrive(modelId) {
    // Pre-fill model input and trigger load
    const modelInput = document.getElementById("modelInput");
    if (modelInput) {
        modelInput.value = modelId;
        showToast(`Loading ${modelId} from Drive...`, "info");
        triggerModelLoad();
        // Switch to chat view
        switchView('chatView');
    }
}
// ──────────────────────────────────────────────────────────────────────────────
