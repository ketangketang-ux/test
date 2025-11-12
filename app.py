# ======================
# comfyui_modal.py
# ComfyUI + GUI di Modal (FIXED: NO multi-line RUN!)
# Cara deploy: modal deploy comfyui_modal.py
# ======================

import os
import modal
import subprocess
import threading
import time
import requests
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse

# Konfigurasi
DATA_ROOT = "/data/comfy"
COMFY_DIR = os.path.join(DATA_ROOT, "ComfyUI")
MODELS_DIR = os.path.join(COMFY_DIR, "models")
OUTPUT_DIR = os.path.join(COMFY_DIR, "output")
TEMP_DIR = os.path.join(COMFY_DIR, "temp")
COMFY_PORT = 8188
GUI_PORT = 8000

# Modal setup
vol = modal.Volume.from_name("comfyui-app", create_if_missing=True)
app = modal.App(name="comfyui")

# Image dengan SATU PER SATU command (NGGAK ADA MULTI-LINE!)
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install(
        "git", "wget", "libgl1-mesa-glx", "libglib2.0-0", "ffmpeg",
        "build-essential", "curl", "pkg-config", "python3-dev"
    )
    .run_commands("pip install --upgrade pip")
    .run_commands("pip install torch==2.3.1 torchvision==0.18.1 torchaudio==2.3.1")
    .run_commands("pip install tokenizers einops transformers diffusers safetensors pillow scipy numpy requests tqdm")
    .run_commands("pip install torchsde")  # FIX untuk k_diffusion
    .run_commands("pip install av")        # FIX untuk video/image processing
    .run_commands("pip install comfy-cli huggingface_hub[hf_transfer]")
    .run_commands("pip install fastapi uvicorn python-multipart")
    .env({
        "HF_HUB_ENABLE_HF_TRANSFER": "1",
        "PYTORCH_ALLOC_CONF": "max_split_size_mb:512",
    })
)

# Models (FLUX)
MODELS_LIST = [
    ("unet/FLUX", "flux1-dev-Q8_0.gguf", "city96/FLUX.1-dev-gguf", None),
    ("clip/FLUX", "t5-v1_1-xxl-encoder-Q8_0.gguf", "city96/t5-v1_1-xxl-encoder-gguf", None),
    ("clip/FLUX", "clip_l.safetensors", "comfyanonymous/flux_text_encoders", None),
    ("checkpoints", "flux1-dev-fp8-all-in-one.safetensors", "camenduru/FLUX.1-dev", None),
    ("vae/FLUX", "ae.safetensors", "ffxvs/vae-flux", None),
]

