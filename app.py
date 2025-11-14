# ... [semua kode dari import sampai extra_cmds tetap sama] ...

@app.function(...)
def ui():
    # ... [kode di atas tetap] ...
    
    # Set COMFY_DIR
    os.environ["COMFY_DIR"] = DATA_BASE
    
    # FIX INSIGHTFACE PATH (PASTIKAN DI SINI)
    print("="*60)
    print("SETTING UP INSIGHTFACE PATH...")
    print("="*60)
    
    # Path model di volume (persistent)
    insightface_target = os.path.join(DATA_ROOT, ".insightface", "models")
    
    # Path default di home (tempat InsightFace cari)
    insightface_home = "/root/.insightface"
    
    # Buat folder home
    os.makedirs(insightface_home, exist_ok=True)
    
    # Buat symlink (hard link) supaya InsightFace baca dari volume
    try:
        subprocess.run(["ln", "-sf", insightface_target, os.path.join(insightface_home, "models")], check=True)
        print(f"✅ Symlink: {insightface_home}/models → {insightface_target}")
    except Exception as e:
        print(f"❌ Gagal symlink: {e}")
        # Fallback: copy folder (ga efisien tapi pasti work)
        print("🔁 Fallback: Copy folder ke home...")
        subprocess.run(["cp", "-r", insightface_target, insightface_home], check=True)
    
    # Verifikasi file
    try:
        result = subprocess.run(["ls", "-lh", f"{insightface_home}/models/buffalo_l"], 
                              capture_output=True, text=True, check=True)
        print("📂 Isi folder buffalo_l:")
        print(result.stdout)
    except Exception as e:
        print(f"⚠️  Model ga ketemu di path: {e}")
    
    # ... [lanjutkan kode setelahnya] ...
