import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
v2_sounds = os.path.join(ROOT, "assets", "sounds_src")
v3_sounds = os.path.join(ROOT, "assets", "sounds")

os.makedirs(v3_sounds, exist_ok=True)

mapping = {
    "navigation_forward-selection.ogg": "wake.pcm",
    "state-change_confirm-down.ogg": "sleep.pcm",
    "ui_loading.ogg": "thinking.pcm",
    "state-change_confirm-up.ogg": "success.pcm",
    "alert_error-03.ogg": "error.pcm"
}

for src, dst in mapping.items():
    src_path = os.path.join(v2_sounds, src)
    dst_path = os.path.join(v3_sounds, dst)
    if os.path.exists(src_path):
        print(f"Converting {src} to {dst}...")
        # 24kHz, 16-bit, mono
        cmd = f"ffmpeg -y -i {src_path} -ac 1 -ar 24000 -f s16le {dst_path}"
        subprocess.run(cmd, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"Successfully created {dst_path}")
    else:
        print(f"File not found: {src_path}")
