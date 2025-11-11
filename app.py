import os
import shutil
import subprocess
import threading
import time
import glob
from datetime import datetime
from huggingface_hub import hf_hub_download
import modal
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import requests
import json

# === KONFIGURASI (Gampang ganti model) ===
DATA_ROOT = "/data/comfy"
DATA_BASE = os.path.join(DATA_ROOT, "ComfyUI")
MODELS_DIR = os.path.join(DATA_BASE, "models")
OUTPUT_DIR = os.path.join(DATA_BASE, "output")
TEMP_DIR = os.path.join(DATA_BASE, "temp")
TMP_DL = "/tmp/download"
DEFAULT_COMFY_DIR = "/root/comfy/ComfyUI"

# List model yang mau didownload (ganti di sini!)
MODELS_LIST = [
    # FLUX (sekarang) - nanti tinggal comment/ganti
    ("unet/FLUX", "flux1-dev-Q8_0.gguf", "city96/FLUX.1-dev-gguf", None),
    ("clip/FLUX", "t5-v1_1-xxl-encoder-Q8_0.gguf", "city96/t5-v1_1-xxl-encoder-gguf", None),
    ("clip/FLUX", "clip_l.safetensors", "comfyanonymous/flux_text_encoders", None),
    ("checkpoints", "flux1-dev-fp8-all-in-one.safetensors", "camenduru/FLUX.1-dev", None),
    ("vae/FLUX", "ae.safetensors", "ffxvs/vae-flux", None),
    
    # Model lain (uncomment kalau mau ganti)
    # ("checkpoints", "dreamshaper.safetensors", "Lykon/dreamshaper", None),
    # ("checkpoints", "realisticVision.safetensors", "SG161222/realisticVision", None),
]

# Workflow FLUX (bisa diganti sesuai model lain)
WORKFLOW_TEMPLATE = {
    "1": {"inputs": {"text": "beautiful sunset over mountains, high quality"}, "class_type": "CLIPTextEncode"},
    "2": {"inputs": {"text": "blurry, low quality, bad anatomy"}, "class_type": "CLIPTextEncode"},
    "3": {"inputs": {"ckpt_name": "flux1-dev-fp8-all-in-one.safetensors"}, "class_type": "CheckpointLoaderSimple"},
    "4": {"inputs": {"width": 1024, "height": 1024, "batch_size": 1}, "class_type": "EmptyLatentImage"},
    "5": {"inputs": {"seed": -1, "steps": 25, "cfg": 6.5, "sampler_name": "dpmpp_2m", "scheduler": "karras", 
                    "denoise": 1.0, "model": ["3", 0], "positive": ["1", 0], "negative": ["2", 0], "latent_image": ["4", 0]}, 
           "class_type": "KSampler"},
    "6": {"inputs": {"samples": ["5", 0], "vae": ["3", 2]}, "class_type": "VAEDecode"},
    "7": {"inputs": {"filename_prefix": "ComfyUI", "images": ["6", 0]}, "class_type": "SaveImage"}
}

# === HELPER FUNCTIONS ===
def git_clone_cmd(repo: str, recursive=False, install_reqs=False):
    """Clone repo ke custom_nodes"""
    name = repo.split("/")[-1]
    dest = os.path.join(DEFAULT_COMFY_DIR, "custom_nodes", name)
    cmd = f"git clone https://github.com/{repo} {dest}"
    if recursive:
        cmd += " --recursive"
    if install_reqs:
        cmd += f" && pip install -r {dest}/requirements.txt"
    return cmd

def download_model(subdir, filename, repo_id, subfolder=None):
    """Download model dari HF Hub"""
    try:
        target = os.path.join(MODELS_DIR, subdir)
        os.makedirs(target, exist_ok=True)
        
        # Skip kalau sudah ada
        if os.path.exists(os.path.join(target, filename)):
            print(f"⏩ Skipping {filename} (exists)")
            return
            
        print(f"📥 Downloading {filename}...")
        out = hf_hub_download(
            repo_id=repo_id, 
            filename=filename, 
            subfolder=subfolder, 
            local_dir=TMP_DL,
            local_dir_use_symlinks=False
        )
        shutil.move(out, os.path.join(target, filename))
        print(f"✅ Downloaded {filename}")
    except Exception as e:
        print(f"❌ Error downloading {filename}: {e}")

def get_latest_image():
    """Ambil gambar terbaru dari output folder (fallback)"""
    try:
        files = glob.glob(os.path.join(OUTPUT_DIR, "*.png"))
        if files:
            latest = max(files, key=os.path.getmtime)
            return {
                "filename": os.path.basename(latest),
                "subfolder": "",
                "type": "output"
            }
    except Exception as e:
        print(f"❌ Error getting latest image: {e}")
    return None

