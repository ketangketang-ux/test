import os
import shutil
import subprocess
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
# ZIP INSTALLER (SAFE)
# =============================================

def install_zip(repo: str, name: str = None, branch: str = "main") -> str:
    if name is None:
        name = repo.split("/")[-1]
    dest = f"{DEFAULT_COMFY_DIR}/custom_nodes/{name}"
    zip_url = f"https://github.com/{repo}/archive/refs/heads/{branch}.zip"

    return (
        f"wget -q -O /tmp/{name}.zip {zip_url} && "
        f"rm -rf {dest} && "
        f"unzip -q /tmp/{name}.zip -d /tmp && "
        f"mv /tmp/{name}-{branch} {dest} && "
        f"rm /tmp/{name}.zip"
    )


# =============================================
# MODAL IMAGE BASE
# =============================================

import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("wget", "unzip", "libgl1-mesa-glx", "libglib2.0-0", "ffmpeg")
    .run_commands(["pip install --upgrade pip"])
)


# =============================================
# INSTALL COMFYUI FROM ZIP
# =============================================

image = image.run_commands([
    "wget -q -O /tmp/comfyui.zip https://github.com/comfyanonymous/ComfyUI/archive/refs/heads/master.zip"
])

image = image.run_commands([
    "unzip -q /tmp/comfyui.zip -d /tmp && "
    "rm -rf /root/comfy && mkdir -p /root/comfy && "
    "mv /tmp/ComfyUI-master /root/comfy/ComfyUI"
])


# =============================================
# INSTALL QWEN NODES (100% VALID)
# =============================================

# OFFICIAL QWEN IMAGE MODEL NODE
image = image.run_commands([
    install_zip("QwenLM/Qwen-Image", branch="main")
])

# COMMUNITY VL NODE
image = image.run_commands([
    install_zip("1038lab/ComfyUI-QwenVL", branch="main")
])


# =============================================
# OPTIONAL ENHANCEMENT NODES (SAFE)
# =============================================

extra_nodes = [
    ("ssitu/ComfyUI_UltimateSDUpscale", "main"),
    ("ltdrdata/ComfyUI-IPAdapter-Plus", "main")
]

for repo, branch in extra_nodes:
    image = image.run_commands([install_zip(repo, branch=branch)])


# =============================================
# APP INIT
# =============================================

vol = modal.Volume.from_name("comfyui-app", create_if_missing=True)

app = modal.App(
    name="comfyui-v12",
    image=image,
)


@app.function(
    gpu="A100-40GB",
    volumes={DATA_ROOT: vol},
)
@modal.web_server(8000)
def ui():

    os.environ["COMFY_DIR"] = DATA_BASE

    # First run: copy ComfyUI to volume
    if not os.path.exists(os.path.join(DATA_BASE, "main.py")):
        os.makedirs(DATA_ROOT, exist_ok=True)
        subprocess.run(f"cp -r {DEFAULT_COMFY_DIR} {DATA_ROOT}/", shell=True)

    # Launch ComfyUI
    subprocess.Popen(
        ["python3", "main.py", "--listen", "0.0.0.0", "--port", "8000"],
        cwd=DATA_BASE,
        env=os.environ.copy()
    )
