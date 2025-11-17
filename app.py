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
        # Install requirements after clone
        cmd += f" && pip install -r {dest}/requirements.txt"
    return cmd

# === HUGGINGFACE DOWNLOAD HELPER ===
def hf_download(subdir: str, filename: str, repo_id: str, subfolder: Optional[str] = None):
    target = os.path.join(MODELS_DIR, subdir)
    os.makedirs(target, exist_ok=True)
    final_path = os.path.join(target, filename)
    
    # Cek file sudah lengkap (min 1MB bukan corrupted)
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
    """
    Setup InsightFace di volume + symlink ke home (100% persistent)
    """
    print("="*60)
    print("SETUP INSIGHTFACE DIMULAI...")
    print("="*60)
    
    insightface_vol = os.path.join(DATA_ROOT, ".insightface", "models")
    insightface_home = "/root/.insightface"
    insightface_home_models = os.path.join(insightface_home, "models")
    
    # 1. Cek & download model ke volume jika belum ada
    if not os.path.exists(os.path.join(insightface_vol, "buffalo_l")):
        print("⬇️  Model belum ada di volume, downloading...")
        os.makedirs(insightface_vol, exist_ok=True)
        
        try:
            zip_path = os.path.join(insightface_vol, "buffalo_l.zip")
            subprocess.run([
                "wget", "-q", "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
                "-O", zip_path
            ], check=True)
            
            # Extract
            print("📦  Extracting...")
            subprocess.run(["unzip", "-q", zip_path, "-d", insightface_vol], check=True)
            os.remove(zip_path)
            
            print("✅ Model berhasil disimpan di volume")
        except Exception as e:
            print(f"❌ ERROR download: {e}")
            return False
    else:
        print(f"✅ Model sudah ada di: {insightface_vol}/buffalo_l")
    
    # 2. Buat symlink dari home → volume
    print("🔗 Membuat symlink...")
    try:
        os.makedirs(insightface_home, exist_ok=True)
        
        # Hapus folder dummy kalo ada
        if os.path.exists(insightface_home_models) and not os.path.islink(insightface_home_models):
            shutil.rmtree(insightface_home_models)
        
        # Buat symlink
        if not os.path.islink(insightface_home_models):
            subprocess.run(["ln", "-sfn", insightface_vol, insightface_home_models], check=True)
        print(f"✅ Symlink: {insightface_home_models} → {insightface_vol}")
        
    except Exception as e:
        print(f"❌ ERROR symlink: {e}")
        # Fallback: copy folder
        print("🔁 Fallback: Copy folder...")
        shutil.copytree(insightface_vol, insightface_home_models, dirs_exist_ok=True)
        print("✅ Folder copied")
    
    # 3. Verifikasi
    try:
        result = subprocess.run(["ls", "-lh", f"{insightface_home_models}/buffalo_l"], 
                              capture_output=True, text=True, check=True)
        print("📂 Verifikasi model:")
        print(result.stdout)
        return True
    except Exception as e:
        print(f"⚠️  Verifikasi gagal: {e}")
        return False

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
        # Qwen dependencies - INSTALLED DURING IMAGE BUILD
        "pip install -U openai qwen-vl-utils transformers accelerate pillow",
        # Additional deps for Qwen nodes
        "pip install -U modelscope",
        "pip install -U zhipuai",
    ])
    .env({
        "HF_HUB_ENABLE_HF_TRANSFER": "1",
        "PYTHONPATH": "/root/comfy/ComfyUI"
    })
)

# Install nodes - dengan error handling yang lebih baik
def install_nodes():
    # Core nodes yang penting
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
        try:
            subprocess.run(f"comfy node install {node}", shell=True, check=False)
            print(f"✅ Installed {node}")
        except Exception as e:
            print(f"⚠️ Failed to install {node}: {e}")

# Git nodes dengan dependencies yang jelas
git_repos = [
    ("ssitu/ComfyUI_UltimateSDUpscale", {'recursive': True, 'install_reqs': True}),
    ("welltop-cn/ComfyUI-TeaCache", {'install_reqs': True}),
    ("nkchocoai/ComfyUI-SaveImageWithMetaData", {}),
    ("receyuki/comfyui-prompt-reader-node", {'recursive': True, 'install_reqs': True}),
    # Qwen nodes - hanya install yang stabil
    ("QwenLM/ComfyUI_QwenVL", {'install_reqs': True}),  # Node Qwen yang lebih stabil
]

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
@modal.web_server(8000, startup_timeout=300)
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

    # Install/Update nodes dengan error handling
    print("📦 Installing/Updating nodes...")
    os.chdir(DATA_BASE)
    
    # Install core nodes via comfy-cli
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
        try:
            subprocess.run(f"comfy node install {node}", shell=True, check=False)
            print(f"✅ Installed {node}")
        except Exception as e:
            print(f"⚠️ Failed to install {node}: {e}")

    # Git clone nodes
    for repo, flags in git_repos:
        try:
            name = repo.split("/")[-1]
            dest = os.path.join(CUSTOM_NODES_DIR, name)
            if not os.path.exists(dest):
                print(f"📥 Cloning {name}...")
                cmd = git_clone_cmd(repo, **flags)
                subprocess.run(cmd, shell=True, check=True)
                
                # Install requirements jika ada
                if flags.get('install_reqs'):
                    req_file = os.path.join(dest, "requirements.txt")
                    if os.path.exists(req_file):
                        subprocess.run(f"pip install -r {req_file}", shell=True, check=False)
                        print(f"✅ Requirements installed for {name}")
        except Exception as e:
            print(f"❌ Failed to install {repo}: {e}")
            # Hapus folder jika clone gagal
            if os.path.exists(dest):
                shutil.rmtree(dest, ignore_errors=True)

    # Install requirements for nodes that have them
    print("🔧 Installing node dependencies...")
    for node_dir in os.listdir(CUSTOM_NODES_DIR):
        req_file = os.path.join(CUSTOM_NODES_DIR, node_dir, "requirements.txt")
        if os.path.exists(req_file):
            try:
                subprocess.run(f"pip install -r {req_file}", shell=True, check=False)
                print(f"✅ Installed deps for {node_dir}")
            except Exception as e:
                print(f"⚠️ Failed to install deps for {node_dir}: {e}")

    # Update pip & comfy-cli
    subprocess.run("pip install --upgrade pip comfy-cli", shell=True, check=False)

    # Install frontend
    print("🎨 Updating ComfyUI frontend...")
    try:
        req_path = os.path.join(DATA_BASE, "requirements.txt")
        if os.path.exists(req_path):
            subprocess.run(f"pip install -r {req_path}", shell=True, check=False)
        subprocess.run("comfy update", shell=True, check=False)
   
