import os
import shutil
import subprocess
from typing import Optional
from huggingface_hub import hf_hub_download
import modal

# === Path Settings ===
DATA_ROOT = "/data/comfy"
DATA_BASE = os.path.join(DATA_ROOT, "ComfyUI")
CUSTOM_NODES_DIR = os.path.join(DATA_BASE, "custom_nodes")
MODELS_DIR = os.path.join(DATA_BASE, "models")
TMP_DL = "/tmp/download"
DEFAULT_COMFY_DIR = "/root/comfy/ComfyUI"

# === Helper Functions ===
def git_clone_cmd(repo: str, recursive=False, install_reqs=False):
    name = repo.split("/")[-1]
    dest = os.path.join(DEFAULT_COMFY_DIR, "custom_nodes", name)
    cmd = f"git clone https://github.com/{repo} {dest}"
    if recursive:
        cmd += " --recursive"
    if install_reqs:
        cmd += f" && pip install -r {dest}/requirements.txt"
    return cmd

def hf_download(subdir, filename, repo_id, subfolder=None):
    out = hf_hub_download(repo_id=repo_id, filename=filename, subfolder=subfolder, local_dir=TMP_DL)
    target = os.path.join(MODELS_DIR, subdir)
    os.makedirs(target, exist_ok=True)
    shutil.move(out, os.path.join(target, filename))

# === Modal Image Build ===
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "wget", "libgl1-mesa-glx", "libglib2.0-0", "ffmpeg")
    .run_commands([
        "pip install --upgrade pip",
        "pip install --no-cache-dir comfy-cli uv",
        "uv pip install --system --compile-bytecode huggingface_hub[hf_transfer]==0.28.1",
        "comfy --skip-prompt install --nvidia"
    ])
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

# === Auto Install Popular Nodes ===
image = image.run_commands([
    "comfy node install rgthree-comfy comfyui-impact-pack comfyui-impact-subpack comfyui_ipadapter_plus comfyui-inspire-pack wlsh_nodes comfyui_essentials ComfyUI-GGUF"
])

# === Extra Git Nodes ===
for repo, flags in [
    ("ssitu/ComfyUI_UltimateSDUpscale", {'recursive': True}),
    ("welltop-cn/ComfyUI-TeaCache", {'install_reqs': True}),
    ("nkchocoai/ComfyUI-SaveImageWithMetaData", {}),
    ("receyuki/comfyui-prompt-reader-node", {'recursive': True, 'install_reqs': True}),
]:
    image = image.run_commands([git_clone_cmd(repo, **flags)])

# === Model Downloads ===
model_tasks = [
    ("unet/FLUX", "flux1-dev-Q8_0.gguf", "city96/FLUX.1-dev-gguf", None),
    ("clip/FLUX", "t5-v1_1-xxl-encoder-Q8_0.gguf", "city96/t5-v1_1-xxl-encoder-gguf", None),
    ("clip/FLUX", "clip_l.safetensors", "comfyanonymous/flux_text_encoders", None),
    ("checkpoints", "flux1-dev-fp8-all-in-one.safetensors", "camenduru/FLUX.1-dev", None),
    ("vae/FLUX", "ae.safetensors", "ffxvs/vae-flux", None),
]

extra_cmds = [
    f"wget -q https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth -P {MODELS_DIR}/upscale_models",
]

# === Persistent Volume ===
vol = modal.Volume.from_name("comfyui-app", create_if_missing=True)
app = modal.App(name="comfyui", image=image)

@app.function(
    gpu="L4",
    timeout=1800,
    scaledown_window=300,
    volumes={DATA_ROOT: vol},
)
@modal.web_server(8000, startup_timeout=300)
def ui():
    # Ensure data directory exists
    os.makedirs(DATA_ROOT, exist_ok=True)
    
    # Only copy from default if ComfyUI doesn't exist in volume
    if not os.path.exists(DATA_BASE):
        print("First run - copying ComfyUI to volume...")
        subprocess.run(f"cp -r {DEFAULT_COMFY_DIR} {DATA_ROOT}/", shell=True, check=True)
        print("ComfyUI successfully copied to persistent volume")
    
    os.chdir(DATA_BASE)

    # === Update Backend ===
    print("Updating ComfyUI backend...")
    subprocess.run("git config pull.ff only", shell=True)
    result = subprocess.run("git pull --ff-only", shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Git pull failed: {result.stderr}")
    else:
        print(f"Git pull output: {result.stdout}")

    # === Update Manager ===
    manager_dir = os.path.join(CUSTOM_NODES_DIR, "ComfyUI-Manager")
    if os.path.exists(manager_dir):
        print("Updating ComfyUI-Manager...")
        os.chdir(manager_dir)
        subprocess.run("git config pull.ff only", shell=True)
        result = subprocess.run("git pull --ff-only", shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print("ComfyUI-Manager updated successfully")
        else:
            print(f"ComfyUI-Manager update failed: {result.stderr}")
        os.chdir(DATA_BASE)  # Return to base directory
    else:
        print("Installing ComfyUI-Manager...")
        subprocess.run("comfy node install ComfyUI-Manager", shell=True)

    # === Download Missing Models ===
    print("Checking for missing models...")
    os.makedirs(TMP_DL, exist_ok=True)
    
    for sub, fn, repo, subf in model_tasks:
        target = os.path.join(MODELS_DIR, sub, fn)
        if not os.path.exists(target):
            print(f"Downloading {fn} to {sub}...")
            try:
                hf_download(sub, fn, repo, subf)
                print(f"✓ Successfully downloaded {fn}")
            except Exception as e:
                print(f"✗ Failed to download {fn}: {e}")
        else:
            print(f"✓ {fn} already exists")

    # === Download Extra Models ===
    print("Downloading extra models...")
    for cmd in extra_cmds:
        try:
            subprocess.run(cmd, shell=True, check=True)
            print(f"✓ Successfully executed: {cmd.split()[1]}")
        except subprocess.CalledProcessError as e:
            print(f"✗ Failed to execute command: {e}")

    # === Commit changes to volume ===
    try:
        vol.commit()
        print("✓ Changes committed to persistent volume")
    except Exception as e:
        print(f"✗ Failed to commit volume: {e}")

    # === Launch ComfyUI ===
    print("Starting ComfyUI server...")
    os.environ["COMFY_DIR"] = DATA_BASE
    cmd = [
        "comfy", "launch", "--", 
        "--listen", "0.0.0.0", 
        "--port", "8000", 
        "--front-end-version", "Comfy-Org/ComfyUI_frontend@latest"
    ]
    
    process = subprocess.Popen(
        cmd, 
        cwd=DATA_BASE, 
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    # Print initial output
    print("ComfyUI is starting...")
    try:
        # Read and print first few lines of output
        for _ in range(10):
            line = process.stdout.readline()
            if line:
                print(line.strip())
    except:
        pass
    
    print("ComfyUI should be available at: http://0.0.0.0:8000")
    
    # Wait for process
    process.wait()

# === Volume Backup Function (Optional) ===
@app.function(volumes={DATA_ROOT: vol})
def backup_volume():
    """Manual backup function if needed"""
    print("Volume backup completed")
    vol.commit()

if __name__ == "__main__":
    with app.run():
        print("ComfyUI Modal app deployed!")
