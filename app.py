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

# === GITHUB CLONE HELPER ===
def git_clone_cmd(node_repo: str, recursive: bool = False, install_reqs: bool = False) -> str:
    name = node_repo.split("/")[-1]
    dest = os.path.join(DEFAULT_COMFY_DIR, "custom_nodes", name)
    cmd = f"git clone https://github.com/{node_repo} {dest}"
    if recursive:
        cmd += " --recursive"
    if install_reqs:
        cmd += f" && pip install -r {dest}/requirements.txt"
    return cmd

# === HUGGINGFACE DOWNLOAD HELPER ===
def hf_download(subdir: str, filename: str, repo_id: str, subfolder: Optional[str] = None):
    out = hf_hub_download(repo_id=repo_id, filename=filename, subfolder=subfolder, local_dir=TMP_DL)
    target = os.path.join(MODELS_DIR, subdir)
    os.makedirs(target, exist_ok=True)
    shutil.move(out, os.path.join(target, filename))

# === INSIGHTFACE PERSISTENT SETUP ===
def setup_insightface_persistent():
    print("="*60)
    print("SETUP INSIGHTFACE DIMULAI...")
    print("="*60)

    insightface_vol = os.path.join(DATA_ROOT, ".insightface", "models")
    insightface_home = "/root/.insightface"
    insightface_home_models = os.path.join(insightface_home, "models")

    if not os.path.exists(os.path.join(insightface_vol, "buffalo_l")):
        print("⬇️  Downloading insightface model...")
        os.makedirs(insightface_vol, exist_ok=True)

        try:
            zip_path = os.path.join(insightface_vol, "buffalo_l.zip")
            subprocess.run([
                "wget", "-q",
                "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
                "-O", zip_path
            ], check=True)

            subprocess.run(["unzip", "-q", zip_path, "-d", insightface_vol], check=True)
            os.remove(zip_path)
            print("✅ InsightFace model saved.")
        except Exception as e:
            print("❌ ERROR:", e)

    print("🔗 Creating symlink...")
    try:
        os.makedirs(insightface_home, exist_ok=True)
        if os.path.exists(insightface_home_models) and not os.path.islink(insightface_home_models):
            subprocess.run(["rm", "-rf", insightface_home_models], check=True)

        subprocess.run(["ln", "-sf", insightface_vol, insightface_home_models], check=True)
        print(f"✅ Linked: {insightface_home_models} → {insightface_vol}")

    except Exception as e:
        print("❌ Symlink error:", e)
        subprocess.run(["cp", "-rf", insightface_vol, insightface_home], check=True)
        print("🔁 Fallback copy done.")

    try:
        v = subprocess.run(["ls", "-lh", f"{insightface_home_models}/buffalo_l"],
                           capture_output=True, text=True, check=True)
        print(v.stdout)
    except:
        pass


# === MODAL APP SETUP ===
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

# === AUTO INSTALL MANDATORY NODES ===
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

# === INSTALL QWEN FULL PACK ===
QWEN_REPOS = [
    ("QwenLM/Qwen2-VL-Node", {}),
    ("QwenLM/Qwen2-VL-ComfyUI", {}),
    ("QwenLM/Qwen2-Image", {}),  # full Qwen image support
]

for repo, flags in QWEN_REPOS:
    image = image.run_commands([git_clone_cmd(repo, **flags)])

# === EXTRA GIT NODES ===
EXTRA_GIT = [
    ("ssitu/ComfyUI_UltimateSDUpscale", {'recursive': True}),
    ("welltop-cn/ComfyUI-TeaCache", {'install_reqs': True}),
    ("nkchocoai/ComfyUI-SaveImageWithMetaData", {}),
    ("receyuki/comfyui-prompt-reader-node", {'recursive': True, 'install_reqs': True}),
]

for repo, flags in EXTRA_GIT:
    image = image.run_commands([git_clone_cmd(repo, **flags)])


