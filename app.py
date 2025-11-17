import os
import shutil
import subprocess
from typing import Optional
from huggingface_hub import hf_hub_download

# === PATHS ===
DATA_ROOT = "/data/comfy"
DATA_BASE = os.path.join(DATA_ROOT, "ComfyUI")
CUSTOM_NODES_DIR = os.path.join(DATA_BASE, "custom_nodes")
MODELS_DIR = os.path.join(DATA_BASE, "models")
TMP_DL = "/tmp/download"
DEFAULT_COMFY_DIR = "/root/comfy/ComfyUI"


# === SUPER SAFE SINGLE-LINE CLONE (NO MULTILINE, NO SUBSHELL) ===
def git_clone_cmd(repo: str, recursive: bool = False, install_reqs: bool = False) -> str:
    name = repo.split("/")[-1]
    dest = f"{DEFAULT_COMFY_DIR}/custom_nodes/{name}"
    rec = "--recursive" if recursive else ""
    zip_url = f"https://github.com/{repo}/archive/refs/heads/main.zip"
    zip_path = f"/tmp/{name}.zip"

    cmd = (
        f"mkdir -p $(dirname {dest}) && "
        f"export GIT_TERMINAL_PROMPT=0 && "
        f"git clone --depth 1 {rec} https://github.com/{repo}.git {dest} || "
        f"echo clone_fail && "
        f"wget -q -O {zip_path} {zip_url} && "
        f"unzip -q {zip_path} -d /tmp && "
        f"rm -rf {dest} && "
        f"mv /tmp/{name}-main {dest} && "
        f"rm -f {zip_path}"
    )

    if install_reqs:
        cmd += f" && if [ -f {dest}/requirements.txt ]; then pip install -r {dest}/requirements.txt || true; fi"

    return cmd


# === HF DOWNLOAD ===
def hf_download(subdir: str, filename: str, repo_id: str, subfolder: Optional[str] = None):
    out = hf_hub_download(repo_id=repo_id, filename=filename, subfolder=subfolder, local_dir=TMP_DL)
    target = os.path.join(MODELS_DIR, subdir)
    os.makedirs(target, exist_ok=True)
    shutil.move(out, os.path.join(target, filename))


# === INSIGHTFACE SETUP ===
def setup_insightface_persistent():
    print("== InsightFace Setup ==")
    vol = os.path.join(DATA_ROOT, ".insightface", "models")
    home = "/root/.insightface"
    home_models = os.path.join(home, "models")

    if not os.path.exists(os.path.join(vol, "buffalo_l")):
        os.makedirs(vol, exist_ok=True)
        subprocess.run("wget -q https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip -O /tmp/b.zip", shell=True, check=True)
        subprocess.run(f"unzip -q /tmp/b.zip -d {vol}", shell=True, check=True)
        os.remove("/tmp/b.zip")

    os.makedirs(home, exist_ok=True)
    if os.path.exists(home_models) and not os.path.islink(home_models):
        subprocess.run(f"rm -rf {home_models}", shell=True)
    subprocess.run(f"ln -sf {vol} {home_models}", shell=True)


# === MODAL IMAGE ===
import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "wget", "libgl1-mesa-glx", "libglib2.0-0", "ffmpeg")
    .run_commands(["pip install --upgrade pip"])
    .run_commands(["pip install --no-cache-dir comfy-cli uv"])
    .run_commands(["uv pip install --system --compile-bytecode huggingface_hub[hf_transfer]==0.28.1"])
    .run_commands(["comfy --skip-prompt install --nvidia"])
    .run_commands(["pip install insightface onnxruntime-gpu"])
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)


# === MUST-HAVE NODES ===
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
    "ComfyUI-Manager",
]

image = image.run_commands([" ".join(["comfy", "node", "install"] + MANDATORY_NODES)])


# === QWEN NODES (PUBLIC REPO, NO AUTH) ===
QWEN_REPOS = [
    "QwenLM/Qwen2-VL-Node",
    "QwenLM/Qwen2-VL-ComfyUI",
    "QwenLM/Qwen2-Image",
]

for repo in QWEN_REPOS:
    image = image.run_commands([git_clone_cmd(repo)])


# === EXTRA NODES ===
EXTRA_GIT = [
    ("ssitu/ComfyUI_UltimateSDUpscale", {'recursive': True}),
    ("welltop-cn/ComfyUI-TeaCache", {'install_reqs': True}),
    ("nkchocoai/ComfyUI-SaveImageWithMetaData", {}),
    ("receyuki/comfyui-prompt-reader-node", {'recursive': True, 'install_reqs': True}),
]

for repo, flags in EXTRA_GIT:
    image = image.run_commands([git_clone_cmd(repo, **flags)])


# === MODEL DOWNLOAD ===
model_tasks = [
    ("unet/FLUX", "flux1-dev-Q8_0.gguf", "city96/FLUX.1-dev-gguf", None),
    ("clip/FLUX", "t5-v1_1-xxl-encoder-Q8_0.gguf", "city96/t5-v1_1-xxl-encoder-gguf", None),
    ("clip/FLUX", "clip_l.safetensors", "comfyanonymous/flux_text_encoders", None),
    ("checkpoints", "flux1-dev-fp8-all-in-one.safetensors", "camenduru/FLUX.1-dev", None),
    ("loras", "mjV6.safetensors", "strangerzonehf/Flux-Midjourney-Mix2-LoRA", None),
    ("vae/FLUX", "ae.safetensors", "ffxvs/vae-flux", None),
]

extra_cmds = [
    f"wget -q https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth -P {MODELS_DIR}/upscale_models"
]


def repair_node(name):
    path = os.path.join(CUSTOM_NODES_DIR, name)
    if not os.path.exists(path):
        subprocess.run(f"comfy node install {name}", shell=True)


# === APP ===
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
        if os.path.exists(DEFAULT_COMFY_DIR):
            subprocess.run(f"cp -r {DEFAULT_COMFY_DIR} {DATA_ROOT}/", shell=True)

    os.chdir(DATA_BASE)
    subprocess.run("git fetch --all", shell=True)
    subprocess.run("git reset --hard origin/master || git reset --hard origin/main", shell=True)

    manager = os.path.join(CUSTOM_NODES_DIR, "ComfyUI-Manager")
    if os.path.exists(manager):
        os.chdir(manager)
        subprocess.run("git fetch --all", shell=True)
        subprocess.run("git reset --hard origin/main || git reset --hard origin/master", shell=True)

    subprocess.run("pip install --upgrade pip comfy-cli", shell=True)

    req = os.path.join(DATA_BASE, "requirements.txt")
    if os.path.exists(req):
        subprocess.run(f"pip install -r {req}", shell=True)

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
        ["comfy", "launch", "--", "--listen", "0.0.0.0", "--port", "8000", "--front-end-version", "Comfy-Org/ComfyUI_frontend@latest"],
        cwd=DATA_BASE,
        env=os.environ.copy(),
    )
