# LLM Workstation

A self-contained, monolithic Python-based LLM (Large Language Model) workstation designed for Google Colab, GPU servers, or local machines. It packages a FastAPI backend, a dynamic 4-bit Hugging Face model loader, NVML GPU telemetry, and an automated Cloudflare Tunnel to expose your workstation to the public web with a single command.

---

## Features

1. **Monolithic & Portable**: The entire backend, telemetry engine, Cloudflare tunnel automations, and a fully featured responsive dark-mode SPA (Single Page Application) frontend are contained inside `app.py`.
2. **GPU Telemetry Dashboard**:
   - Real-time VRAM gauge tracking memory consumption (Used / Total MB) using `pynvml`.
   - Live token generation speed counter (tokens/s).
   - Dynamic RAM tracking if running on CPU.
3. **Advanced Hugging Face Manager**:
   - Hot-swap models on-the-fly via the UI or endpoint.
   - Clears GPU memory completely using garbage collection (`gc.collect()`) and PyTorch cache purges (`torch.cuda.empty_cache()`) before loading a new model.
   - 4-bit BitsAndBytes quantization (`load_in_4bit=True`, `nf4` double quantization) configured automatically for T4/GPU memory optimization.
4. **OpenAI-Compatible API**:
   - Implements standard `/v1/chat/completions` with streaming and non-streaming options. Allows you to hook the workstation directly to tools like VS Code Continue, Open WebUI, LibreChat, or other OpenAI client wrappers.
5. **Auto Cloudflare Tunneling**:
   - Automatically detects OS (Windows, Linux, MacOS) and CPU architecture, downloads the correct `cloudflared` binary, launches the tunnel in the background, extracts the dynamic `.trycloudflare.com` URL, and displays it inside both the terminal and UI dashboard.

---

## Quickstart

### 1. Install Dependencies

Install the requirements from the provided `requirements.txt` file:

```bash
pip install -r requirements.txt
```

*Note: On GPU servers or Google Colab, make sure you have PyTorch installed with CUDA support. You can install the correct PyTorch package according to your CUDA runtime.*

### 2. Run the Workstation

Start the server using a single command:

```bash
python app.py
```

By default, the server will:
- Bind to `0.0.0.0:8000`.
- Automatically download and run `cloudflared` in the background to generate a public URL.
- Load `Qwen/Qwen2.5-1.5B-Instruct` as the default model (lightweight, fast, and supports chat templates).

---

## Command Line Arguments

You can configure the workstation on startup using arguments:

```bash
python app.py --port 8080 --model microsoft/Phi-3-mini-4k-instruct
```

| Argument | Type | Default | Description |
|---|---|---|---|
| `--host` | `str` | `0.0.0.0` | IP Address to bind uvicorn server. |
| `--port` | `int` | `8000` | Port to run uvicorn server. |
| `--model` | `str` | `Qwen/Qwen2.5-1.5B-Instruct` | Default Hugging Face Model ID to preload on startup. |
| `--no-tunnel` | `flag`| (None) | Disable automated Cloudflare Quick Tunnel. |

---

## API Endpoints

The workstation exposes standard endpoints:

- **Web UI**: `GET /`
- **Telemetry**: `GET /api/telemetry`
- **Load Model**: `POST /api/model/load` `{"model_id": "HuggingFace_ID"}`
- **Unload Model**: `POST /api/model/unload`
- **UI Chat Stream**: `POST /api/chat` (consumes payload and streams plain-text tokens)
- **OpenAI chat completions**: `POST /v1/chat/completions` (OpenAI compatible request/response formatting, supports `stream: true` and `stream: false`)

### Example: Connecting to Continue (VS Code extension)

To use your running workstation inside the VS Code **Continue** extension, add the following to your `config.json`:

```json
{
  "models": [
    {
      "title": "LLM Workstation",
      "provider": "openai",
      "model": "local-model",
      "apiBase": "https://YOUR-SUBDOMAIN.trycloudflare.com/v1"
    }
  ]
}
```

Replace `YOUR-SUBDOMAIN` with the `.trycloudflare.com` URL printed in your workstation console.
