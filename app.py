# ==========================
# comfy_final.py  (siap modal run)
# ==========================
import os
import subprocess
import modal
from typing import Optional
from huggingface_hub import hf_hub_download

# ---------- CONFIG ----------
DATA_ROOT = "/data/comfy"
DATA_BASE = os.path.join(DATA_ROOT, "ComfyUI")
CUSTOM_NODES_DIR = os.path.join(DATA_BASE, "custom_nodes")
MODELS_DIR = os.path.join(DATA_BASE, "models")
TMP_DL = "/tmp/download"
DEFAULT_COMFY_DIR = "/root/comfy/ComfyUI"

GPU_TYPE = os.environ.get("MODAL_GPU_TYPE", "A100-40GB")

# ---------- UTILS ----------
def git_clone(repo: str, recursive: bool = False, install_req: bool = False) -> str:
    name = repo.split("/")[-1].replace(".git", "")
    dest = os.path.join(CUSTOM_NODES_DIR, name)
    cmd = f'git clone https://github.com/{repo} "{dest}"'
    if recursive:
        cmd += " --recursive"
    if install_req and os.path.isfile(f"{dest}/requirements.txt"):
        cmd += f' && python -m pip install -r "{dest}/requirements.txt"'
    return cmd

def hf_dl(subdir: str, filename: str, repo_id: str, subfolder: Optional[str] = None):
    target_dir = os.path.join(MODELS_DIR, subdir)
    os.makedirs(target_dir, exist_ok=True)
    out = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        subfolder=subfolder,
        local_dir=TMP_DL,
        local_dir_use_symlinks=False,
    )
    shutil.move(out, os.path.join(target_dir, filename))

# ---------- IMAGE BUILD ----------
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "wget", "unzip", "build-essential", "libgl1-mesa-glx", "libglib2.0-0", "ffmpeg")
    .pip_install(
        "comfy-cli",
        "huggingface_hub[hf_transfer]",
        "insightface",
        "onnxruntime-gpu",
        "requests",
        "tqdm",
    )
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

# ---------- VOLUME ----------
vol = modal.Volume.from_name("comfyui-app", create_if_missing=True)
app = modal.App(name="comfyui-final", image=image)

# ---------- APP FUNCTION ----------
@app.function(
    gpu=GPU_TYPE,
    timeout=3600,
    volumes={DATA_ROOT: vol},
    concurrent=10,
    max_containers=1,
    scaledown_window=300,
    web_server=8000,
    startup_timeout=300,
)
def ui():
    import shutil

    # 1. First-run copy ComfyUI ke volume
    if not os.path.exists(os.path.join(DATA_BASE, "main.py")):
        print("First run – copy ComfyUI to volume ...")
        shutil.copytree(DEFAULT_COMFY_DIR, DATA_BASE, dirs_exist_ok=True)

    os.chdir(DATA_BASE)

    # 2. Update core & Manager
    subprocess.run("git config pull.ff only", shell=True, check=False)
    subprocess.run("git pull --ff-only", shell=True, check=False)

    manager = os.path.join(CUSTOM_NODES_DIR, "ComfyUI-Manager")
    if not os.path.exists(manager):
        subprocess.run("git clone https://github.com/ltdrdata/ComfyUI-Manager.git", shell=True, check=True)
    else:
        os.chdir(manager)
        subprocess.run("git pull --ff-only", shell=True, check=False)
        os.chdir(DATA_BASE)

    # 3. Install nodes via manager CLI (tambah / kurangi di list)
    nodes = [
        "rgthree-comfy",
        "comfyui-impact-pack",
        "comfyui-reactor-node",
        "ComfyUI-SUPIR",
        "ComfyUI-InsightFace",
        "ComfyUI_essentials",
        "ComfyUI-YOLO",
        "comfyui-ipadapter-plus",
    ]
    for n in nodes:
        subprocess.run(["comfy", "node", "install", n], check=False)

    # 4. InsightFace setup (persistent di volume)
    insight_vol = os.path.join(DATA_ROOT, ".insightface", "models")
    insight_home = "/root/.insightface"
    os.makedirs(insight_vol, exist_ok=True)

    if not os.path.exists(os.path.join(insight_vol, "buffalo_l")):
        print("⬇️  Download InsightFace model ...")
        zip_path = os.path.join(insight_vol, "buffalo_l.zip")
        subprocess.run([
            "wget", "-q", "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
            "-O", zip_path
        ], check=True)
        subprocess.run(["unzip", "-q", zip_path, "-d", insight_vol], check=True)
        os.remove(zip_path)

    # symlink agar ReActor & InsightFace temukan model
    os.makedirs(os.path.dirname(insight_home), exist_ok=True)
    if os.path.exists(insight_home) and not os.path.islink(insight_home):
        shutil.rmtree(insight_home)
    os.symlink(insight_vol, insight_home, target_is_directory=True)

    # 5. Download model (tambah / kurangi di list)
    models = [
        ("checkpoints", "flux1-dev-fp8.safetensors", "camenduru/FLUX.1-dev", None),
        ("vae/FLUX", "ae.safetensors", "comfyanonymous/flux_vae", None),
        ("clip/FLUX", "t5xxl_fp8_e4m3fn.safetensors", "comfyanonymous/flux_text_encoders", None),
        ("clip/FLUX", "clip_l.safetensors", "comfyanonymous/flux_text_encoders", None),
        ("upscale_models", "4x_NMKD-Superscale-SP_178000_G.pth", "nmkd/superscale-model", None),
    ]
    for sub, fn, repo, sf in models:
        target = os.path.join(MODELS_DIR, sub, fn)
        if not os.path.exists(target):
            print(f"⬇️  {fn}")
            hf_dl(sub, fn, repo, sf)

    # 6. Launch
    subprocess.Popen([
        "python", "-m", "comfy", "launch", "--listen", "0.0.0.0", "--port", "8000",
        "--front-end-version", "Comfy-Org/ComfyUI_frontend@latest"
    ], cwd=DATA_BASE)


@app.local_entrypoint()
def main():
    ui.remote()
