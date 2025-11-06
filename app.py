import os
import shutil
import subprocess
from typing import Optional
from huggingface_hub import hf_hub_download
import modal

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
        "pip install --no-cache-dir comfy-cli uv",
        "uv pip install --system --compile-bytecode huggingface_hub[hf_transfer]==0.28.1",
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
    gpu="L4",  # Ganti ke "A100-40GB" kalau mau nanti
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

    # === Update Backend ===
    subprocess.run("git config pull.ff only", shell=True)
    subprocess.run("git pull --ff-only", shell=True)

    # === Update Manager ===
    manager_dir = os.path.join(CUSTOM_NODES_DIR, "ComfyUI-Manager")
    if os.path.exists(manager_dir):
        os.chdir(manager_dir)
        subprocess.run("git config pull.ff only", shell=True)
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

    os.environ["COMFY_DIR"] = DATA_BASE
    cmd = ["comfy", "launch", "--", "--listen", "0.0.0.0", "--port", "8000", "--front-end-version", "Comfy-Org/ComfyUI_frontend@latest"]
    subprocess.Popen(cmd, cwd=DATA_BASE, env=os.environ.copy())
