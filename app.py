import os
import shutil
import subprocess
from typing import Optional
from huggingface_hub import hf_hub_download

# Paths
DATA_ROOT = "/data/comfy"
DATA_BASE = os.path.join(DATA_ROOT, "ComfyUI")
CUSTOM_NODES_DIR = os.path.join(DATA_BASE, "custom_nodes")
MODELS_DIR = os.path.join(DATA_BASE, "models")
TMP_DL = "/tmp/download"

# ComfyUI default install location
DEFAULT_COMFY_DIR = "/root/comfy/ComfyUI"

def git_clone_cmd(node_repo: str, recursive: bool = False, install_reqs: bool = False) -> str:
    name = node_repo.split("/")[-1]
    dest = os.path.join(DEFAULT_COMFY_DIR, "custom_nodes", name)
    cmd = f"git clone https://github.com/{node_repo} {dest}"
    if recursive:
        cmd += " --recursive"
    if install_reqs:
        cmd += f" && pip install -r {dest}/requirements.txt"
    return cmd

def hf_download(subdir: str, filename: str, repo_id: str, subfolder: Optional[str] = None):
    out = hf_hub_download(repo_id=repo_id, filename=filename, subfolder=subfolder, local_dir=TMP_DL)
    target = os.path.join(MODELS_DIR, subdir)
    os.makedirs(target, exist_ok=True)
    shutil.move(out, os.path.join(target, filename))

import modal

# Build image with ComfyUI installed to default location /root/comfy/ComfyUI
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

# Install nodes to default ComfyUI location during build
image = image.run_commands([
    "comfy node install rgthree-comfy comfyui-impact-pack comfyui-impact-subpack ComfyUI-YOLO comfyui-inspire-pack comfyui_ipadapter_plus wlsh_nodes ComfyUI_Comfyroll_CustomNodes comfyui_essentials ComfyUI-GGUF"
])

# Git-based nodes baked into image at default ComfyUI location
for repo, flags in [
    ("ssitu/ComfyUI_UltimateSDUpscale", {'recursive': True}),
    ("welltop-cn/ComfyUI-TeaCache", {'install_reqs': True}),
    ("nkchocoai/ComfyUI-SaveImageWithMetaData", {}),
    ("receyuki/comfyui-prompt-reader-node", {'recursive': True, 'install_reqs': True}),
]:
    image = image.run_commands([git_clone_cmd(repo, **flags)])

# Model download tasks (will be done at runtime)
model_tasks = [
    ("unet/FLUX", "flux1-dev-Q8_0.gguf", "city96/FLUX.1-dev-gguf", None),
    ("clip/FLUX", "t5-v1_1-xxl-encoder-Q8_0.gguf", "city96/t5-v1_1-xxl-encoder-gguf", None),
    ("clip/FLUX", "clip_l.safetensors", "comfyanonymous/flux_text_encoders", None),
    ("checkpoints", "flux1-dev-fp8-all-in-one.safetensors", "camenduru/FLUX.1-dev", None),
    ("loras", "mjV6.safetensors", "strangerzonehf/Flux-Midjourney-Mix2-LoRA", None),
    ("vae/FLUX", "ae.safetensors", "ffxvs/vae-flux", None),
]

extra_cmds = [
    f"wget https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth  -P {MODELS_DIR}/upscale_models",
]

