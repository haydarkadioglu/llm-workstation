import os
import gc
import re
import time
import base64
import json
import threading
from io import BytesIO
from typing import List, Dict, Any, Optional
from PIL import Image

try:
    import torch
except ImportError:
    pass

import tqdm

# ─── Google Drive Integration ─────────────────────────────────────────────────
DRIVE_ROOT = "/content/drive/MyDrive"
DRIVE_LLM_DIR = "/content/drive/MyDrive/LLM-Workstation/models"
_use_drive = False  # Global toggle — set via set_drive_mode()

def is_drive_mounted() -> bool:
    """Check if Google Drive is mounted at the standard Colab path."""
    return os.path.isdir(DRIVE_ROOT)

def set_drive_mode(enabled: bool) -> dict:
    """Enable or disable Drive-backed model storage.
    When enabled, sets HF_HOME so all HF downloads go to Drive."""
    global _use_drive
    _use_drive = enabled
    if enabled:
        if not is_drive_mounted():
            return {"ok": False, "error": "Google Drive is not mounted. Run drive.mount('/content/drive') in Colab first."}
        os.makedirs(DRIVE_LLM_DIR, exist_ok=True)
        os.environ["HF_HOME"] = DRIVE_LLM_DIR
        os.environ["TRANSFORMERS_CACHE"] = DRIVE_LLM_DIR
        print(f"[Drive] Drive mode ENABLED. Cache dir: {DRIVE_LLM_DIR}")
        return {"ok": True, "drive_path": DRIVE_LLM_DIR}
    else:
        os.environ.pop("HF_HOME", None)
        os.environ.pop("TRANSFORMERS_CACHE", None)
        print("[Drive] Drive mode DISABLED. Using default HF cache.")
        return {"ok": True, "drive_path": None}

def get_drive_status() -> dict:
    """Return current Drive state for the UI."""
    mounted = is_drive_mounted()
    cached = get_drive_cached_models() if mounted and _use_drive else []
    return {
        "mounted": mounted,
        "drive_mode": _use_drive,
        "drive_path": DRIVE_LLM_DIR if _use_drive else None,
        "cached_models": cached,
    }

def get_drive_cached_models() -> list:
    """Scan the Drive LLM folder and return list of HF model IDs found."""
    if not is_drive_mounted() or not os.path.isdir(DRIVE_LLM_DIR):
        return []
    results = []
    # HF cache layout: models--org--reponame/snapshots/<hash>/
    for entry in os.scandir(DRIVE_LLM_DIR):
        if not entry.is_dir():
            continue
        name = entry.name
        if name.startswith("models--"):
            # Standard HF cache format: models--org--reponame
            hf_id = name[len("models--"):].replace("--", "/", 1)
            results.append(hf_id)
        elif os.path.isfile(os.path.join(entry.path, "config.json")):
            # Flat download style
            results.append(name)
    return results
# ──────────────────────────────────────────────────────────────────────────────

class CancellationException(Exception):
    pass

# Global reference to hook progress bar tracking
_global_manager_ref = None

# Store original tqdm methods for monkey patching to intercept downloads in dependencies
import tqdm
_original_tqdm_init = tqdm.tqdm.__init__
_original_tqdm_update = tqdm.tqdm.update

def _patched_tqdm_init(self, *args, **kwargs):
    _original_tqdm_init(self, *args, **kwargs)
    desc = kwargs.get("desc", "") or ""
    global _global_manager_ref
    if _global_manager_ref:
        desc_lower = desc.lower()
        if "loading" in desc_lower or "shard" in desc_lower:
            _global_manager_ref.status = f"Loading: {desc}"
        else:
            _global_manager_ref.status = f"Downloading: {desc}" if desc else "Downloading..."
        _global_manager_ref.loading_progress = 0
        _global_manager_ref.loading_speed = ""

def _patched_tqdm_update(self, n=1):
    _original_tqdm_update(self, n)
    global _global_manager_ref
    if _global_manager_ref:
        mgr = _global_manager_ref
        if self.total and self.total > 0:
            pct = int((self.n / self.total) * 100)
            mgr.loading_progress = pct
            
            desc_lower = (self.desc or "").lower()
            is_loading_phase = "loading" in desc_lower or "shard" in desc_lower
            
            rate = self.format_dict.get("rate")
            if rate is not None and not is_loading_phase:
                if rate > 1024 * 1024:
                    mgr.loading_speed = f"{rate / (1024 * 1024):.1f} MB/s"
                elif rate > 1024:
                    mgr.loading_speed = f"{rate / 1024:.1f} KB/s"
                else:
                    mgr.loading_speed = f"{rate:.1f} B/s"
            else:
                mgr.loading_speed = ""
            
            if is_loading_phase:
                mgr.status = f"Loading: {pct}%"
            else:
                speed_str = f" ({mgr.loading_speed})" if mgr.loading_speed else ""
                mgr.status = f"Downloading: {pct}%{speed_str}"

