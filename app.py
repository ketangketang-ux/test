import os
import shutil
import subprocess
import zipfile
import requests

# =============================================
# PATHS
# =============================================

DATA_ROOT = "/data/comfy"
DATA_BASE = os.path.join(DATA_ROOT, "ComfyUI")
CUSTOM_NODES = os.path.join(DATA_BASE, "custom_nodes")
MODELS_DIR = os.path.join(DATA_BASE, "models")
DEFAULT_COMFY_DIR = "/root/comfy/ComfyUI"

os.makedirs(CUSTOM_NODES, exist_ok=True)


# =============================================
# SAFE ZIP DOWNLOADER (RUNTIME)
# =============================================

def download_and_extract_zip(url, extract_name):
    zip_path = f"/tmp/{extract_name}.zip"
    extract_target = os.path.join(CUSTOM_NODES, extract_name)

    if os.path.exists(extract_target):
        print(f"✔ Node {extract_name} sudah ada, skip")
        return

    print(f"⬇️ Downloading: {url}")

    # STREAM DOWNLOAD anti-corrupt
    r = requests.get(url, stream=True)
    if r.status_code != 200:
        print(f"❌ ERROR download {url} (status {r.status_code})")
        return

    with open(zip_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024*1024):
            if chunk:
                f.write(chunk)

    print("📦 Extracting...")
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall("/tmp")

    # Find extracted folder
    folder = None
    for d in os.listdir("/tmp"):
        if d.startswith(extract_name) and os.path.isdir(f"/tmp/{d}"):
            folder = d

    if folder is None:
        print(f"❌ ERROR tidak menemukan folder extract untuk {extract_name}")
        return

    shutil.move(f"/tmp/{folder}", extract_target)
    os.remove(zip_path)

    print(f"✅ Installed node: {extract_name}")


# =============================================
# MODAL IMAGE
# =============================================

import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("wget", "unzip", "libgl1-mesa-glx", "libglib2.0-0", "ffmpeg")
    .run_commands(["pip install --upgrade pip requests"])
)


# =============================================
# INSTALL COMFYUI IN BUILDER ONLY
# =============================================

image = image.run_commands([
    "wget -q -O /tmp/comfyui.zip https://github.com/comfyanonymous/ComfyUI/archive/refs/heads/master.zip"
])

image = image.run_commands([
    "unzip -q /tmp/comfyui.zip -d /tmp && "
    "rm -rf /root/comfy && mkdir -p /root/comfy && "
    "mv /tmp/ComfyUI-master /root/comfy/ComfyUI"
])


# =============================================
# MODAL APP
# =============================================

vol = modal.Volume.from_name("comfyui-v13", create_if_missing=True)
app = modal.App(name="comfyui-v13", image=image)


@app.function(
    gpu="A100-40GB",
    volumes={DATA_ROOT: vol},
)
@modal.web_server(8000)
def ui():

    os.environ["COMFY_DIR"] = DATA_BASE

    # First run: copy ComfyUI into volume
    if not os.path.exists(DATA_BASE):
        print("📁 Copying ComfyUI to persistent volume...")
        subprocess.run(f"cp -r {DEFAULT_COMFY_DIR} {DATA_ROOT}/", shell=True)

    os.makedirs(CUSTOM_NODES, exist_ok=True)

    # =============================================
    # RUNTIME NODE INSTALL (ANTI ERROR)
    # =============================================

    # OFFICIAL Qwen Image Node
    download_and_extract_zip(
        "https://github.com/QwenLM/Qwen-Image/archive/refs/heads/main.zip",
        "Qwen-Image"
    )

    # Stable Vision-Language node
    download_and_extract_zip(
        "https://github.com/1038lab/ComfyUI-QwenVL/archive/refs/heads/main.zip",
        "ComfyUI-QwenVL"
    )

    # Optional Upscale
    download_and_extract_zip(
        "https://github.com/ssitu/ComfyUI_UltimateSDUpscale/archive/refs/heads/main.zip",
        "ComfyUI_UltimateSDUpscale"
    )

    # Optional IPAdapter
    download_and_extract_zip(
        "https://github.com/ltdrdata/ComfyUI-IPAdapter-Plus/archive/refs/heads/main.zip",
        "ComfyUI-IPAdapter-Plus"
    )

    print("🔥 Semua node terinstall. Starting ComfyUI...")

    # Launch ComfyUI
    subprocess.Popen(
        ["python3", "main.py", "--listen", "0.0.0.0", "--port", "8000"],
        cwd=DATA_BASE,
        env=os.environ.copy()
    )
