import os
import shutil
import subprocess
import threading
import time
from typing import Optional
from huggingface_hub import hf_hub_download
import modal
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
import requests

# === Path Settings ===
DATA_ROOT = "/data/comfy"
DATA_BASE = os.path.join(DATA_ROOT, "ComfyUI")
CUSTOM_NODES_DIR = os.path.join(DATA_BASE, "custom_nodes")
MODELS_DIR = os.path.join(DATA_BASE, "models")
TMP_DL = "/tmp/download"
DEFAULT_COMFY_DIR = "/root/comfy/ComfyUI"

# === Helper Functions ===
def git_clone_cmd(repo: str, recursive=False, install_reqs=False):
    name = repo.split("/")[-1]
    dest = os.path.join(DEFAULT_COMFY_DIR, "custom_nodes", name)
    cmd = f"git clone https://github.com/{repo} {dest}"
    if recursive:
        cmd += " --recursive"
    if install_reqs:
        cmd += f" && pip install -r {dest}/requirements.txt"
    return cmd

def hf_download(subdir, filename, repo_id, subfolder=None):
    out = hf_hub_download(repo_id=repo_id, filename=filename, subfolder=subfolder, local_dir=TMP_DL)
    target = os.path.join(MODELS_DIR, subdir)
    os.makedirs(target, exist_ok=True)
    shutil.move(out, os.path.join(target, filename))

# === Modal Image Build ===
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "wget", "libgl1-mesa-glx", "libglib2.0-0", "ffmpeg")
    .run_commands([
        "pip install --upgrade pip",
        "pip install --no-cache-dir comfy-cli fastapi uvicorn",
        "pip install huggingface_hub[hf_transfer]==0.28.1 requests",
        "comfy --skip-prompt install --nvidia"
    ])
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

# === Auto Install Popular Nodes ===
image = image.run_commands([
    "comfy node install rgthree-comfy comfyui-impact-pack comfyui-impact-subpack comfyui_ipadapter_plus comfyui-inspire-pack wlsh_nodes comfyui_essentials ComfyUI-GGUF"
])

# === Extra Git Nodes ===
for repo, flags in [
    ("ssitu/ComfyUI_UltimateSDUpscale", {'recursive': True}),
    ("welltop-cn/ComfyUI-TeaCache", {'install_reqs': True}),
    ("nkchocoai/ComfyUI-SaveImageWithMetaData", {}),
    ("receyuki/comfyui-prompt-reader-node", {'recursive': True, 'install_reqs': True}),
]:
    image = image.run_commands([git_clone_cmd(repo, **flags)])

# === Model Downloads ===
model_tasks = [
    ("unet/FLUX", "flux1-dev-Q8_0.gguf", "city96/FLUX.1-dev-gguf", None),
    ("clip/FLUX", "t5-v1_1-xxl-encoder-Q8_0.gguf", "city96/t5-v1_1-xxl-encoder-gguf", None),
    ("clip/FLUX", "clip_l.safetensors", "comfyanonymous/flux_text_encoders", None),
    ("checkpoints", "flux1-dev-fp8-all-in-one.safetensors", "camenduru/FLUX.1-dev", None),
    ("vae/FLUX", "ae.safetensors", "ffxvs/vae-flux", None),
]

extra_cmds = [
    f"wget https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth -P {MODELS_DIR}/upscale_models",
]

