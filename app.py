# =============================================
# ComfyUI Runtime Installer v15 (GPU L4)
# Anti-error installer, auto-skip invalid nodes
# =============================================

import os
import shutil
import subprocess
import zipfile
import time
import requests
from huggingface_hub import hf_hub_download

DATA_ROOT = "/data/comfy"
DATA_BASE = os.path.join(DATA_ROOT, "ComfyUI")
CUSTOM_NODES = os.path.join(DATA_BASE, "custom_nodes")
MODELS_DIR = os.path.join(DATA_BASE, "models")
WORKFLOWS_DIR = os.path.join(DATA_BASE, "workflows")
DEFAULT_COMFY_DIR = "/root/comfy/ComfyUI"

os.makedirs(CUSTOM_NODES, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(WORKFLOWS_DIR, exist_ok=True)

FORCE_NODE_UPDATE = os.environ.get("FORCE_NODE_UPDATE", "0") in ("1","true","True")

# =========================
# Stable HTTP downloader
# =========================
def http_download_stream(url, dst, retries=5):
    for attempt in range(1, retries+1):
        try:
            print(f"[download] {url} (attempt {attempt})")
            with requests.get(url, stream=True, timeout=60) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                with open(dst, "wb") as f:
                    for chunk in r.iter_content(1024*1024):
                        if chunk:
                            f.write(chunk)
            return True
        except Exception as e:
            print(f"[download] failed: {e}")
            time.sleep(attempt * 2)
    return False

# =========================
# Unzip + detect folder
# =========================
def extract_and_move(zip_path, prefix, dest):
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall("/tmp")

    for name in os.listdir("/tmp"):
        p = os.path.join("/tmp", name)
        if os.path.isdir(p) and name.startswith(prefix):
            if os.path.exists(dest):
                shutil.rmtree(dest)
            shutil.move(p, dest)
            return True
    return False

# =========================
# Node installer (AUTO-SKIP)
# =========================
def install_node(repo, branch="main", name=None):
    if name is None:
        name = repo.split("/")[-1]

    dest = os.path.join(CUSTOM_NODES, name)
    if os.path.exists(dest) and not FORCE_NODE_UPDATE:
        print(f"[node] {name} exists → skip")
        return

    zip_url = f"https://github.com/{repo}/archive/refs/heads/{branch}.zip"
    zip_path = f"/tmp/{name}.zip"

    print(f"[node] Installing {name}")

    ok = http_download_stream(zip_url, zip_path)
    if not ok:
        print(f"[node] ❌ Failed to download {name} → SKIP NODE")
        return

    ok = extract_and_move(zip_path, name, dest)
    try: os.remove(zip_path)
    except: pass

    if not ok:
        print(f"[node] ❌ Failed to extract {name} → SKIP NODE")
        return

    print(f"[node] ✅ Installed {name}")

# =========================
# HF Model downloader
# =========================
def hf_get(repo, file, folder, sub=None):
    try:
        tdir = os.path.join(MODELS_DIR, folder)
        os.makedirs(tdir, exist_ok=True)
        out = hf_hub_download(repo_id=repo, filename=file, subfolder=sub, local_dir="/tmp")
        shutil.move(out, os.path.join(tdir, file))
        print(f"[hf] downloaded {file}")
    except Exception as e:
        print(f"[hf] ❌ failed: {e}")

# =========================
# Modal Runtime
# =========================
import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("wget", "unzip", "ffmpeg", "libgl1-mesa-glx", "libglib2.0-0")
    .run_commands(["pip install --upgrade pip requests huggingface_hub"])
)

# Install ComfyUI in builder
image = image.run_commands([
    "wget -q -O /tmp/comfyui.zip https://github.com/comfyanonymous/ComfyUI/archive/refs/heads/master.zip"
])
image = image.run_commands([
    "unzip -q /tmp/comfyui.zip -d /tmp && rm -rf /root/comfy && mkdir -p /root/comfy && mv /tmp/ComfyUI-master /root/comfy/ComfyUI"
])

vol = modal.Volume.from_name("comfyui-v15", create_if_missing=True)
app = modal.App(name="comfyui-v15", image=image)

@app.function(
    gpu="L4",         # 🔥 GPU L4 seperti permintaan
    volumes={DATA_ROOT: vol},
)
@modal.web_server(8000, startup_timeout=300)
def ui():

    os.environ["COMFY_DIR"] = DATA_BASE

    # Copy ComfyUI ke volume
    if not os.path.exists(DATA_BASE):
        print("[init] Copying ComfyUI to volume...")
        os.makedirs(DATA_ROOT, exist_ok=True)
        subprocess.run(f"cp -r {DEFAULT_COMFY_DIR} {DATA_ROOT}/", shell=True)

    # =========================
    # Install nodes runtime
    # =========================
    # VALID NODES ONLY
    nodes = [
        ("QwenLM/Qwen-Image", "main", "Qwen-Image"),
        ("1038lab/ComfyUI-QwenVL", "main", "ComfyUI-QwenVL"),
        ("ssitu/ComfyUI_UltimateSDUpscale", "main", "ComfyUI_UltimateSDUpscale"),
        # Impact Pack (IPAdapter working)
        ("ltdrdata/ComfyUI-Impact-Pack", "main", "ComfyUI-Impact-Pack"),
    ]

    for repo, branch, name in nodes:
        install_node(repo, branch, name)

    print("🔥 Nodes installed, starting ComfyUI...")

    subprocess.Popen(
        ["python3", "main.py", "--listen", "0.0.0.0", "--port", "8000"],
        cwd=DATA_BASE,
        env=os.environ.copy()
    )