# === MODEL DOWNLOAD LIST ===
model_tasks = [
    ("unet/FLUX", "flux1-dev-Q8_0.gguf", "city96/FLUX.1-dev-gguf", None),
    ("clip/FLUX", "t5-v1_1-xxl-encoder-Q8_0.gguf", "city96/t5-v1_1-xxl-encoder-gguf", None),
    ("clip/FLUX", "clip_l.safetensors", "comfyanonymous/flux_text_encoders", None),
    ("checkpoints", "flux1-dev-fp8-all-in-one.safetensors", "camenduru/FLUX.1-dev", None),
    ("loras", "mjV6.safetensors", "strangerzonehf/Flux-Midjourney-Mix2-LoRA", None),
    ("vae/FLUX", "ae.safetensors", "ffxvs/vae-flux", None),
]

extra_cmds = [
    f"wget https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth -P {MODELS_DIR}/upscale_models",
]

# === BUILD APP ===
vol = modal.Volume.from_name("comfyui-app", create_if_missing=True)
app = modal.App(name="comfyui", image=image)


def repair_node(name):
    path = os.path.join(CUSTOM_NODES_DIR, name)
    if not os.path.exists(path):
        print(f"⚠️ Node missing → reinstall: {name}")
        subprocess.run(f"comfy node install {name}", shell=True, check=False)


@app.function(
    max_containers=1,
    scaledown_window=300,
    timeout=1800,
    gpu=os.environ.get('MODAL_GPU_TYPE', 'A100-40GB'),
    volumes={DATA_ROOT: vol},
)
@modal.concurrent(max_inputs=10)
@modal.web_server(8000, startup_timeout=300)
def ui():
    if not os.path.exists(os.path.join(DATA_BASE, "main.py")):
        print("Copying ComfyUI to volume...")
        os.makedirs(DATA_ROOT, exist_ok=True)
        if os.path.exists(DEFAULT_COMFY_DIR):
            subprocess.run(f"cp -r {DEFAULT_COMFY_DIR} {DATA_ROOT}/", shell=True, check=True)
        else:
            os.makedirs(DATA_BASE, exist_ok=True)

    print("Force updating ComfyUI...")
    os.chdir(DATA_BASE)
    try:
        subprocess.run("git fetch --all", shell=True, check=True)
        subprocess.run("git reset --hard origin/master || git reset --hard origin/main",
                       shell=True, check=True)
        print("✅ Updated.")
    except Exception as e:
        print("❌ Update error:", e)

    # Update ComfyUI-Manager
    manager_dir = os.path.join(CUSTOM_NODES_DIR, "ComfyUI-Manager")
    if os.path.exists(manager_dir):
        print("Updating ComfyUI-Manager...")
        os.chdir(manager_dir)
        subprocess.run("git fetch --all", shell=True, check=True)
        subprocess.run("git reset --hard origin/main || git reset --hard origin/master",
                       shell=True, check=True)
    else:
        print("Installing ComfyUI-Manager fresh...")
        subprocess.run("comfy node install ComfyUI-Manager", shell=True)

    # Upgrade pip & comfy-cli
    subprocess.run("pip install --upgrade pip comfy-cli", shell=True)

    # Requirements
    req_path = os.path.join(DATA_BASE, "requirements.txt")
    if os.path.exists(req_path):
        subprocess.run(f"pip install -r {req_path}", shell=True)

    os.makedirs(os.path.join(DATA_BASE, "user", "default", "ComfyUI-Manager"), exist_ok=True)
    with open(os.path.join(DATA_BASE, "user", "default", "ComfyUI-Manager", "config.ini"), "w") as f:
        f.write("[default]\nnetwork_mode = private\nsecurity_level = weak\nlog_to_file = false\n")

    for d in [CUSTOM_NODES_DIR, MODELS_DIR, TMP_DL]:
        os.makedirs(d, exist_ok=True)

    setup_insightface_persistent()

    # Download models
    for sub, fn, repo, subf in model_tasks:
        target = os.path.join(MODELS_DIR, sub, fn)
        if not os.path.exists(target):
            print(f"Downloading {fn}...")
            hf_download(sub, fn, repo, subf)

    for cmd in extra_cmds:
        subprocess.run(cmd, shell=True)

    # Repair missing mandatory nodes
    for node in MANDATORY_NODES:
        repair_node(node)

    os.environ["COMFY_DIR"] = DATA_BASE
    cmd = [
        "comfy", "launch", "--",
        "--listen", "0.0.0.0",
        "--port", "8000",
        "--front-end-version", "Comfy-Org/ComfyUI_frontend@latest"
    ]
    subprocess.Popen(cmd, cwd=DATA_BASE, env=os.environ.copy())