# === Persistent Volume ===
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
    os.makedirs(DATA_ROOT, exist_ok=True)
    if not os.path.exists(DATA_BASE):
        subprocess.run(f"cp -r {DEFAULT_COMFY_DIR} {DATA_ROOT}/", shell=True, check=True)
    os.chdir(DATA_BASE)

    # === Update Backend & Manager ===
    subprocess.run("git config pull.ff only", shell=True)
    subprocess.run("git pull --ff-only", shell=True)

    manager_dir = os.path.join(CUSTOM_NODES_DIR, "ComfyUI-Manager")
    if os.path.exists(manager_dir):
        os.chdir(manager_dir)
        subprocess.run("git pull --ff-only", shell=True)
    else:
        subprocess.run("comfy node install ComfyUI-Manager", shell=True)

    # === Download Missing Models ===
    for sub, fn, repo, subf in model_tasks:
        target = os.path.join(MODELS_DIR, sub, fn)
        if not os.path.exists(target):
            hf_download(sub, fn, repo, subf)
    for cmd in extra_cmds:
        subprocess.run(cmd, shell=True)

    # === Prepare dirs ===
    output_dir = os.path.join(DATA_BASE, "output")
    temp_dir = os.path.join(DATA_BASE, "temp")
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(temp_dir, exist_ok=True)

    os.environ["COMFY_OUTPUT_PATH"] = output_dir
    os.environ["COMFY_TEMP_PATH"] = temp_dir

    # === Jalankan Comfy di background (port internal 8188) ===
    def run_comfy():
        cmd = [
            "python", "main.py",
            "--listen", "0.0.0.0",
            "--port", "8188",
            "--force-fp16",
            "--disable-xformers",
            "--enable-cors-header", "*",
            "--output-directory", output_dir,
            "--temp-directory", temp_dir,
        ]
        subprocess.Popen(cmd, cwd=DATA_BASE, env=os.environ.copy())

    threading.Thread(target=run_comfy, daemon=True).start()

    # === FastAPI Wrapper (jadi GUI utama di port 8000) ===
    app_gui = FastAPI(title="Comfy Remote GUI")
    COMFY_API = "http://127.0.0.1:8188"

    @app_gui.get("/", response_class=HTMLResponse)
    def home():
        return """
        <html><body style='font-family: sans-serif; background: #101010; color: #eee; text-align:center;'>
        <h2>🧠 ComfyUI Remote GUI</h2>
        <form action="/generate" method="post">
          <input name="prompt" style="width:60%; padding:5px" placeholder="prompt"><br><br>
          <label>Steps: </label><input type="number" name="steps" value="25"><br><br>
          <label>CFG: </label><input type="number" name="cfg" value="6.5" step="0.1"><br><br>
          <button type="submit" style="padding:10px 20px;">Generate</button>
        </form>
        </body></html>
        """

    @app_gui.post("/generate")
    async def generate(req: Request):
        form = await req.form()
        prompt = form.get("prompt", "")
        steps = int(form.get("steps", 25))
        cfg = float(form.get("cfg", 6.5))

        comfy_payload = {
            "prompt": {
                "1": {"inputs": {"text": prompt}, "class_type": "CLIPTextEncode"},
                "2": {"inputs": {"samples": ["1"], "steps": steps, "cfg": cfg}, "class_type": "KSampler"}
            }
        }
        try:
            r = requests.post(f"{COMFY_API}/prompt", json=comfy_payload)
            job_id = r.json()["prompt_id"]

            for _ in range(40):
                time.sleep(1)
                res = requests.get(f"{COMFY_API}/history/{job_id}")
                if res.ok:
                    data = res.json()
                    if "images" in data[job_id]["outputs"]:
                        img_path = data[job_id]["outputs"]["images"][0]["path"]
                        img_url = f"{COMFY_API}/view?filename={img_path}"
                        return HTMLResponse(f"""
                        <html><body style='background:#000; color:#fff; text-align:center;'>
                        <h3>✅ Generated Image:</h3>
                        <img src='{img_url}' style='max-width:90%; border:2px solid #666;'><br><br>
                        <a href='/'>🔄 Generate Again</a></body></html>
                        """)
            return HTMLResponse("<h3>❌ Timeout waiting for image</h3>")
        except Exception as e:
            return HTMLResponse(f"<h3>⚠️ Error: {e}</h3>")

    print("🚀 Comfy backend running on :8188 | GUI on :8000")
    return app_gui
