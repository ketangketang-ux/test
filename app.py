# ==========================
# comfyui_tunnel.py  (final, tanpa web_server)
# ==========================
import os
import subprocess
import modal
from typing import Optional
from huggingface_hub import hf_hub_download
import shutil

# ---------- CONFIG ----------
DATA_ROOT = "/data/comfy"
DATA_BASE = os.path.join(DATA_ROOT, "ComfyUI")
GPU_TYPE = os.environ.get("MODAL_GPU_TYPE", "A100-40GB")

# ---------- IMAGE ----------
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "wget", "unzip", "build-essential", "libgl1-mesa-glx", "libglib2.0-0", "ffmpeg")
    .pip_install(
        "comfy-cli",
        "huggingface_hub[hf_transfer]",
        "insightface",
        "onnxruntime-gpu",
        "requests",
        "tqdm",
    )
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

# ---------- VOLUME ----------
vol = modal.Volume.from_name("comfyui-app", create_if_missing=True)
app = modal.App(name="comfyui-tunnel", image=image)

# ---------- APP FUNCTION ----------
@modal.concurrent(max_inputs=10)
@app.function(
    gpu=GPU_TYPE,
    timeout=3600,
    volumes={DATA_ROOT: vol},
    max_containers=1,
    scaledown_window=300,
)
def ui():
    DEFAULT_COMFY_DIR = "/root/comfy/ComfyUI"
    CUSTOM_NODES_DIR = os.path.join(DATA_BASE, "custom_nodes")
    MODELS_DIR = os.path.join(DATA_BASE, "models")
    TMP_DL = "/tmp/download"

    # 1. First-run copy
    if not os.path.exists(os.path.join(DATA_BASE, "main.py")):
        shutil.copytree(DEFAULT_COMFY_DIR, DATA_BASE, dirs_exist_ok=True)
    os.chdir(DATA_BASE)

    # 2. Update core & Manager
    subprocess.run("git config pull.ff only", shell=True, check=False)
    subprocess.run("git pull --ff-only", shell=True, check=False)

    manager = os.path.join(CUSTOM_NODES_DIR, "ComfyUI-Manager")
    if not os.path.exists(manager):
        subprocess.run("git clone https://github.com/ltdrdata/ComfyUI-Manager.git", shell=True, check=True)
    else:
        os.chdir(manager)
        subprocess.run("git pull --ff-only", shell=True, check=False)
        os.chdir(DATA_BASE)

    # 3. Install nodes
    nodes = [
        "rgthree-comfy",
        "comfyui-reactor-node",
        "ComfyUI-SUPIR",
        "ComfyUI-InsightFace",
        "ComfyUI_essentials",
    ]
    for n in nodes:
        subprocess.run(["comfy", "node", "install", n], check=False)

    # 4. InsightFace setup
    insight_vol = os.path.join(DATA_ROOT, ".insightface", "models")
    insight_home = "/root/.insightface"
    os.makedirs(insight_vol, exist_ok=True)

    if not os.path.exists(os.path.join(insight_vol, "buffalo_l")):
        zip_path = os.path.join(insight_vol, "buffalo_l.zip")
        subprocess.run([
            "wget", "-q", "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
            "-O", zip_path
        ], check=True)
        subprocess.run(["unzip", "-q", zip_path, "-d", insight_vol], check=True)
        os.remove(zip_path)

    os.makedirs(os.path.dirname(insight_home), exist_ok=True)
    if os.path.exists(insight_home) and not os.path.islink(insight_home):
        shutil.rmtree(insight_home)
    os.symlink(insight_vol, insight_home, target_is_directory=True)

    # 5. No tracking prompt
    manager_config = os.path.join(DATA_BASE, "user", "default", "ComfyUI-Manager", "config.ini")
    os.makedirs(os.path.dirname(manager_config), exist_ok=True)
    with open(manager_config, "w") as f:
        f.write("[default]\nnetwork_mode=private\nsecurity_level=weak\ntracking=false\n")

    # 6. Download models
    models = [
        ("checkpoints", "flux1-dev-fp8.safetensors", "camenduru/FLUX.1-dev", None),
        ("vae/FLUX", "ae.safetensors", "comfyanonymous/flux_vae", None),
        ("clip/FLUX", "t5xxl_fp8_e4m3fn.safetensors", "comfyanonymous/flux_text_encoders", None),
        ("clip/FLUX", "clip_l.safetensors", "comfyanonymous/flux_text_encoders", None),
    ]
    for sub, fn, repo, sf in models:
        target = os.path.join(MODELS_DIR, sub, fn)
        if not os.path.exists(target):
            out = hf_hub_download(
                repo_id=repo, filename=fn, subfolder=sf,
                local_dir=TMP_DL, local_dir_use_symlinks=False
            )
            shutil.move(out, target)

    # 7. Launch ComfyUI on port 8000
    subprocess.Popen([
        "python", "-m", "comfy", "launch", "--listen", "0.0.0.0", "--port", "8000",
        "--front-end-version", "Comfy-Org/ComfyUI_frontend@latest"
    ], cwd=DATA_BASE)

    # 8. Tunnels port 8000 → internet (tunggu 5 detik agar ComfyUI ready)
    import time
    time.sleep(5)
    subprocess.run(["modal", "tunnel", "8000"], check=True)


@app.local_entrypoint()
def main():
    ui.remote()
