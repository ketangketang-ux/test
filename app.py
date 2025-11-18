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

# === HUGGINGFACE DOWNLOAD HELPER ===
def hf_download(subdir: str, filename: str, repo_id: str, subfolder: Optional[str] = None):
    target = os.path.join(MODELS_DIR, subdir)
    os.makedirs(target, exist_ok=True)
    final_path = os.path.join(target, filename)
    
    if os.path.exists(final_path) and os.path.getsize(final_path) > 1024*1024:
        print(f"✅ {filename} sudah ada, skip...")
        return
        
    print(f"⬇️ Downloading {filename}...")
    out = hf_hub_download(
        repo_id=repo_id, 
        filename=filename, 
        subfolder=subfolder, 
        local_dir=TMP_DL,
        local_dir_use_symlinks=False
    )
    shutil.move(out, final_path)
    print(f"✅ {filename} berhasil di-download")

# === INSIGHTFACE SETUP FIX ===
def setup_insightface_persistent():
    """Setup InsightFace di volume (persistent)"""
    print("="*60)
    print("SETUP INSIGHTFACE DIMULAI...")
    print("="*60)
    
    insightface_vol = os.path.join(DATA_ROOT, ".insightface", "models")
    insightface_home = "/root/.insightface"
    insightface_home_models = os.path.join(insightface_home, "models")
    
    if not os.path.exists(os.path.join(insightface_vol, "buffalo_l")):
        print("⬇️  Downloading InsightFace model...")
        os.makedirs(insightface_vol, exist_ok=True)
        
        try:
            zip_path = os.path.join(insightface_vol, "buffalo_l.zip")
            subprocess.run([
                "wget", "-q", "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
                "-O", zip_path
            ], check=True)
            
            subprocess.run(["unzip", "-q", zip_path, "-d", insightface_vol], check=True)
            os.remove(zip_path)
            print("✅ Model berhasil disimpan")
        except Exception as e:
            print(f"❌ ERROR download: {e}")
            return False
    else:
        print(f"✅ Model sudah ada di: {insightface_vol}/buffalo_l")
    
    # Buat symlink
    try:
        os.makedirs(insightface_home, exist_ok=True)
        if os.path.exists(insightface_home_models) and not os.path.islink(insightface_home_models):
            shutil.rmtree(insightface_home_models)
        
        if not os.path.islink(insightface_home_models):
            subprocess.run(["ln", "-sfn", insightface_vol, insightface_home_models], check=True)
        print(f"✅ Symlink: {insightface_home_models} → {insightface_vol}")
        
    except Exception as e:
        print(f"❌ Symlink error: {e}")
        shutil.copytree(insightface_vol, insightface_home_models, dirs_exist_ok=True)
        print("✅ Folder copied")
    
    return True

# === MODAL APP SETUP ===
import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "wget", "libgl1-mesa-glx", "libglib2.0-0", "ffmpeg", "unzip")
    .run_commands([
        "pip install --upgrade pip",
        "pip install --no-cache-dir comfy-cli uv",
        "uv pip install --system --compile-bytecode huggingface_hub[hf_transfer]==0.28.1",
        "comfy --skip-prompt install --nvidia",
        "pip install insightface onnxruntime-gpu",
        # Qwen dependencies
        "pip install -U openai qwen-vl-utils transformers accelerate pillow",
        "pip install -U modelscope",
    ])
    .env({
        "HF_HUB_ENABLE_HF_TRANSFER": "1",
        "PYTHONPATH": "/root/comfy/ComfyUI"
    })
)

# Install core nodes (yang paling stabil)
core_nodes = [
    "rgthree-comfy",
    "comfyui-impact-pack",
    "comfyui-impact-subpack",
    "comfyui-ipadapter-plus",
    "comfyui-inspire-pack",
    "comfyui_essentials",
    "wlsh_nodes",
    "ComfyUI_Comfyroll_CustomNodes",
    "ComfyUI-Manager",
    "ComfyUI-GGUF",
    "ComfyUI-KJNodes",
    "ComfyUI-YOLO",
]

for node in core_nodes:
    image = image.run_commands([f"comfy node install {node} || echo 'Skip: {node}'"])

