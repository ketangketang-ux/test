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


# === ZIP FALLBACK GIT CLONE (ANTI-ERROR) ===
def git_clone_cmd(node_repo: str, recursive: bool = False, install_reqs: bool = False) -> str:
    """
    Clone GitHub repo tanpa autentikasi dengan ZIP fallback (stabil di Modal).
    Git clone dicoba dulu, kalau gagal → unduh main.zip dan extract.
    """
    name = node_repo.split("/")[-1]
    git_url = f"https://github.com/{node_repo}.git"
    zip_url = f"https://github.com/{node_repo}/archive/refs/heads/main.zip"

    dest = os.path.join(DEFAULT_COMFY_DIR, "custom_nodes", name)

    cmd = f"""
        mkdir -p {os.path.dirname(dest)} && \
        export GIT_TERMINAL_PROMPT=0 && \
        (git clone --depth 1 {git_url} {dest} || (
            echo '⚠️ git clone gagal, fallback ke ZIP...' && \
            wget -q -O /tmp/{name}.zip {zip_url} && \
            unzip -q /tmp/{name}.zip -d /tmp && \
            rm -rf {dest} && \
            mv /tmp/{name}-main {dest} && \
            rm -f /tmp/{name}.zip
        ))
    """

    if install_reqs:
        cmd += f" && if [ -f {dest}/requirements.txt ]; then pip install -r {dest}/requirements.txt || true; fi"

    return cmd


# === HUGGINGFACE DOWNLOAD HELPER ===
def hf_download(subdir: str, filename: str, repo_id: str, subfolder: Optional[str] = None):
    out = hf_hub_download(repo_id=repo_id, filename=filename, subfolder=subfolder, local_dir=TMP_DL)
    target = os.path.join(MODELS_DIR, subdir)
    os.makedirs(target, exist_ok=True)
    shutil.move(out, os.path.join(target, filename))


# === INSIGHTFACE SETUP ===
def setup_insightface_persistent():
    print("="*60)
    print("SETUP INSIGHTFACE DIMULAI...")
    print("="*60)

    vol = os.path.join(DATA_ROOT, ".insightface", "models")
    home = "/root/.insightface"
    home_models = os.path.join(home, "models")

    if not os.path.exists(os.path.join(vol, "buffalo_l")):
        print("⬇️ Downloading insightface...")
        os.makedirs(vol, exist_ok=True)
        zip_path = os.path.join(vol, "buffalo_l.zip")

        subprocess.run([
            "wget", "-q",
            "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
            "-O", zip_path
        ], check=True)

        subprocess.run(["unzip", "-q", zip_path, "-d", vol], check=True)
        os.remove(zip_path)

    print("🔗 Creating symlink...")
    os.makedirs(home, exist_ok=True)

    if os.path.exists(home_models) and not os.path.islink(home_models):
        subprocess.run(["rm", "-rf", home_models])

    subprocess.run(["ln", "-sf", vol, home_models])

    print("📂 Insightface OK.")


# === MODAL SETUP ===
import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "wget", "libgl1-mesa-glx", "libglib2.0-0", "ffmpeg")
    .run_commands([
        "pip install --upgrade pip",
        "pip install --no-cache-dir comfy-cli uv",
        "uv pip install --system --compile-bytecode huggingface_hub[hf_transfer]==0.28.1",
        "comfy --skip-prompt install --nvidia",
        "pip install insightface onnxruntime-gpu"
    ])
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)


# === MANDATORY NODES ===
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

image = image.run_commands([
    " ".join(["comfy", "node", "install"] + MANDATORY_NODES)
])


# === QWEN NODES (FULL PACK, PUBLIC REPOS) ===
QWEN_REPOS = [
    "QwenLM/Qwen2-VL-Node",
    "QwenLM/Qwen2-VL-ComfyUI",
    "QwenLM/Qwen2-Image",
]

for repo in QWEN_REPOS:
    image = image.run_commands([git_clone_cmd(repo)])


# === EXTRA GIT NODES ===
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
    f"wget -q https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth -P {MODELS_DIR}/upscale_models",
]


# === UTILITY ===
def repair_node(name):
    path = os.path.join(CUSTOM_NODES_DIR, name)
    if not os.path.exists(path):
        print(f"⚠️ Node missing → reinstall: {name}")
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
        print("Copying ComfyUI to volume...")
        os.makedirs(DATA_ROOT, exist_ok=True)
        if os.path.exists(DEFAULT_COMFY_DIR):
            subprocess.run(f"cp -r {DEFAULT_COMFY_DIR} {DATA_ROOT}/", shell=True)

    # Update ComfyUI
    os.chdir(DATA_BASE)
    subprocess.run("git fetch --all", shell=True)
    subprocess.run("git reset --hard origin/master || git reset --hard origin/main", shell=True)

    # Update Manager
    manager_dir = os.path.join(CUSTOM_NODES_DIR, "ComfyUI-Manager")
    if os.path.exists(manager_dir):
        os.chdir(manager_dir)
        subprocess.run("git fetch --all", shell=True)
        subprocess.run("git reset --hard origin/main || git reset --hard origin/master", shell=True)

    subprocess.run("pip install --upgrade pip comfy-cli", shell=True)

    reqfile = os.path.join(DATA_BASE, "requirements.txt")
    if os.path.exists(reqfile):
        subprocess.run(f"pip install -r {reqfile}", shell=True)

    os.makedirs(os.path.join(DATA_BASE, "user", "default", "ComfyUI-Manager"), exist_ok=True)

    setup_insightface_persistent()

    # Model downloads
    for sub, fn, repo, subf in model_tasks:
        target = os.path.join(MODELS_DIR, sub, fn)
        if not os.path.exists(target):
            hf_download(sub, fn, repo, subf)

    for cmd in extra_cmds:
        subprocess.run(cmd, shell=True)

    # Repair nodes
    for node in MANDATORY_NODES:
        repair_node(node)

    os.environ["COMFY_DIR"] = DATA_BASE

    cmd = [
        "comfy", "launch", "--",
        "--listen", "0.0.0.0",
        "--port", "8000",
        "--front-end-version", "Comfy-Org/ComfyUI_frontend@latest",
    ]

    subprocess.Popen(cmd, cwd=DATA_BASE, env=os.environ.copy())
