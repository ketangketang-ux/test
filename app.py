# FINAL SCRIPT v14 — Runtime installer + auto-model + workflow loader
import os
import shutil
import subprocess
import zipfile
import requests
import time
from huggingface_hub import hf_hub_download, hf_hub_download as hf_download_api

# =========================
# CONFIG / PATHS (editable via env)
# =========================
DATA_ROOT = "/data/comfy"
DATA_BASE = os.path.join(DATA_ROOT, "ComfyUI")
CUSTOM_NODES = os.path.join(DATA_BASE, "custom_nodes")
MODELS_DIR = os.path.join(DATA_BASE, "models")
WORKFLOWS_DIR = os.path.join(DATA_BASE, "workflows")
DEFAULT_COMFY_DIR = "/root/comfy/ComfyUI"

os.makedirs(CUSTOM_NODES, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(WORKFLOWS_DIR, exist_ok=True)

# Optional env config:
# QWEN model HB repo and filename, if provided script will download it.
# Example: export QWEN_MODEL_REPO="QwenLM/Qwen-Image-weights" ; export QWEN_MODEL_FILE="qwen-image-1.0.safetensors"
QWEN_MODEL_REPO = os.environ.get("QWEN_MODEL_REPO", "").strip()
QWEN_MODEL_FILE = os.environ.get("QWEN_MODEL_FILE", "").strip()

# Optional list of HF models to download (comma-separated triple: folder|repo|file)
# Example: export EXTRA_HF_MODELS="unet/FLUX|city96/FLUX.1-dev-gguf|flux1-dev-Q8_0.gguf,clip/FLUX|city96/...|..."
EXTRA_HF_MODELS = os.environ.get("EXTRA_HF_MODELS", "").strip()

# Workflow URLs (comma separated). Script downloads each JSON into workflows dir.
# Example: export WORKFLOW_URLS="https://example.com/mywf.json,https://..."
WORKFLOW_URLS = [u.strip() for u in os.environ.get("WORKFLOW_URLS", "").split(",") if u.strip()]

# Node list (repo, branch, friendly-name). We use repos known-valid in v13/v12.
NODES_TO_INSTALL = [
    ("QwenLM/Qwen-Image", "main", "Qwen-Image"),
    ("1038lab/ComfyUI-QwenVL", "main", "ComfyUI-QwenVL"),
    ("ssitu/ComfyUI_UltimateSDUpscale", "main", "ComfyUI_UltimateSDUpscale"),
    ("ltdrdata/ComfyUI-IPAdapter-Plus", "main", "ComfyUI-IPAdapter-Plus"),
]

# Force node update? if "1" will redownload nodes even if exist
FORCE_NODE_UPDATE = os.environ.get("FORCE_NODE_UPDATE", "0").strip() in ("1", "true", "True")

# Retry settings
HTTP_RETRIES = 5
RETRY_DELAY = 3  # seconds

# =========================
# UTIL: robust http download (streaming)
# =========================
def http_download_stream(url, dst_path, retries=HTTP_RETRIES, timeout=60):
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, timeout=timeout) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                total = r.headers.get("content-length")
                with open(dst_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024*1024):
                        if chunk:
                            f.write(chunk)
            return True
        except Exception as e:
            print(f"[download] attempt {attempt} failed: {e}")
            if attempt < retries:
                time.sleep(RETRY_DELAY * attempt)
            else:
                return False

# =========================
# UTIL: runtime unzip + move extracted folder
# =========================
def extract_and_move(zip_path, expected_prefix, dest_dir):
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall("/tmp")
    # find extracted folder starting with expected_prefix
    for name in os.listdir("/tmp"):
        p = os.path.join("/tmp", name)
        if os.path.isdir(p) and name.startswith(expected_prefix):
            if os.path.exists(dest_dir):
                shutil.rmtree(dest_dir)
            shutil.move(p, dest_dir)
            return True
    return False

# =========================
# INSTALL NODE AT RUNTIME (ZIP only)
# =========================
def install_node_runtime(repo, branch="main", name=None):
    if name is None:
        name = repo.split("/")[-1]
    dest = os.path.join(CUSTOM_NODES, name)
    if os.path.exists(dest) and not FORCE_NODE_UPDATE:
        print(f"[node] {name} exists -> skip")
        return True
    zip_url = f"https://github.com/{repo}/archive/refs/heads/{branch}.zip"
    zip_path = f"/tmp/{name}.zip"
    print(f"[node] Installing {name} from {zip_url}")
    ok = http_download_stream(zip_url, zip_path)
    if not ok:
        print(f"[node] download failed for {name}")
        return False
    ok2 = extract_and_move(zip_path, name, dest)
    os.remove(zip_path)
    if not ok2:
        print(f"[node] extract/move failed for {name}")
        return False
    print(f"[node] Installed {name}")
    return True

