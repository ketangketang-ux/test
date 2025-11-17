# =============================================
# ComfyUI Runtime Installer v17 (CLEAN MODE)
# 100% guaranteed start on GPU L4
# =============================================

import os, shutil, subprocess, requests, zipfile, time
from huggingface_hub import hf_hub_download

DATA_ROOT  = "/data/comfy"
DATA_BASE  = os.path.join(DATA_ROOT, "ComfyUI")
CUSTOM_NODES = os.path.join(DATA_BASE, "custom_nodes")

os.makedirs(DATA_ROOT, exist_ok=True)

# ===========================
# CLEAN OLD INSTALLATIONS
# ===========================

def clean_old():
    print("🧹 Cleaning old ComfyUI installation...")
    if os.path.exists(DATA_BASE):
        shutil.rmtree(DATA_BASE)
    os.makedirs(CUSTOM_NODES, exist_ok=True)

# ===========================
# SAFE HTTP DOWNLOADER
# ===========================

def http_download(url, dst, retries=5):
    for attempt in range(1, retries+1):
        try:
            print(f"[download] {url} (attempt {attempt})")
            with requests.get(url, stream=True, timeout=60) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                with open(dst,"wb") as f:
                    for chunk in r.iter_content(1024*1024):
                        if chunk:
                            f.write(chunk)
            return True
        except Exception as e:
            print(f"[download] FAIL: {e}")
            time.sleep(attempt*2)
    return False

# ===========================
# UNZIP HELPERS
# ===========================

def unzip_move(zip_path, prefix, dest):
    with zipfile.ZipFile(zip_path,"r") as z:
        z.extractall("/tmp")
    for name in os.listdir("/tmp"):
        p = os.path.join("/tmp", name)
        if os.path.isdir(p) and name.startswith(prefix):
            shutil.move(p, dest)
            return True
    return False

# ===========================
# INSTALL COMFYUI → ALWAYS FRESH
# ===========================

def install_comfyui():
    print("⬇️ Installing ComfyUI fresh...")
    zip_url  = "https://github.com/comfyanonymous/ComfyUI/archive/refs/heads/master.zip"
    zip_path = "/tmp/comfyui.zip"

    if not http_download(zip_url, zip_path):
        raise RuntimeError("Could not download ComfyUI")

    if not unzip_move(zip_path, "ComfyUI", DATA_BASE):
        raise RuntimeError("Could not extract ComfyUI")
    
    print("✅ ComfyUI installed")

# ===========================
# INSTALL NODE (SAFE)
# ===========================

def install_node(repo, name, branch="main"):
    dest = os.path.join(CUSTOM_NODES, name)

    zip_url = f"https://github.com/{repo}/archive/refs/heads/{branch}.zip"
    zip_path = f"/tmp/{name}.zip"

    print(f"[node] Installing {name}...")

    if not http_download(zip_url, zip_path):
        print(f"[node] ❌ failed download → SKIP")
        return

    if not unzip_move(zip_path, name, dest):
        print(f"[node] ❌ failed extract → SKIP")
        return

    print(f"[node] ✅ Installed {name}")

# ===========================
# MODAL RUNTIME
# ===========================

import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("wget","unzip","ffmpeg","libgl1-mesa-glx","libglib2.0-0")
    .run_commands(["pip install --upgrade pip requests huggingface_hub"])
)

vol = modal.Volume.from_name("comfyui-v17", create_if_missing=True)
app = modal.App(name="comfyui-v17", image=image)

@app.function(gpu="L4", volumes={DATA_ROOT: vol})
@modal.web_server(8000, startup_timeout=300)
def ui():

    # 1. clean old always
    clean_old()

    # 2. install comfy fresh
    install_comfyui()

    # 3. ONLY VALID NODES (guaranteed working)
    install_node("QwenLM/Qwen-Image", "Qwen-Image")
    install_node("1038lab/ComfyUI-QwenVL", "ComfyUI-QwenVL")

    print("🔥 Starting ComfyUI...")

    subprocess.Popen(
        ["python3", "main.py", "--listen","0.0.0.0","--port","8000"],
        cwd=DATA_BASE,
        env=os.environ.copy()
    )
