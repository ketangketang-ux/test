# ==========================
# comfyui_stable.py  (update otomatis + link langsung)
# ==========================
import os
import subprocess
import modal
import shutil

DATA_ROOT = "/data/comfy"
DATA_BASE = os.path.join(DATA_ROOT, "ComfyUI")
GPU_TYPE = os.environ.get("MODAL_GPU_TYPE", "A100-40GB")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "wget", "unzip", "build-essential", "libgl1-mesa-glx", "libglib2.0-0", "ffmpeg")
    .pip_install("comfy-cli", "huggingface_hub[hf_transfer]", "insightface", "onnxruntime-gpu", "requests", "tqdm")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

vol = modal.Volume.from_name("comfyui-app", create_if_missing=True)
app = modal.App(name="comfyui-stable", image=image)

@app.function(
    gpu=GPU_TYPE,
    timeout=3600,
    volumes={DATA_ROOT: vol},
    max_containers=1,
    scaledown_window=300,
)
@modal.web_server(8000, startup_timeout=300)
def ui():
    DEFAULT_COMFY_DIR = "/root/comfy/ComfyUI"
    CUSTOM_NODES_DIR = os.path.join(DATA_BASE, "custom_nodes")
    MODELS_DIR = os.path.join(DATA_BASE, "models")

    # 1. Copy ComfyUI ke volume (pertama kali)
    if not os.path.exists(os.path.join(DATA_BASE, "main.py")):
        shutil.copytree(DEFAULT_COMFY_DIR, DATA_BASE, dirs_exist_ok=True)
    os.chdir(DATA_BASE)

    # 2. Selalu update ComfyUI + Manager
    subprocess.run("git pull --ff-only", shell=True, check=False)
    manager = os.path.join(CUSTOM_NODES_DIR, "ComfyUI-Manager")
    if not os.path.exists(manager):
        subprocess.run("git clone https://github.com/ltdrdata/ComfyUI-Manager.git", shell=True, check=True)
    else:
        os.chdir(manager); subprocess.run("git pull --ff-only", shell=True, check=False); os.chdir(DATA_BASE)

    # 3. Suppress tracking prompt
    config = os.path.join(DATA_BASE, "user", "default", "ComfyUI-Manager", "config.ini")
    os.makedirs(os.path.dirname(config), exist_ok=True)
    with open(config, "w") as f:
        f.write("[default]\nnetwork_mode=private\nsecurity_level=weak\ntracking=false\n")

    # 4. Launch ComfyUI on port 8000
    subprocess.Popen([
        "python", "-m", "comfy", "launch", "--listen", "0.0.0.0", "--port", "8000",
        "--front-end-version", "Comfy-Org/ComfyUI_frontend@latest"
    ], cwd=DATA_BASE)


@app.local_entrypoint()
def main():
    ui.remote()
