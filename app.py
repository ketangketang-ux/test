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
TMP_DL = "/tmp/download"
DEFAULT_COMFY_DIR = "/root/comfy/ComfyUI"


# =============================================
# ZIP INSTALLER (NO GIT CLONE ANYWHERE)
# =============================================

def install_zip_repo(repo: str, install_reqs: bool = False) -> str:
    name = repo.split("/")[-1]
    dest = f"{DEFAULT_COMFY_DIR}/custom_nodes/{name}"
    zip_url = f"https://github.com/{repo}/archive/refs/heads/main.zip"
    zip_path = f"/tmp/{name}.zip"

    cmd = (
        f"mkdir -p $(dirname {dest}) && "
        f"wget -q -O {zip_path} {zip_url} && "
        f"rm -rf {dest} && "
        f"unzip -q {zip_path} -d /tmp && "
        f"mv /tmp/{name}-main {dest} && "
        f"rm -f {zip_path}"
    )

    if install_reqs:
        cmd += f" && if [ -f {dest}/requirements.txt ]; then pip install -r {dest}/requirements.txt || true; fi"

    return cmd


# =============================================
# HUGGINGFACE DOWNLOAD
# =============================================

def hf_download(subdir: str, filename: str, repo_id: str, subfolder: Optional[str] = None):
    out = hf_hub_download(repo_id=repo_id, filename=filename, subfolder=subfolder, local_dir="/tmp")
    target_dir = os.path.join(MODELS_DIR, subdir)
    os.makedirs(target_dir, exist_ok=True)
    shutil.move(out, os.path.join(target_dir, filename))


# =============================================
# INSIGHTFACE SETUP
# =============================================

def setup_insightface_persistent():
    print("== InsightFace Setup ==")
    vol = os.path.join(DATA_ROOT, ".insightface", "models")
    home = "/root/.insightface"
    home_models = os.path.join(home, "models")

    if not os.path.exists(os.path.join(vol, "buffalo_l")):
        os.makedirs(vol, exist_ok=True)
        subprocess.run(
            "wget -q https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip -O /tmp/a.zip",
            shell=True, check=True
        )
        subprocess.run(f"unzip -q /tmp/a.zip -d {vol}", shell=True, check=True)
        os.remove("/tmp/a.zip")

    os.makedirs(home, exist_ok=True)

    if os.path.exists(home_models) and not os.path.islink(home_models):
        subprocess.run(f"rm -rf {home_models}", shell=True)

    subprocess.run(f"ln -sf {vol} {home_models}", shell=True)


# =============================================
# MODAL IMAGE
# =============================================

import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("wget", "unzip", "libgl1-mesa-glx", "libglib2.0-0", "ffmpeg")
    .run_commands(["pip install --upgrade pip"])
    .run_commands(["pip install huggingface_hub[hf_transfer]==0.28.1 insightface onnxruntime-gpu"])
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

# Install ComfyUI (ZIP ONLY, NO COMFY-CLI)
image = image.run_commands([
    "wget -q -O /tmp/comfy.zip https://github.com/comfyanonymous/ComfyUI/archive/refs/heads/master.zip"
])

image = image.run_commands([
    "unzip -q /tmp/comfy.zip -d /tmp && rm -rf /root/comfy/ComfyUI && mkdir -p /root/comfy && mv /tmp/ComfyUI-master /root/comfy/ComfyUI"
])


# =============================================
# MUST-HAVE NODES
# =============================================

MANDATORY_NODES = [
    "rgthree-comfy",
    "comfyui-impact-pack",
    "comfyui-impact-subpack",
    "ComfyUI-YOLO",
    "comfyui-inspire-pack",
    "comfyui_ipadapter_plus",
    "wlsh_nodes",
    "ComfyUI_Comfyroll_CustomNodes",
    "comfyui_essentials",
    "ComfyUI-GGUF",
    "ComfyUI-Manager"
]

image = image.run_commands([
    " ".join(["comfy", "node", "install"] + MANDATORY_NODES)
])


# =============================================
# QWEN NODES (ZIP)
# =============================================

QWEN_REPOS = [
    "QwenLM/Qwen2-VL-Node",
    "QwenLM/Qwen2-VL-ComfyUI",
    "QwenLM/Qwen2-Image"
]

for repo in QWEN_REPOS:
    image = image.run_commands([install_zip_repo(repo)])


# EXTRA NODE PACKS
EXTRA_REPOS = [
    ("ssitu/ComfyUI_UltimateSDUpscale", False),
    ("welltop-cn/ComfyUI-TeaCache", True),
    ("nkchocoai/ComfyUI-SaveImageWithMetaData", False),
    ("receyuki/comfyui-prompt-reader-node", True)
]

for repo, req in EXTRA_REPOS:
    image = image.run_commands([install_zip_repo(repo, install_reqs=req)])


# =============================================
# MODELS
# =============================================

model_tasks = [
    ("unet/FLUX", "flux1-dev-Q8_0.gguf", "city96/FLUX.1-dev-gguf", None),
    ("clip/FLUX", "t5-v1_1-xxl-encoder-Q8_0.gguf", "city96/t5-v1_1-xxl-encoder-gguf", None),
    ("clip/FLUX", "clip_l.safetensors", "comfyanonymous/flux_text_encoders", None),
    ("checkpoints", "flux1-dev-fp8-all-in-one.safetensors", "camenduru/FLUX.1-dev", None),
    ("loras", "mjV6.safetensors", "strangerzonehf/Flux-Midjourney-Mix2-LoRA", None),
    ("vae/FLUX", "ae.safetensors", "ffxvs/vae-flux", None)
]

extra_cmds = [
    f"wget -q https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth -P {MODELS_DIR}/upscale_models"
]


def repair_node(name):
    if not os.path.exists(os.path.join(CUSTOM_NODES_DIR, name)):
        subprocess.run(f"comfy node install {name}", shell=True)


# =============================================
# MAIN APP
# =============================================

vol = modal.Volume.from_name("comfyui-app", create_if_missing=True)
app = modal.App(name="comfyui", image=image)


@app.function(
    max_containers=1,
    scaledown_window=300,
    timeout=1800,
    gpu=os.environ.get("MODAL_GPU_TYPE", "A100-40GB"),
    volumes={DATA_ROOT: vol},
)
@modal.concurrent(max_inputs=10)
@modal.web_server(8000, startup_timeout=300)
def ui():

    if not os.path.exists(os.path.join(DATA_BASE, "main.py")):
        os.makedirs(DATA_ROOT, exist_ok=True)
        subprocess.run(f"cp -r {DEFAULT_COMFY_DIR} {DATA_ROOT}/", shell=True)

    os.chdir(DATA_BASE)

    subprocess.run("pip install --upgrade pip comfy-cli || true", shell=True)

    reqfile = os.path.join(DATA_BASE, "requirements.txt")
    if os.path.exists(reqfile):
        subprocess.run(f"pip install -r {reqfile}", shell=True)

    setup_insightface_persistent()

    for sub, fn, repo, subf in model_tasks:
        target = os.path.join(MODELS_DIR, sub, fn)
        if not os.path.exists(target):
            hf_download(sub, fn, repo, subf)

    for cmd in extra_cmds:
        subprocess.run(cmd, shell=True)

    for node in MANDATORY_NODES:
        repair_node(node)

    os.environ["COMFY_DIR"] = DATA_BASE

    subprocess.Popen(
        ["python3", "main.py", "--listen", "0.0.0.0", "--port", "8000"],
        cwd=DATA_BASE,
        env=os.environ.copy(),
    )