# Git repos yang stabil (FIX: remove problematic QwenVL)
git_repos = [
    ("ssitu/ComfyUI_UltimateSDUpscale", {'recursive': True}),
    ("welltop-cn/ComfyUI-TeaCache", {}),
    ("nkchocoai/ComfyUI-SaveImageWithMetaData", {}),
    ("receyuki/comfyui-prompt-reader-node", {'recursive': True}),
]

# Clone git repos (FIX: lanjutkan meskipun clone gagal)
for repo, flags in git_repos:
    node_name = repo.split("/")[-1]
    dest = f"/root/comfy/ComfyUI/custom_nodes/{node_name}"
    clone_cmd = f"git clone https://github.com/{repo} {dest}"
    if flags.get('recursive'):
        clone_cmd += " --recursive"
    image = image.run_commands([f"{clone_cmd} || echo 'Clone failed: {node_name}'"])

# Install requirements untuk setiap node yang memilikinya (FIX)
for repo, _ in git_repos:
    node_name = repo.split("/")[-1]
    req_path = f"/root/comfy/ComfyUI/custom_nodes/{node_name}/requirements.txt"
    image = image.run_commands([f"if [ -f {req_path} ]; then pip install -r {req_path}; else echo 'No requirements for {node_name}'; fi"])

# Model download tasks
model_tasks = [
    ("unet/flux", "flux1-dev-Q8_0.gguf", "city96/FLUX.1-dev-gguf", None),
    ("clip/t5", "t5-v1_1-xxl-encoder-Q8_0.gguf", "city96/t5-v1_1-xxl-encoder-gguf", None),
    ("clip/clip_l", "clip_l.safetensors", "comfyanonymous/flux_text_encoders", None),
    ("checkpoints", "flux1-dev-fp8-all-in-one.safetensors", "camenduru/FLUX.1-dev", None),
    ("loras", "mjV6.safetensors", "strangerzonehf/Flux-Midjourney-Mix2-LoRA", None),
    ("vae/flux", "ae.safetensors", "ffxvs/vae-flux", None),
]

extra_cmds = [
    f"wget -q https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth -P {MODELS_DIR}/upscale_models",
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
# FIX: Tingkatkan startup_timeout untuk loading model besar
@modal.web_server(8000, startup_timeout=900)  # 15 menit
def ui():
    # Setup environment
    os.environ["COMFY_DIR"] = DATA_BASE
    os.environ["PYTHONPATH"] = f"{DATA_BASE}:{os.environ.get('PYTHONPATH', '')}"
    
    # First run setup
    if not os.path.exists(os.path.join(DATA_BASE, "main.py")):
        print("🔥 First run: Copying ComfyUI to volume...")
        os.makedirs(DATA_ROOT, exist_ok=True)
        if os.path.exists(DEFAULT_COMFY_DIR):
            subprocess.run(f"cp -r {DEFAULT_COMFY_DIR}/* {DATA_BASE}/", shell=True, check=True)
        else:
            os.makedirs(DATA_BASE, exist_ok=True)

    # Update ComfyUI
    print("🔄 Updating ComfyUI backend...")
    os.chdir(DATA_BASE)
    try:
        subprocess.run("git config --global --add safe.directory /data/comfy/ComfyUI", shell=True, check=False)
        subprocess.run("git config pull.ff only", shell=True, check=False)
        subprocess.run("git fetch origin", shell=True, check=False)
        subprocess.run("git reset --hard origin/main", shell=True, check=False)
        print("✅ ComfyUI updated successfully")
    except Exception as e:
        print(f"❌ Update error: {e}")

    # Install requirements untuk semua git nodes
    print("🔧 Installing requirements for git nodes...")
    for repo, _ in git_repos:
        node_name = repo.split("/")[-1]
        req_file = os.path.join(CUSTOM_NODES_DIR, node_name, "requirements.txt")
        if os.path.exists(req_file):
            try:
                print(f"📦 Installing deps for {node_name}...")
                subprocess.run(f"pip install -r {req_file}", shell=True, check=False)
            except Exception as e:
                print(f"⚠️ Failed to install {node_name} deps: {e}")
    
    # Install requirements untuk semua nodes yang ada
    print("🔧 Installing requirements for all nodes...")
    for node_dir in os.listdir(CUSTOM_NODES_DIR):
        req_file = os.path.join(CUSTOM_NODE