# Create volume
vol = modal.Volume.from_name("comfyui-app", create_if_missing=True)
app = modal.App(name="comfyui", image=image)

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
    # Check if volume is empty (first run)
    if not os.path.exists(os.path.join(DATA_BASE, "main.py")):
        print("First run detected. Copying ComfyUI from default location to volume...")
        
        os.makedirs(DATA_ROOT, exist_ok=True)
        
        if os.path.exists(DEFAULT_COMFY_DIR):
            print(f"Copying {DEFAULT_COMFY_DIR} to {DATA_BASE}")
            subprocess.run(f"cp -r {DEFAULT_COMFY_DIR} {DATA_ROOT}/", shell=True, check=True)
        else:
            print(f"Warning: {DEFAULT_COMFY_DIR} not found, creating empty structure")
            os.makedirs(DATA_BASE, exist_ok=True)
    
    # Fix detached HEAD and update ComfyUI backend to the latest version
    print("Fixing git branch and updating ComfyUI backend...")
    os.chdir(DATA_BASE)
    try:
        result = subprocess.run("git symbolic-ref HEAD", shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            print("Detected detached HEAD, checking out main branch...")
            subprocess.run("git checkout -B main origin/main", shell=True, check=True, capture_output=True, text=True)
            print("Successfully checked out main branch")
        subprocess.run("git config pull.ff only", shell=True, check=True, capture_output=True, text=True)
        result = subprocess.run("git pull --ff-only", shell=True, check=True, capture_output=True, text=True)
        print("Git pull output:", result.stdout)
    except Exception as e:
        print(f"Error updating ComfyUI backend: {e}")

    # Update ComfyUI-Manager
    manager_dir = os.path.join(CUSTOM_NODES_DIR, "ComfyUI-Manager")
    if os.path.exists(manager_dir):
        print("Updating ComfyUI-Manager...")
        os.chdir(manager_dir)
        try:
            subprocess.run("git config pull.ff only", shell=True, check=True, capture_output=True, text=True)
            result = subprocess.run("git pull --ff-only", shell=True, check=True)
            print("ComfyUI-Manager updated")
        except Exception as e:
            print(f"Error updating ComfyUI-Manager: {e}")
        os.chdir(DATA_BASE)
    else:
        print("ComfyUI-Manager directory not found, installing...")
        try:
            subprocess.run("comfy node install ComfyUI-Manager", shell=True, check=True)
            print("ComfyUI-Manager installed successfully")
        except Exception as e:
            print(f"Error installing ComfyUI-Manager: {e}")

    # Upgrade pip & comfy-cli
    print("Upgrading pip and comfy-cli...")
    subprocess.run("pip install --no-cache-dir --upgrade pip", shell=True, check=True)
    subprocess.run("pip install --no-cache-dir --upgrade comfy-cli", shell=True, check=True)

    # Update ComfyUI frontend
    requirements_path = os.path.join(DATA_BASE, "requirements.txt")
    if os.path.exists(requirements_path):
        print("Updating ComfyUI frontend...")
        subprocess.run(f"/usr/local/bin/python -m pip install -r {requirements_path}", shell=True, check=True)
    else:
        print(f"Warning: {requirements_path} not found")

    # Configure ComfyUI-Manager
    manager_config_dir = os.path.join(DATA_BASE, "user", "default", "ComfyUI-Manager")
    manager_config_path = os.path.join(manager_config_dir, "config.ini")
    print("Configuring ComfyUI-Manager...")
    os.makedirs(manager_config_dir, exist_ok=True)
    config_content = "[default]\nnetwork_mode = private\nsecurity_level = weak\nlog_to_file = false\n"
    with open(manager_config_path, "w") as f:
        f.write(config_content)
    print("ComfyUI-Manager configured")

    # Create directories
    for d in [CUSTOM_NODES_DIR, MODELS_DIR, TMP_DL]:
        os.makedirs(d, exist_ok=True)

    # 🎯 INI PERUBAHAN PENTING: Set path InsightFace ke volume!
    print("="*60)
    print("SETUP INSIGHTFACE DIMULAI...")
    print("="*60)
    os.environ["INSIGHTFACE_HOME"] = os.path.join(DATA_ROOT, ".insightface")
    print(f"INSIGHTFACE_HOME set to: {os.environ['INSIGHTFACE_HOME']}")

    # Download ComfyUI models (jika belum ada)
    print("Checking and downloading missing ComfyUI models...")
    for sub, fn, repo, subf in model_tasks:
        target = os.path.join(MODELS_DIR, sub, fn)
        if not os.path.exists(target):
            print(f"Downloading {fn}...")
            try:
                hf_download(sub, fn, repo, subf)
                print(f"✅ {fn} downloaded")
            except Exception as e:
                print(f"❌ Error downloading {fn}: {e}")
        else:
            print(f"⏭️  {fn} already exists")

    # Run extra commands
    print("Running additional downloads...")
    for cmd in extra_cmds:
        try:
            print(f"Running: {cmd}")
            result = subprocess.run(cmd, shell=True, check=False, cwd=DATA_BASE, capture_output=True, text=True)
            if result.returncode == 0:
                print("✅ Command completed")
            else:
                print(f"⚠️  Command failed: {result.stderr}")
        except Exception as e:
            print(f"❌ Error: {e}")

    # Set COMFY_DIR
    os.environ["COMFY_DIR"] = DATA_BASE
    
    # Launch ComfyUI
    print(f"🚀 Starting ComfyUI from {DATA_BASE}...")
    cmd = ["comfy", "launch", "--", "--listen", "0.0.0.0", "--port", "8000", "--front-end-version", "Comfy-Org/ComfyUI_frontend@latest"]
    process = subprocess.Popen(cmd, cwd=DATA_BASE, env=os.environ.copy())