def check_comfy_status(job_id, timeout=180):
    """Polling status job ComfyUI dengan timeout panjang"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            res = requests.get(f"http://127.0.0.1:8188/history/{job_id}", timeout=5)
            if res.ok:
                data = res.json()
                if job_id in data and data[job_id]["status"]["completed"]:
                    return data[job_id]["outputs"]
        except Exception as e:
            print(f"⚠️ Polling error: {e}")
        time.sleep(1)
    return None

# === MODAL IMAGE SETUP ===
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "wget", "libgl1-mesa-glx", "libglib2.0-0", "ffmpeg")
    .run_commands([
        "pip install --upgrade pip",
        "pip install --no-cache-dir comfy-cli fastapi uvicorn requests python-multipart huggingface_hub[hf_transfer]==0.28.1",
        "comfy --skip-prompt install --nvidia"
    ])
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

# Install nodes populer
image = image.run_commands([
    "comfy node install rgthree-comfy comfyui-impact-pack ComfyUI-Manager"
])

# Clone nodes dari Git
for repo, flags in [
    ("ssitu/ComfyUI_UltimateSDUpscale", {'recursive': True}),
    ("welltop-cn/ComfyUI-TeaCache", {'install_reqs': True}),
]:
    image = image.run_commands([git_clone_cmd(repo, **flags)])

# === MODAL APP SETUP ===
vol = modal.Volume.from_name("comfyui-app", create_if_missing=True)
app = modal.App(name="comfyui", image=image)

@app.function(
    gpu="L4",
    timeout=1800,
    scaledown_window=300,
    volumes={DATA_ROOT: vol},
)
@modal.web_server(8000, startup_timeout=300)
def ui():
    # Setup directories
    os.makedirs(DATA_ROOT, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)
    
    # Copy ComfyUI kalau belum ada
    if not os.path.exists(DATA_BASE):
        print("📂 Copying ComfyUI to persistent volume...")
        subprocess.run(f"cp -r {DEFAULT_COMFY_DIR} {DATA_ROOT}/", shell=True, check=True)
    
    # Download models
    for sub, fn, repo, subf in MODELS_LIST:
        download_model(sub, fn, repo, subf)
    
    # Update ComfyUI & Manager
    os.chdir(DATA_BASE)
    subprocess.run("git config pull.ff only", shell=True, check=False)
    subprocess.run("git pull --ff-only", shell=True, check=False)
    
    manager_dir = os.path.join(DATA_BASE, "custom_nodes", "ComfyUI-Manager")
    if os.path.exists(manager_dir):
        os.chdir(manager_dir)
        subprocess.run("git pull --ff-only", shell=True, check=False)
    
    # Environment
    os.environ["COMFY_OUTPUT_PATH"] = OUTPUT_DIR
    os.environ["COMFY_TEMP_PATH"] = TEMP_DIR

    # Start ComfyUI di background
    def run_comfy():
        cmd = [
            "python", "main.py",
            "--listen", "0.0.0.0",
            "--port", "8188",
            "--force-fp16",
            "--disable-xformers",
            "--enable-cors-header", "*",
            "--output-directory", OUTPUT_DIR,
            "--temp-directory", TEMP_DIR,
            "--verbose",
        ]
        subprocess.Popen(cmd, cwd=DATA_BASE, env=os.environ.copy())
    
    threading.Thread(target=run_comfy, daemon=True).start()
    time.sleep(3)  # Tunggu ComfyUI startup

    # === FASTAPI GUI ===
    app_gui = FastAPI()
    COMFY_API = "http://127.0.0.1:8188"

    @app_gui.get("/", response_class=HTMLResponse)
    def home():
        return """
        <html>
        <head>
            <style>
                body { font-family: sans-serif; background: #111; color: #eee; text-align: center; padding: 50px; }
                input, select { width: 70%; padding: 10px; margin: 10px; background: #222; color: #eee; border: 1px solid #444; }
                button { padding: 12px 30px; background: #4CAF50; color: white; border: none; cursor: pointer; font-size: 16px; }
                button:hover { background: #45a049; }
                #loading { display: none; margin-top: 20px; }
            </style>
        </head>
        <body>
            <h2>🧠 ComfyUI Remote GUI</h2>
            <form action="/generate" method="post" onsubmit="showLoading()">
                <input name="prompt" placeholder="Enter your prompt (e.g., 'a cat in space')" required><br>
                <label>Steps:</label> <input type="number" name="steps" value="25" min="1" max="50"><br>
                <label>CFG:</label> <input type="number" name="cfg" value="6.5" step="0.1" min="1" max="20"><br>
                <label>Width:</label> <input type="number" name="width" value="1024" min="64" max="2048" step="64"><br>
                <label>Height:</label> <input type="number" name="height" value="1024" min="64" max="2048" step="64"><br>
                <button type="submit">Generate ✨</button>
            </form>
            <div id="loading">
                <p>Generating... This may take 1-3 minutes on L4</p>
                <p>Don't close this page!</p>
            </div>
            <script>
                function showLoading() { document.getElementById('loading').style.display = 'block'; }
            </script>
        </body>
        </html>
        """

    @app_gui.post("/generate")
    async def generate(
        prompt: str = Form(...),
        steps: int = Form(25),
        cfg: float = Form(6.5),
        width: int = Form(1024),
        height: int = Form(1024)
    ):
        # Build workflow dinamis
        workflow = WORKFLOW_TEMPLATE.copy()
        workflow["1"]["inputs"]["text"] = prompt
        workflow["4"]["inputs"]["width"] = width
        workflow["4"]["inputs"]["height"] = height
        workflow["5"]["inputs"]["steps"] = steps
        workflow["5"]["inputs"]["cfg"] = cfg
        
        try:
            print(f"🚀 Submitting job: {prompt[:50]}...")
            
            # Submit job
            r = requests.post(f"{COMFY_API}/prompt", json={"prompt": workflow}, timeout=10)
            if not r.ok:
                raise Exception(f"Submit failed: {r.text}")
                
            job_id = r.json()["prompt_id"]
            print(f"✅ Job ID: {job_id}")
            
            # Polling status
            outputs = check_comfy_status(job_id, timeout=180)
            
            if outputs:
                print(f"✅ Job completed! Outputs: {list(outputs.keys())}")
                
                # Cari gambar
                for node_id, output_data in outputs.items():
                    if "images" in output_data and output_data["images"]:
                        img_info = output_data["images"][0]
                        filename = img_info["filename"]
                        subfolder = img_info.get("subfolder", "")
                        img_type = img_info.get("type", "output")
                        
                        # URL yang bener
                        img_url = f"/view-image?filename={filename}&subfolder={subfolder}&type={img_type}"
                        
                        return HTMLResponse(f"""
                        <html><body style='background:#000;color:#fff;text-align:center;padding:40px;'>
                        <h2>✅ Image Generated!</h2>
                        <img src='{img_url}' style='max-width:90%;border:2px solid #666;margin:20px auto;'><br>
                        <p><strong>Filename:</strong> {filename}</p>
                        <a href='/' style='color:#4CAF50;font-size:18px;text-decoration:none;'>← Generate Again</a>
                        </body></html>
                        """)
                
                # Kalau nggak ketemu di outputs, coba fallback
                print("⚠️ No images in outputs, trying fallback...")
            
            # Fallback: cek file terbaru di folder
            latest = get_latest_image()
            if latest:
                img_url = f"/view-image?filename={latest['filename']}&subfolder={latest['subfolder']}&type={latest['type']}"
                return HTMLResponse(f"""
                <html><body style='background:#000;color:#fff;text-align:center;padding:40px;'>
                <h2>✅ Image Generated (Fallback)!</h2>
                <img src='{img_url}' style='max-width:90%;border:2px solid #666;margin:20px auto;'><br>
                <p><strong>Filename:</strong> {latest['filename']}</p>
                <a href='/' style='color:#4CAF50;font-size:18px;text-decoration:none;'>← Generate Again</a>
                </body></html>
                """)
            
            raise Exception("No image generated after 180s")
            
        except Exception as e:
            print(f"🔥 ERROR: {e}")
            return HTMLResponse(f"""
            <html><body style='background:#000;color:#fff;text-align:center;padding:40px;'>
            <h2>❌ Generation Failed</h2>
            <p>Error: {str(e)}</p>
            <p>Check Modal logs for details</p>
            <a href='/' style='color:#4CAF50;font-size:18px;text-decoration:none;'>← Try Again</a>
            </body></html>
            """)

    @app_gui.get("/view-image")
    def view_image(filename: str, subfolder: str = "", type: str = "output"):
        """Proxy untuk menampilkan gambar dari ComfyUI"""
        params = f"filename={filename}&subfolder={subfolder}&type={type}"
        img_response = requests.get(f"{COMFY_API}/view?{params}")
        return HTMLResponse(content=img_response.content, media_type="image/png")

    @app_gui.get("/manual-check")
    def manual_check():
        """Cek manual file terbaru (backup plan)"""
        latest = get_latest_image()
        if latest:
            return RedirectResponse(f"/view-image?filename={latest['filename']}&subfolder={latest['subfolder']}&type={latest['type']}")
        return "No images yet. Check logs."

    print("🚀 ComfyUI backend: http://0.0.0.0:8188")
    print("✅ GUI ready: http://0.0.0.0:8000")
    return app_gui