@app.function(
    gpu="L4",
    timeout=1800,
    volumes={DATA_ROOT: vol},
    image=image,
)
@modal.web_server(GUI_PORT, startup_timeout=300)
def ui():
    # Setup dirs
    os.makedirs(DATA_ROOT, exist_ok=True)
    if not os.path.exists(COMFY_DIR):
        subprocess.run(f"cp -r /root/comfy/ComfyUI {DATA_ROOT}/", shell=True, check=True)
    
    os.chdir(COMFY_DIR)
    
    # Download models
    for sub, fn, repo, subf in MODELS_LIST:
        target = os.path.join(MODELS_DIR, sub, fn)
        if not os.path.exists(target):
            from huggingface_hub import hf_hub_download
            tmp = "/tmp/download"
            os.makedirs(tmp, exist_ok=True)
            out = hf_hub_download(repo_id=repo, filename=fn, subfolder=subf, local_dir=tmp)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.rename(out, target)
            print(f"✅ Downloaded {fn} ({os.path.getsize(target)//1024//1024} MB)")
    
    vol.commit()
    
    # Environment
    os.environ.update({
        "COMFY_OUTPUT_PATH": OUTPUT_DIR,
        "COMFY_TEMP_PATH": TEMP_DIR,
    })
    
    # Start ComfyUI
    def run_comfy():
        cmd = [
            "python", "main.py",
            "--listen", "0.0.0.0",
            "--port", str(COMFY_PORT),
            "--force-fp16",
            "--disable-xformers",
            "--enable-cors-header", "*",
            "--output-directory", OUTPUT_DIR,
            "--temp-directory", TEMP_DIR,
        ]
        subprocess.Popen(cmd, cwd=COMFY_DIR)
    
    threading.Thread(target=run_comfy, daemon=True).start()
    
    # Wait for ComfyUI ready
    print("⏳ Waiting for ComfyUI to start...")
    for i in range(60):
        try:
            r = requests.get(f"http://127.0.0.1:{COMFY_PORT}/system_stats", timeout=1)
            if r.ok:
                print(f"✅ ComfyUI ready after {i+1}s!")
                break
        except:
            pass
        time.sleep(1)
    else:
        raise RuntimeError("❌ ComfyUI failed to start")

    # Build GUI
    gui_app = FastAPI(title="ComfyUI Remote GUI")
    COMFY_API = f"http://127.0.0.1:{COMFY_PORT}"

    @gui_app.get("/", response_class=HTMLResponse)
    def home():
        return """
        <html>
        <head>
            <style>
                body { font-family: sans-serif; background: #111; color: #eee; text-align: center; padding: 40px; }
                .container { max-width: 800px; margin: 0 auto; }
                input, button { width: 100%; padding: 12px; margin: 10px 0; font-size: 16px; }
                button { background: #4CAF50; color: white; border: none; cursor: pointer; }
                button:hover { background: #45a049; }
                #loading { display: none; margin-top: 20px; }
            </style>
        </head>
        <body>
            <div class="container">
                <h2>🧠 ComfyUI Remote GUI</h2>
                <form action="/generate" method="post" onsubmit="document.getElementById('loading').style.display='block'">
                    <input name="prompt" placeholder="Enter your prompt" required><br>
                    <label>Steps: <input type="number" name="steps" value="25" min="1" max="50"></label><br>
                    <label>CFG: <input type="number" name="cfg" value="6.5" step="0.1" min="1" max="20"></label><br>
                    <button type="submit">Generate ✨</button>
                </form>
                <div id="loading">
                    <p>Generating... This may take 1-3 minutes</p>
                </div>
            </div>
        </body>
        </html>
        """

    @gui_app.post("/generate")
    async def generate(prompt: str = Form(...), steps: int = Form(25), cfg: float = Form(6.5)):
        try:
            workflow = {
                "1": {"inputs": {"text": prompt}, "class_type": "CLIPTextEncode"},
                "2": {"inputs": {"text": "bad quality, blurry"}, "class_type": "CLIPTextEncode"},
                "3": {"inputs": {"ckpt_name": "flux1-dev-fp8-all-in-one.safetensors"}, "class_type": "CheckpointLoaderSimple"},
                "4": {"inputs": {"width": 1024, "height": 1024, "batch_size": 1}, "class_type": "EmptyLatentImage"},
                "5": {"inputs": {"seed": -1, "steps": steps, "cfg": cfg, "sampler_name": "dpmpp_2m", "scheduler": "normal", 
                               "denoise": 1, "model": ["3", 0], "positive": ["1", 0], "negative": ["2", 0], "latent_image": ["4", 0]}, 
                      "class_type": "KSampler"},
                "6": {"inputs": {"samples": ["5", 0], "vae": ["3", 2]}, "class_type": "VAEDecode"},
                "7": {"inputs": {"filename_prefix": "ComfyUI", "images": ["6", 0]}, "class_type": "SaveImage"}
            }
            
            r = requests.post(f"{COMFY_API}/prompt", json={"prompt": workflow}, timeout=10)
            job_id = r.json()["prompt_id"]
            
            # Poll for result
            for _ in range(180):
                time.sleep(1)
                status = requests.get(f"{COMFY_API}/history/{job_id}").json()
                if job_id in status and status[job_id]["status"]["completed"]:
                    if "7" in status[job_id]["outputs"]:
                        img_info = status[job_id]["outputs"]["7"]["images"][0]
                        filename = img_info["filename"]
                        return HTMLResponse(f"""
                        <html><body style='background:#000;color:#fff;text-align:center;padding:40px;'>
                        <h2>✅ Generated!</h2>
                        <img src="/view?filename={filename}" style='max-width:90%;border:2px solid #666;margin:20px;'><br>
                        <a href="/">← Generate Again</a>
                        </body></html>
                        """)
            
            return HTMLResponse(f"<h2>❌ Timeout after 180s</h2><a href='/'>Back</a>")
        
        except Exception as e:
            return HTMLResponse(f"<h2>❌ Error: {str(e)}</h2><a href='/'>Back</a>")

    @gui_app.get("/view")
    def view_image(filename: str):
        r = requests.get(f"{COMFY_API}/view?filename={filename}&type=output")
        return HTMLResponse(content=r.content, media_type="image/png")

    @gui_app.get("/health")
    def health():
        return {"status": "healthy", "comfy_port": COMFY_PORT, "gui_port": GUI_PORT}

    # Give Modal time to register
    time.sleep(2)
    
    print(f"🚀 GUI ready! Serving on port {GUI_PORT}")
    return gui_app
