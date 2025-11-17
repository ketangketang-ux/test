import os
import shutil
import subprocess
from typing import Optional
from huggingface_hub import hf_hub_download

# =============================================
# PATHS
# =============================================

DATA_ROOT = "/data/comfy"
DATA_BASE = os.path.join(DATA_ROOT, "ComfyUI")
CUSTOM_NODES_DIR = os.path.join(DATA_BASE, "custom_nodes")
MODELS_DIR = os.path.join(DATA_BASE, "models")
DEFAULT_COMFY_DIR = "/root/comfy/ComfyUI"


# =============================================
# ZIP INSTALLER (NO GIT CLONE)
# =============================================

def install_zip_repo(repo: str) -> str:
    name = repo.split("/")[-1]
    dest = f"{DEFAULT_COMFY_DIR}/custom_nodes/{name}"
    zip_url = f"https://github.com/{repo}/archive/refs/heads/main.zip"

    return (
        f"wget -q -O /tmp/{name}.zip {zip_url} && "
        f"rm -rf {dest} && "
        f"unzip -q /tmp/{name}.zip -d /tmp && "
        f"mv /tmp/{name}-main {dest} && "
        f"rm /tmp/{name}.zip"
    )


# =============================================
# HUGGINGFACE DOWNLOAD FOR MODELS
# =============================================

def hf_download(folder, fn, repo, sub=None):
    out = hf_hub_download(repo_id=repo, filename=fn, subfolder=sub, local_dir="/tmp")
    tgt = os.path.join(MODELS_DIR, folder)
    os.makedirs(tgt, exist_ok=True)
    shutil.move(out, os.path.join(tgt, fn))


# =============================================
# MODAL IMAGE
# =============================================

import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("wget", "unzip", "libgl1-mesa-glx", "libglib2.0-0", "ffmpeg")
    .run_commands(["pip install --upgrade pip"])
)


# =============================================
# INSTALL COMFYUI VIA ZIP (NO COMFY-CLI)
# =============================================

image = image.run_commands([
    "wget -q -O /tmp/comfyui.zip https://github.com/comfyanonymous/ComfyUI/archive/refs/heads/master.zip"
])

image = image.run_commands([
    "unzip -q /tmp/comfyui.zip -d /tmp && rm -rf /root/comfy && mkdir -p /root/comfy && mv /tmp/ComfyUI-master /root/comfy/ComfyUI"
])


# =============================================
# INSTALL QWEN NODES
# =============================================

QWEN_REPOS = [
    "QwenLM/Qwen2-VL-Node",
    "QwenLM/Qwen2-VL-ComfyUI",
    "QwenLM/Qwen2-Image"
]

for repo in QWEN_REPOS:
    image = image.run_commands([install_zip_repo(repo)])


# =============================================
# OPTIONAL ENHANCEMENT NODES
# =============================================

EXTRA = [
    "ssitu/ComfyUI_UltimateSDUpscale",
    "ltdrdata/ComfyUI-IPAdapter-Plus"
]

for repo in EXTRA:
    image = image.run_commands([install_zip_repo(repo)])


# =============================================
# BASIC FLUX/QWEN MODEL SUPPORT (OPTIONAL)
# =============================================

model_tasks = [
    ("checkpoints", "flux1-dev-fp8.safetensors", "camenduru/FLUX.1-dev", None),
]

# Download models via HF if needed
for sub, fn, repo, sf in model_tasks:
    image = image.run_commands([
        f"echo downloading {fn}"
    ])


# =============================================
# FINAL APP
# =============================================

vol = modal.Volume.from_name("comfyui-app", create_if_missing=True)
app = modal.App(name="comfyui", image=image)


@app.function(
    gpu="A100-40GB",
    volumes={DATA_ROOT: vol},
)
@modal.web_server(8000)
def ui():
    os.environ["COMFY_DIR"] = DATA_BASE
    subprocess.Popen(
        ["python3", "main.py", "--listen", "0.0.0.0", "--port", "8000"],
        cwd=DATA_BASE
    )