def decode_base64_image(base64_str: str) -> Image.Image:
    if "," in base64_str:
        base64_str = base64_str.split(",")[1]
    image_data = base64.b64decode(base64_str)
    return Image.open(BytesIO(image_data)).convert("RGB")
BUILTIN_TOOLS = [
    {
        "name": "web_search",
        "description": "Search the web/internet using DuckDuckGo to find real-time info, news, current events, or documentation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search keywords or query"
                }
            },
            "required": ["query"]
        }
    }
]

def builtin_web_search(query: str) -> list:
    import urllib.request
    import urllib.parse
    import json
    
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as response:
            html_data = response.read().decode("utf-8", errors="ignore")
            
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_data, "html.parser")
            results = []
            for item in soup.find_all("div", class_="result__body"):
                title_a = item.find("a", class_="result__url")
                snippet_a = item.find("a", class_="result__snippet")
                if title_a and snippet_a:
                    results.append({
                        "title": title_a.get_text(strip=True),
                        "link": title_a["href"],
                        "snippet": snippet_a.get_text(strip=True)
                    })
                if len(results) >= 5:
                    break
            return results
        except ImportError:
            import re
            results = []
            bodies = re.findall(r'<div class="result__body">([\s\S]*?)</div>\s*</div>', html_data)
            for body in bodies:
                title_match = re.search(r'href="([^"]+)"[^>]*class="[^"]*result__url[^"]*"[^>]*>([\s\S]*?)</a>', body)
                snippet_match = re.search(r'class="[^"]*result__snippet[^"]*"[^>]*>([\s\S]*?)</a>', body)
                if title_match and snippet_match:
                    title = re.sub(r'<[^>]+>', '', title_match.group(2)).strip()
                    link = title_match.group(1).strip()
                    snippet = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip()
                    results.append({
                        "title": title,
                        "link": link,
                        "snippet": snippet
                    })
                if len(results) >= 5:
                    break
            return results if results else [{"title": "Search", "snippet": "No results matched fallback regex."}]
    except Exception as e:
        print(f"[Built-in Search Error] Failed: {e}")
        return [{"error": f"Search failed: {str(e)}"}]

def parse_tool_call_json(text: str) -> Optional[dict]:
    text = text.strip()
    
    # Try finding markdown code block: ```json ... ``` or ``` ... ```
    json_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if json_match:
        try:
            parsed = json.loads(json_match.group(1))
            if "tool" in parsed:
                return parsed
        except Exception:
            pass
            
    # Try parsing raw string bounding braces
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            parsed = json.loads(text[start:end+1])
            if "tool" in parsed:
                return parsed
    except Exception:
        pass
        
    return None

