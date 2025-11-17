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
    
    # Path di Modal Volume (persistent)
    insightface_vol = os.path.join(DATA_ROOT, ".insightface", "models")
    # Path default di home (tempat InsightFace cari)
    insightface_home = "/root/.insightface"
    insightface_home_models = os.path.join(insightface_home, "models")
    
    # 1. Cek & download model ke volume jika belum ada
    if not os.path.exists(os.path.join(insightface_vol, "buffalo_l")):
        print("⬇️  Model belum ada di volume, downloading...")
        os.makedirs(insightface_vol, exist_ok=True)
        
        try:
            # Download (FIX: hapus spasi di URL)
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
        # Qwen dependencies
        "pip install -U openai qwen-vl-utils transformers accelerate",
    ])
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

# Install nodes
image = image.run_commands([
    # Core nodes
    "comfy node install rgthree-comfy comfyui-impact-pack comfyui-impact-subpack",
    "comfy node install comfyui-ipadapter-plus comfyui-inspire-pack comfyui_essentials",
    "comfy node install wlsh_nodes ComfyUI_Comfyroll_CustomNodes",
    "comfy node install ComfyUI-Manager ComfyUI-GGUF ComfyUI-KJNodes",
    # YOLO & utils
    "comfy node install ComfyUI-YOLO",
])

# Git nodes
git_repos = [
    ("ssitu/ComfyUI_UltimateSDUpscale", {'recursive': True}),
    ("welltop-cn/ComfyUI-TeaCache", {'install_reqs': True}),
    ("nkchocoai/ComfyUI-SaveImageWithMetaData", {}),
    ("receyuki/comfyui-prompt-reader-node", {'recursive': True, 'install_reqs': True}),
]

for repo, flags in git_repos:
    image = image.run_commands([git_clone_cmd(repo, **flags)])

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

    # Update ComfyUI (FIXED: lebih robust dan simpel)
    print("🔄 Updating ComfyUI backend...")
    os.chdir(DATA_BASE)
    try:
        # Setup git config
        subprocess.run("git config --global --add safe.directory /data/comfy/ComfyUI", shell=True, check=False)
        subprocess.run("git config pull.ff only", shell=True, check=False)
        
        # Reset dan pull latest
        subprocess.run("git fetch origin", shell=True, check=True)
        subprocess.run("git reset --hard origin/main", shell=True, check=True)
        print("✅ ComfyUI updated successfully")
    except Exception as e:
        print(f"❌ Update error: {e}")
        print("🔄 Fallback: Reinstalling comfy-cli...")
        subprocess.run("pip install --upgrade --force-reinstall comfy-cli", shell=True, check=True)

    # Update ComfyUI-Manager
    manager_dir = os.path.join(CUSTOM_NODES_DIR, "ComfyUI-Manager")
    if os.path.exists(manager_dir):
        print("🔄 Updating ComfyUI-Manager...")
        os.chdir(manager_dir)
        subprocess.run("git pull --ff-only", shell=True, check=False)
        os.chdir(DATA_BASE)
    else:
        print("📦 Installing ComfyUI-Manager...")
        subprocess.run("comfy node install ComfyUI-Manager", shell=True, check=False)

    # Install/Update frontend (FIXED: tanpa --fast-deps)
    print("🎨 Updating ComfyUI frontend...")
    try:
        # Install requirements jika ada
        req_path = os.path.join(DATA_BASE, "requirements.txt")
        if os.path.exists(req_path):
            subprocess.run(f"pip install -r {req_path}", shell=True, check=False)
        
        # Update comfy
        subprocess.run("pip install --upgrade comfy-cli", shell=True, check=False)
        subprocess.run("comfy update", shell=True, check=False)
    except Exception as e:
        print(f"⚠️ Frontend update partial: {e}")

    # Configure manager
    manager_config_dir = os.path.join(DATA_BASE, "user", "default", "ComfyUI-Manager")
    os.makedirs(manager_config_dir, exist_ok=True)
    with open(os.path.join(manager_config_dir, "config.ini"), "w") as f:
        f.write("[default]\nnetwork_mode = private\nsecurity_level = weak\nlog_to_file = false\n")

    # Create dirs
    for d in [CUSTOM_NODES_DIR, MODELS_DIR, TMP_DL]:
        os.makedirs(d, exist_ok=True)

    # Setup InsightFace
    setup_insightface_persistent()

    # Download models
    print("⬇️ Checking models...")
    for sub, fn, repo, subf in model_tasks:
        hf_download(sub, fn, repo, subf)

    # Run extra commands
    for cmd in extra_cmds:
        subprocess.run(cmd, shell=True, check=False)

    # Verifikasi nodes dan models
    print("🔍 Verifikasi setup...")
    try:
        # Cek nodes
        nodes_count = len(os.listdir(CUSTOM_NODES_DIR)) if os.path.exists(CUSTOM_NODES_DIR) else 0
        print(f"📂 Custom nodes: {nodes_count} nodes terinstall")
        
        # Cek models
        for model_type in ["unet", "clip", "checkpoints", "loras", "vae"]:
            path = os.path.join(MODELS_DIR, model_type)
            if os.path.exists(path):
                files = os.listdir(path)
                print(f"📦 {model_type}: {len(files)} files")
    except Exception as e:
        print(f"⚠️ Verifikasi partial: {e}")

    # Launch ComfyUI
    print("🚀 Launching ComfyUI...")
    launch_cmd = [
        "comfy", "launch",
        "--background",
        "--",
        "--listen", "0.0.0.0",
        "--port", "8000",
        "--front-end-version", "Comfy-Org/ComfyUI_frontend@latest",
        "--gpu-only"
    ]
    
    try:
        # Cek apakah port 8000 sudah terpakai
        result = subprocess.run(["lsof", "-i", ":8000"], capture_output=True)
        if result.returncode == 0:
            print("⚠️ Port 8000 terpakai, kill process lama...")
            subprocess.run(["pkill", "-f", "comfy"], check=False)
        
        # Launch
        process = subprocess.Popen(launch_cmd, cwd=DATA_BASE, env=os.environ.copy())
        print(f"✅ ComfyUI launched with PID: {process.pid}")
        
        # Tunggu sebentar konfirmasi
        import time
        time.sleep(5)
        
    except Exception as e:
        print(f"❌ Launch error: {e}")
        # Fallback launch manual
        print("🔄 Fallback: Launch manual...")
        os.chdir(DATA_BASE)
        subprocess.run([
            "python", "main.py",
            "--listen", "0.0.0.0",
            "--port", "8000"
        ], env=os.environ.copy())