# =========================
# HF download wrapper with retries
# =========================
def hf_download_with_retries(repo_id, filename, target_folder, subfolder=None, retries=3):
    os.makedirs(target_folder, exist_ok=True)
    for attempt in range(1, retries+1):
        try:
            print(f"[hf] downloading {filename} from {repo_id} (attempt {attempt})")
            out = hf_hub_download(repo_id=repo_id, filename=filename, subfolder=subfolder, local_dir="/tmp")
            shutil.move(out, os.path.join(target_folder, filename))
            print(f"[hf] saved {filename} -> {target_folder}")
            return True
        except Exception as e:
            print(f"[hf] attempt {attempt} failed: {e}")
            if attempt < retries:
                time.sleep(2 * attempt)
            else:
                return False

# =========================
# DOWNLOAD WORKFLOWS (runtime)
# =========================
def download_workflow_urls(urls):
    for url in urls:
        fname = os.path.basename(url.split("?")[0])
        if not fname.endswith(".json"):
            fname = fname + ".json"
        dst = os.path.join(WORKFLOWS_DIR, fname)
        if os.path.exists(dst):
            print(f"[wf] {fname} exists -> skip")
            continue
        ok = http_download_stream(url, dst)
        if not ok:
            print(f"[wf] failed to download {url}")

# =========================
# MAIN runtime installer + launcher (for Modal/Colab)
# =========================
import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("wget", "unzip", "libgl1-mesa-glx", "libglib2.0-0", "ffmpeg")
    .run_commands(["pip install --upgrade pip"])
    .run_commands(["pip install requests huggingface_hub==0.15.1"])
)

# Note: Only ensure ComfyUI code is present in builder, node + models installed at runtime.
image = image.run_commands([
    "wget -q -O /tmp/comfyui.zip https://github.com/comfyanonymous/ComfyUI/archive/refs/heads/master.zip"
])
image = image.run_commands([
    "unzip -q /tmp/comfyui.zip -d /tmp && rm -rf /root/comfy && mkdir -p /root/comfy && mv /tmp/ComfyUI-master /root/comfy/ComfyUI"
])

vol = modal.Volume.from_name("comfyui-v14", create_if_missing=True)
app = modal.App(name="comfyui-v14", image=image)

@app.function(gpu="A100-40GB", volumes={DATA_ROOT: vol})
@modal.web_server(8000)
def ui():
    os.environ["COMFY_DIR"] = DATA_BASE
    # copy comfy to volume if not present
    if not os.path.exists(DATA_BASE):
        print("[init] copying ComfyUI to persistent volume")
        os.makedirs(DATA_ROOT, exist_ok=True)
        subprocess.run(f"cp -r {DEFAULT_COMFY_DIR} {DATA_ROOT}/", shell=True, check=False)

    # install nodes at runtime (robust)
    for repo, branch, name in NODES_TO_INSTALL:
        install_node_runtime(repo, branch=branch, name=name)

    # optional nodes from env (comma separated repo|branch|name)
    extra_env = os.environ.get("EXTRA_NODE_LIST","").strip()
    if extra_env:
        for entry in extra_env.split(","):
            if not entry.strip(): continue
            parts = entry.split("|")
            repo = parts[0].strip()
            branch = parts[1].strip() if len(parts)>1 else "main"
            name = parts[2].strip() if len(parts)>2 else None
            install_node_runtime(repo, branch=branch, name=name)

    # download workflows
    if WORKFLOW_URLS:
        print("[wf] downloading workflows")
        download_workflow_urls(WORKFLOW_URLS)

    # download hf models: Qwen optional
    if QWEN_MODEL_REPO and QWEN_MODEL_FILE:
        print(f"[hf] downloading QWEN model {QWEN_MODEL_FILE} from {QWEN_MODEL_REPO}")
        hf_download_with_retries(folder="qwen_models", filename=QWEN_MODEL_FILE, target_folder=os.path.join(MODELS_DIR,"qwen_models"), repo_id=QWEN_MODEL_REPO)

    # extra hf models from env (format folder|repo|file,comma separated)
    if EXTRA_HF_MODELS:
        for entry in EXTRA_HF_MODELS.split(","):
            if not entry.strip(): continue
            fld, repo, fn = [p.strip() for p in entry.split("|")]
            hf_download_with_retries(folder=fld, filename=fn, target_folder=os.path.join(MODELS_DIR, fld), repo_id=repo)

    # Final sanity: ensure custom_nodes folder exists
    os.makedirs(CUSTOM_NODES, exist_ok=True)

    print("[start] all installs done — launching ComfyUI")
    subprocess.Popen(["python3","main.py","--listen","0.0.0.0","--port","8000"], cwd=DATA_BASE, env=os.environ.copy())