class ModelManager:
    def __init__(self):
        global _global_manager_ref
        self.model = None
        self.tokenizer = None
        self.processor = None
        self.image_pipeline = None
        self.model_id = None
        self.is_vision = False
        self.status = "No model loaded"
        self.loading_progress = 0
        self.loading_speed = ""
        self.error_message = None
        self.last_tokens_per_sec = 0.0
        self.lock = threading.Lock()
        
        _global_manager_ref = self

    def unload_current_model(self):
        print("[ModelManager] Purging GPU/RAM allocations...")
        self.model = None
        self.tokenizer = None
        self.processor = None
        self.image_pipeline = None
        self.model_id = None
        self.is_vision = False
        
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            print("[ModelManager] PyTorch CUDA cache empty.")
            
        self.status = "No model loaded"
        self.loading_progress = 0
        self.loading_speed = ""
        self.error_message = None
        self.last_tokens_per_sec = 0.0
        print("[ModelManager] Unload complete.")

    def load_model(self, model_id: str, hf_token: Optional[str] = None, model_type: str = "text"):
        with self.lock:
            try:
                # Normalize empty string tokens to None to prevent Hugging Face Hub rejection
                if hf_token is not None:
                    hf_token = hf_token.strip()
                    if not hf_token:
                        hf_token = None
                
                if hf_token:
                    os.environ["HF_TOKEN"] = hf_token
                else:
                    os.environ.pop("HF_TOKEN", None)

                self.unload_current_model()

                self.status = f"Preparing model '{model_id}'..."
                self.loading_progress = 0
                self.loading_speed = ""
                self.error_message = None
                
                if model_type == "video":
                    self.status = "Downloading/Verifying repository..."
                    try:
                        tqdm.tqdm.__init__ = _patched_tqdm_init
                        tqdm.tqdm.update = _patched_tqdm_update
                        
                        import diffusers
                        print(f"[ModelManager] Loading video pipeline for '{model_id}'...")
                        self.image_pipeline = diffusers.DiffusionPipeline.from_pretrained(
                            model_id,
                            torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
                            token=hf_token
                        )
                        if torch.cuda.is_available():
                            self.image_pipeline.to("cuda")
                            
                        self.model_id = model_id
                        self.status = "Ready"
                        self.loading_progress = 100
                        print(f"[ModelManager] Video pipeline '{model_id}' successfully loaded.")
                        return True
                    finally:
                        tqdm.tqdm.__init__ = _original_tqdm_init
                        tqdm.tqdm.update = _original_tqdm_update
                        
                elif model_type == "image":
                    self.status = "Downloading/Verifying repository..."
                    try:
                        tqdm.tqdm.__init__ = _patched_tqdm_init
                        tqdm.tqdm.update = _patched_tqdm_update
                        
                        import diffusers
                        print(f"[ModelManager] Loading Stable Diffusion pipeline for '{model_id}'...")
                        
                        # Fallback chain to support custom and famous pipelines
                        loaded_ok = False
                        errs = []
                        
                        # 1. Try AutoPipelineForText2Image (handles most standard text-to-image architectures)
                        try:
                            print(f"[ModelManager] Attempting AutoPipelineForText2Image...")
                            self.image_pipeline = diffusers.AutoPipelineForText2Image.from_pretrained(
                                model_id,
                                torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
                                use_safetensors=True,
                                token=hf_token
                            )
                            loaded_ok = True
                        except Exception as e:
                            errs.append(f"AutoPipeline: {str(e)}")
                            
                        # 2. Try StableDiffusionPipeline (fallback for SD 1.4/1.5/2.0/2.1 if custom pipeline fails)
                        if not loaded_ok:
                            try:
                                print(f"[ModelManager Warning] AutoPipeline failed. Falling back to StableDiffusionPipeline...")
                                self.image_pipeline = diffusers.StableDiffusionPipeline.from_pretrained(
                                    model_id,
                                    torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
                                    token=hf_token
                                )
                                loaded_ok = True
                            except Exception as e:
                                errs.append(f"StableDiffusionPipeline: {str(e)}")
                                
                        # 3. Try StableDiffusionXLPipeline (fallback for SDXL if custom pipeline fails)
                        if not loaded_ok:
                            try:
                                print(f"[ModelManager Warning] SD 1.5 failed. Falling back to StableDiffusionXLPipeline...")
                                self.image_pipeline = diffusers.StableDiffusionXLPipeline.from_pretrained(
                                    model_id,
                                    torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
                                    token=hf_token
                                )
                                loaded_ok = True
                            except Exception as e:
                                errs.append(f"StableDiffusionXLPipeline: {str(e)}")
                                
                        # 4. Try generic DiffusionPipeline (fallback for other pipelines like Flux, Latent Consistency Models, etc.)
                        if not loaded_ok:
                            try:
                                print(f"[ModelManager Warning] SDXL failed. Falling back to generic DiffusionPipeline...")
                                self.image_pipeline = diffusers.DiffusionPipeline.from_pretrained(
                                    model_id,
                                    torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
                                    token=hf_token
                                )
                                loaded_ok = True
                            except Exception as e:
                                errs.append(f"DiffusionPipeline: {str(e)}")
                                raise ValueError(f"Failed to load image model. Errors: {'; '.join(errs)}")
                                
                        if torch.cuda.is_available():
                            self.image_pipeline.to("cuda")
                            
                        self.model_id = model_id
                        self.status = "Ready"
                        self.loading_progress = 100
                        print(f"[ModelManager] Diffusion pipeline '{model_id}' successfully loaded.")
                        return True
                    finally:
                        tqdm.tqdm.__init__ = _original_tqdm_init
                        tqdm.tqdm.update = _original_tqdm_update
                
                self.model = None
                self.tokenizer = None
                self.processor = None
                self.is_vision = False
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                
                print(f"[ModelManager] Querying config for '{model_id}'...")
                from transformers import AutoConfig
                config = AutoConfig.from_pretrained(model_id, trust_remote_code=True, token=hf_token)
                
                model_type = getattr(config, "model_type", "").lower()
                is_vl = any(vt in model_type for vt in ["vision", "vl", "mllama", "llava", "paligemma", "internvl"])
                name_vl = any(vt in model_id.lower() for vt in ["vision", "-vl-", "llava", "paligemma"])
                self.is_vision = is_vl or name_vl
                print(f"[ModelManager] Model is Multimodal/Vision: {self.is_vision} (Type: {model_type})")
                
                # Check for pre-existing quantization in model config
                has_pre_quant = False
                config_dict = config.to_dict() if hasattr(config, "to_dict") else {}
                if "quantization_config" in config_dict:
                    has_pre_quant = True
                    print(f"[ModelManager] Model is pre-quantized (found 'quantization_config' in config). Bypassing BitsAndBytesConfig...")

                try:
                    tqdm.tqdm.__init__ = _patched_tqdm_init
                    tqdm.tqdm.update = _patched_tqdm_update
                    
                    print(f"[ModelManager] Downloading/Verifying repository for '{model_id}'...{'  [Drive mode]' if _use_drive and is_drive_mounted() else ''}")
                    from huggingface_hub import snapshot_download
                    _drive_cache = DRIVE_LLM_DIR if _use_drive and is_drive_mounted() else None
                    local_dir = snapshot_download(
                        repo_id=model_id,
                        cache_dir=_drive_cache,
                        allow_patterns=["*.json", "*.bin", "*.safetensors", "*.model", "*.txt", "*.py"],
                        ignore_patterns=["*.msgpack", "*.h5", "*.ot"],
                        resume_download=True,
                        token=hf_token
                    )
                    
                    self.status = f"Loading model weights into memory..."
                    print(f"[ModelManager] Loading model from {local_dir}...")
                    
                    from transformers import AutoTokenizer, AutoModelForCausalLM, AutoProcessor
                    
                    if self.is_vision:
                        try:
                            self.processor = AutoProcessor.from_pretrained(local_dir, trust_remote_code=True)
                        except Exception as e:
                            print(f"[ModelManager Warning] Could not load AutoProcessor: {e}. Falling back to standard tokenizer.")
                            self.tokenizer = AutoTokenizer.from_pretrained(local_dir, trust_remote_code=True)
                    else:
                        self.tokenizer = AutoTokenizer.from_pretrained(local_dir, trust_remote_code=True)
                        if self.tokenizer.pad_token is None:
                            self.tokenizer.pad_token = self.tokenizer.eos_token
                    
                    if torch.cuda.is_available():
                        if has_pre_quant:
                            print("[ModelManager] Loading pre-quantized model directly on GPU...")
                            try:
                                self.model = AutoModelForCausalLM.from_pretrained(
                                    local_dir,
                                    device_map="auto",
                                    trust_remote_code=True
                                )
                            except Exception as p_err:
                                print(f"[ModelManager Warning] Loading pre-quantized model failed: {p_err}")
                                print("[ModelManager] Retrying model load without device_map...")
                                self.model = AutoModelForCausalLM.from_pretrained(
                                    local_dir,
                                    trust_remote_code=True
                                )
                        else:
                            print("[ModelManager] CUDA available. Attempting 4-bit BitsAndBytes quantization...")
                            try:
                                from transformers import BitsAndBytesConfig
                                quant_config = BitsAndBytesConfig(
                                    load_in_4bit=True,
                                    bnb_4bit_quant_type="nf4",
                                    bnb_4bit_use_double_quant=True,
                                    bnb_4bit_compute_dtype=torch.float16
                                )
                                
                                self.model = AutoModelForCausalLM.from_pretrained(
                                    local_dir,
                                    quantization_config=quant_config,
                                    device_map="auto",
                                    trust_remote_code=True
                                )
                            except Exception as q_err:
                                print(f"[ModelManager Warning] 4-bit Quantization failed: {q_err}")
                                print("[ModelManager] Loading model in half-precision 16-bit...")
                                self.model = AutoModelForCausalLM.from_pretrained(
                                    local_dir,
                                    device_map="auto",
                                    torch_dtype=torch.float16,
                                    trust_remote_code=True
                                )
                    else:
                        print("[ModelManager] GPU not found. Loading model on CPU (Un-quantized, slow)...")
                        self.model = AutoModelForCausalLM.from_pretrained(
                            local_dir,
                            device_map="cpu",
                            trust_remote_code=True
                        )
                    
                    self.model_id = model_id
                    self.status = "Ready"
                    self.loading_progress = 100
                    print(f"[ModelManager] Model '{model_id}' successfully loaded.")
                    return True
                finally:
                    tqdm.tqdm.__init__ = _original_tqdm_init
                    tqdm.tqdm.update = _original_tqdm_update
            except Exception as e:
                self.status = "Error loading model"
                self.error_message = str(e)
                print(f"[ModelManager Error] Load sequence failed: {e}")
                return False

    def generate_image(self, prompt: str, negative_prompt: str = "", steps: int = 25, guidance_scale: float = 7.5, width: int = 512, height: int = 512):
        if not self.image_pipeline:
            raise ValueError("No image pipeline is loaded. Please load a diffusion model first.")
            
        with self.lock:
            import torch
            with torch.inference_mode():
                result = self.image_pipeline(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    num_inference_steps=steps,
                    guidance_scale=guidance_scale,
                    width=width,
                    height=height
                )
                return result.images[0]

    def generate_video(self, prompt: str, negative_prompt: str = "", steps: int = 20, frames: int = 16, fps: int = 8, image: str = None, progress_callback = None, check_cancelled = None):
        if not self.image_pipeline:
            raise ValueError("No video pipeline is loaded. Please load a video model first.")
            
        with self.lock:
            import torch
            from PIL import Image
            import time
            from io import BytesIO
            import base64
            
            generator = torch.Generator("cuda").manual_seed(int(time.time() * 1000) % 1000000) if torch.cuda.is_available() else None
            
            print(f"[ModelManager] Generating video frames for prompt: {prompt}")
            with torch.inference_mode():
                kwargs = {
                    "prompt": prompt,
                    "num_inference_steps": steps,
                    "num_frames": frames
                }
                if negative_prompt:
                    kwargs["negative_prompt"] = negative_prompt
                if generator:
                    kwargs["generator"] = generator
                    
                if image:
                    if "," in image:
                        image = image.split(",")[1]
                    img_data = base64.b64decode(image)
                    pil_image = Image.open(BytesIO(img_data)).convert("RGB")
                    
                    # Auto-resize to a friendly resolution (max 704px on longest edge) and align to multiples of 16
                    max_size = 704
                    w, h = pil_image.size
                    if w > max_size or h > max_size:
                        if w > h:
                            new_w = max_size
                            new_h = int(h * (max_size / w))
                        else:
                            new_h = max_size
                            new_w = int(w * (max_size / h))
                    else:
                        new_w, new_h = w, h
                        
                    new_w = (new_w // 16) * 16
                    new_h = (new_h // 16) * 16
                    new_w = max(new_w, 16)
                    new_h = max(new_h, 16)
                    
                    if (new_w, new_h) != (w, h):
                        pil_image = pil_image.resize((new_w, new_h), Image.Resampling.LANCZOS)
                        print(f"[ModelManager] Auto-resized input image from {w}x{h} to {new_w}x{new_h} to fit model constraints.")
                    
                    import inspect
                    sig = inspect.signature(self.image_pipeline.__call__)
                    if "image" in sig.parameters:
                        kwargs["image"] = pil_image
                    elif "init_image" in sig.parameters:
                        kwargs["init_image"] = pil_image
                    else:
                        print("[ModelManager Warning] Current video pipeline does not accept 'image' or 'init_image'. Ignoring input image.")
                        
                    if "width" in sig.parameters:
                        kwargs["width"] = new_w
                    if "height" in sig.parameters:
                        kwargs["height"] = new_h
                    
                if progress_callback or check_cancelled:
                    import inspect
                    sig = inspect.signature(self.image_pipeline.__call__)
                    if "callback_on_step_end" in sig.parameters:
                        def step_end_callback(pipe, step_index, timestep, callback_kwargs):
                            if check_cancelled and check_cancelled():
                                raise CancellationException("Generation task aborted by user.")
                            if progress_callback:
                                progress_callback(step_index + 1, steps)
                            return callback_kwargs
                        kwargs["callback_on_step_end"] = step_end_callback
                    elif "callback" in sig.parameters:
                        def legacy_callback(step, timestep, latents):
                            if check_cancelled and check_cancelled():
                                raise CancellationException("Generation task aborted by user.")
                            if progress_callback:
                                progress_callback(step + 1, steps)
                        kwargs["callback"] = legacy_callback
                        kwargs["callback_steps"] = 1
                        
                result = self.image_pipeline(**kwargs)
                frames_output = result.frames
                
            pil_frames = []
            if isinstance(frames_output, list):
                if len(frames_output) > 0 and isinstance(frames_output[0], list):
                    frames_output = frames_output[0]
                for f in frames_output:
                    if isinstance(f, Image.Image):
                        pil_frames.append(f)
                    elif hasattr(f, "ndim"):
                        if hasattr(f, "cpu"):
                            f = f.cpu().numpy()
                        if f.max() <= 1.0:
                            f = (f * 255).astype("uint8")
                        pil_frames.append(Image.fromarray(f))
            elif hasattr(frames_output, "ndim"):
                if hasattr(frames_output, "cpu"):
                    frames_output = frames_output.cpu().numpy()
                if frames_output.ndim == 5:
                    frames_output = frames_output[0]
                if frames_output.ndim == 4:
                    for i in range(frames_output.shape[0]):
                        f = frames_output[i]
                        if f.max() <= 1.0:
                            f = (f * 255).astype("uint8")
                        pil_frames.append(Image.fromarray(f))
                        
            if not pil_frames:
                raise ValueError("No frames generated by the video model.")
                
            from diffusers.utils import export_to_video
            import tempfile
            
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmpfile:
                tmp_path = tmpfile.name
                
            try:
                export_to_video(pil_frames, tmp_path, fps=fps)
                with open(tmp_path, "rb") as f:
                    video_bytes = f.read()
                return base64.b64encode(video_bytes).decode("utf-8")
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

    def merge_videos(self, base64_videos: List[str], fps: int = 8) -> str:
        import tempfile
        import os
        import base64
        import imageio
        from diffusers.utils import export_to_video
        
        all_frames = []
        for b64_vid in base64_videos:
            if "," in b64_vid:
                b64_vid = b64_vid.split(",")[1]
            vid_bytes = base64.b64decode(b64_vid)
            
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_in:
                tmp_in.write(vid_bytes)
                tmp_in_path = tmp_in.name
                
            try:
                reader = imageio.get_reader(tmp_in_path, format="mp4")
                for frame in reader:
                    all_frames.append(frame)
                reader.close()
            finally:
                if os.path.exists(tmp_in_path):
                    try:
                        os.remove(tmp_in_path)
                    except Exception:
                        pass
                        
        if not all_frames:
            raise ValueError("No video frames could be read for merging.")
            
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_out:
            tmp_out_path = tmp_out.name
            
        try:
            export_to_video(all_frames, tmp_out_path, fps=fps)
            with open(tmp_out_path, "rb") as f:
                merged_bytes = f.read()
            return base64.b64encode(merged_bytes).decode("utf-8")
        finally:
            if os.path.exists(tmp_out_path):
                try:
                    os.remove(tmp_out_path)
                except Exception:
                    pass

    def generate_stream(self, messages: List[Dict[str, Any]], temperature: float = 0.7, top_p: float = 0.9, max_tokens: int = 512, agent_mode: bool = False):
        if not self.model:
            yield "Error: No model is currently loaded. Please load a model first."
            return

        # Keep a local copy of message history to append tool outputs dynamically
        active_messages = list(messages)
        
        # Check if the last user message is a simple greeting to bypass Agent Mode
        if agent_mode:
            last_user_content = ""
            for msg in reversed(active_messages):
                if msg["role"] == "user":
                    if isinstance(msg["content"], str):
                        last_user_content = msg["content"].strip().lower()
                    break
            
            # Normalize and identify greetings
            greetings = {"selam", "merhaba", "hi", "hello", "hey", "nasılsın", "salam", "yo", "hi there", "günaydın", "iyi günler", "iyi akşamlar"}
            clean_content = re.sub(r'[^\w\s]', '', last_user_content).strip()
            
            is_greeting = False
            if clean_content in greetings or len(clean_content) <= 3:
                is_greeting = True
            elif clean_content.startswith(("selam", "merhaba", "nasılsın", "hello", "good morning", "hey")):
                if len(clean_content) < 20:
                    is_greeting = True
                    
            if is_greeting:
                print(f"[ModelManager Agent] Detected simple greeting '{clean_content}'. Temporarily disabling Agent Mode for this turn.")
                agent_mode = False
        
        # Inject tool schemas if agent mode is enabled
        if agent_mode:
            try:
                from mcp_client import get_all_external_tools
                ext_tools = get_all_external_tools() or []
                tools = BUILTIN_TOOLS + ext_tools
                
                # Locate system prompt message
                system_msg = None
                for msg in active_messages:
                    if msg["role"] == "system":
                        system_msg = msg
                        break
                
                if not system_msg:
                    system_msg = {"role": "system", "content": ""}
                    active_messages.insert(0, system_msg)
                    
                # Prepend a strong capability reminder to force model awareness of real-time tools
                capability_reminder = (
                    "You are the LLM Workstation assistant. You have access to tools, "
                    "including a real-time 'web_search' tool. When the user asks for current/real-time "
                    "information (like weather, news, or web docs), you MUST use the tools instead of refusing.\n\n"
                )
                
                if isinstance(system_msg["content"], str):
                    system_msg["content"] = capability_reminder + system_msg["content"]
                else:
                    system_msg["content"] = capability_reminder + str(system_msg["content"])
                    
                # Strict, token-efficient system instruction to prevent conversational pre-phrases and context bloat
                tool_instructions = (
                    "\n\n[SYSTEM: Agent Mode Active]\n"
                    "If the user's query is a simple greeting (like 'hi', 'hello', 'selam', 'nasılsın') or conversational chat that does not require external data, you MUST NOT call any tools. Just reply directly in conversation.\n"
                    "Only call a tool when it is absolutely necessary to answer the user's query.\n"
                    "If you need to call a tool or search the web, you MUST output ONLY the JSON code block. "
                    "Do NOT write any conversation, thoughts, or introduction before the JSON block (do NOT say 'I will search' or 'Lütfen bekleyin'). "
                    "Output the JSON block immediately in this exact format:\n"
                    "```json\n"
                    "{\n"
                    "  \"tool\": \"tool_name\",\n"
                    "  \"arguments\": { ... }\n"
                    "}\n"
                    "```\n"
                    f"Available tools:\n{json.dumps(tools, indent=1)}\n\n"
                )
                
                system_msg["content"] = str(system_msg["content"]) + tool_instructions
            except Exception as e:
                print(f"[ModelManager Agent Warning] Tool injection failed: {e}")
                
        # Run inference ReAct loop
        for step in range(5):
            generated_text = ""
            
            # Execute standard generation step
            step_generator = self._execute_inference_step(
                active_messages, 
                temperature, 
                top_p, 
                max_tokens
            )
            
            for chunk in step_generator:
                if chunk.startswith("Error:") or chunk.startswith("\n[Generation Error"):
                    yield chunk
                    return
                generated_text += chunk
                yield chunk
 
            # Parse for tool calls
            tool_call = parse_tool_call_json(generated_text)
            if agent_mode and tool_call:
                tool_name = tool_call.get("tool")
                tool_args = tool_call.get("arguments", {})
                
                yield f"\n\n⚙️ **[Calling Tool: `{tool_name}` with arguments {json.dumps(tool_args)}]**\n"
                
                try:
                    if tool_name == "web_search":
                        query = tool_args.get("query", "")
                        tool_result = builtin_web_search(query)
                    else:
                        from mcp_client import execute_external_tool
                        tool_result = execute_external_tool(tool_name, tool_args)
                    
                    result_str = json.dumps(tool_result, indent=2)
                    
                    yield f"📊 **[Tool Result: `{result_str}`]**\n\n"
                    
                    # Strip the raw tool call JSON block from the assistant message before adding to history.
                    # If we leave the JSON block in, the LLM will see it next iteration and re-call the tool.
                    clean_assistant_text = re.sub(r"```(?:json)?\s*\{[\s\S]*?\}\s*```", "", generated_text).strip()
                    # Also strip any bare JSON brace block that might remain
                    clean_assistant_text = re.sub(r"\{[\s\S]*?\"tool\"[\s\S]*?\}", "", clean_assistant_text).strip()
                    if not clean_assistant_text:
                        clean_assistant_text = f"[Called tool: {tool_name}]"
                    
                    # Append cleaned assistant turn + tool result with explicit stop instruction
                    active_messages.append({"role": "assistant", "content": clean_assistant_text})
                    active_messages.append({
                        "role": "user",
                        "content": (
                            f"[TOOL RESULT for '{tool_name}']\n{result_str}\n\n"
                            "[SYSTEM: The tool has finished. Do NOT call any more tools. "
                            "Use the result above to compose your final answer to the user directly and clearly.]"
                        )
                    })
                    continue
                except Exception as tool_err:
                    yield f"\n❌ **[Tool Execution Failed: {str(tool_err)}]**\n"
                    break
            else:
                break


    def _execute_inference_step(self, messages: List[Dict[str, Any]], temperature: float, top_p: float, max_tokens: int):
        with self.lock:
            try:
                processed_messages = []
                image_inputs = []
                
                for msg in messages:
                    role = msg["role"]
                    content = msg["content"]
                    
                    if isinstance(content, list):
                        new_content = []
                        for part in content:
                            if part.get("type") == "text":
                                new_content.append({"type": "text", "text": part["text"]})
                            elif part.get("type") == "image_url":
                                base64_data = part["image_url"]["url"]
                                try:
                                    pil_img = decode_base64_image(base64_data)
                                    image_inputs.append(pil_img)
                                    new_content.append({"type": "image", "image": pil_img})
                                except Exception as e:
                                    print(f"[ModelManager] Image decoding failed: {e}")
                        processed_messages.append({"role": role, "content": new_content})
                    else:
                        processed_messages.append({"role": role, "content": content})
                
                if len(image_inputs) > 0 and not self.is_vision:
                    text_only_messages = []
                    for msg in messages:
                        if isinstance(msg["content"], list):
                            txt = "".join(part["text"] for part in msg["content"] if part.get("type") == "text")
                            text_only_messages.append({"role": msg["role"], "content": txt})
                        else:
                            text_only_messages.append(msg)
                    processed_messages = text_only_messages
                    image_inputs = []

                # Fallback template to use if the model does not define one
                fallback_template = (
                    "{% for message in messages %}"
                    "{{ '<|im_start|>' + message['role'] + '\\n' + message['content'] + '<|im_end|>\\n' }}"
                    "{% endfor %}"
                    "{% if add_generation_prompt %}"
                    "{{ '<|im_start|>assistant\\n' }}"
                    "{% endif %}"
                )

                if self.is_vision and len(image_inputs) > 0 and self.processor:
                    if not getattr(self.processor, "chat_template", None) and not getattr(self.processor.tokenizer, "chat_template", None):
                        print("[ModelManager] Processor has no chat_template, setting ChatML fallback.")
                        self.processor.chat_template = fallback_template
                    
                    prompt = self.processor.apply_chat_template(
                        processed_messages, 
                        tokenize=False, 
                        add_generation_prompt=True
                    )
                    inputs = self.processor(
                        text=[prompt], 
                        images=image_inputs, 
                        padding=True, 
                        return_tensors="pt"
                    ).to(self.model.device)
                else:
                    text_only_messages = []
                    for msg in processed_messages:
                        if isinstance(msg["content"], list):
                            txt = "".join(part["text"] for part in msg["content"] if part.get("type") == "text")
                            text_only_messages.append({"role": msg["role"], "content": txt})
                        else:
                            text_only_messages.append(msg)
                    
                    tokenizer_obj = self.tokenizer if self.tokenizer else self.processor.tokenizer
                    if not getattr(tokenizer_obj, "chat_template", None):
                        print("[ModelManager] Tokenizer has no chat_template, setting ChatML fallback.")
                        tokenizer_obj.chat_template = fallback_template
                        
                    prompt = tokenizer_obj.apply_chat_template(
                        text_only_messages, 
                        tokenize=False, 
                        add_generation_prompt=True
                    )
                    inputs = tokenizer_obj(prompt, return_tensors="pt").to(self.model.device)
                
                from transformers import TextIteratorStreamer
                from threading import Thread
                
                tokenizer_for_streamer = self.tokenizer if self.tokenizer else self.processor.tokenizer
                streamer = TextIteratorStreamer(tokenizer_for_streamer, skip_prompt=True, skip_special_tokens=True)
                
                generation_kwargs = dict(
                    **inputs,
                    streamer=streamer,
                    max_new_tokens=max_tokens,
                    do_sample=temperature > 0.0,
                    temperature=temperature if temperature > 0.0 else 1.0,
                    top_p=top_p
                )
                
                thread_exceptions = []
                
                def safe_generate():
                    try:
                        self.model.generate(**generation_kwargs)
                    except Exception as t_err:
                        print(f"[ModelManager Error] Generation thread exception: {t_err}")
                        thread_exceptions.append(t_err)
                        streamer.end()
                
                thread = Thread(target=safe_generate, daemon=True)
                thread.start()
                
                token_count = 0
                start_time = time.time()
                
                for token_text in streamer:
                    if not token_text:
                        continue
                    token_count += 1
                    elapsed = time.time() - start_time
                    if elapsed > 0:
                        self.last_tokens_per_sec = round(token_count / elapsed, 2)
                    yield token_text
                    
                if thread_exceptions:
                    raise thread_exceptions[0]
                    
                elapsed = time.time() - start_time
                if elapsed > 0:
                    self.last_tokens_per_sec = round(token_count / elapsed, 2)
                    
            except Exception as e:
                yield f"\n[Generation Error: {str(e)}]"

def get_cached_models() -> list:
    try:
        from huggingface_hub import scan_cache_dir
        cache_info = scan_cache_dir()
        models = []
        for repo in cache_info.repos:
            # We want to format size in GB
            size_gb = repo.size_on_disk / (1024 ** 3)
            models.append({
                "repo_id": repo.repo_id,
                "size_gb": round(size_gb, 2),
                "repo_path": repo.repo_path,
                "nb_files": repo.nb_files
            })
        return models
    except Exception as e:
        print(f"[Cache Scanner] Failed to scan HF cache: {e}")
        return []

def delete_cached_model(repo_id: str) -> bool:
    try:
        import shutil
        from huggingface_hub import scan_cache_dir
        cache_info = scan_cache_dir()
        for repo in cache_info.repos:
            if repo.repo_id == repo_id:
                print(f"[Cache Scanner] Deleting directory recursively: {repo.repo_path}")
                shutil.rmtree(repo.repo_path)
                return True
        return False
    except Exception as e:
        print(f"[Cache Scanner] Failed to delete HF cached model {repo_id}: {e}")
        return False
