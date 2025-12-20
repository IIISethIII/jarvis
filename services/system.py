# jarvis/services/system.py
import os
import subprocess
import time

def init_audio_settings():
    """Sets ALSA settings optimized for your specific setup."""
    print(" [Audio] Setting Optimized Settings...")
    time.sleep(1)

def init_audio_settings():
    # ... (Code davor) ...
    try:
        # 1. Maximale Basis-Lautstärke
        os.system("amixer -c 0 sset 'Mono ADC' 100%")
        os.system("amixer -c 0 sset 'ADC' 100%")

        # 2. Der "Sweet Spot" Boost (Deine Einstellung)
        os.system("amixer -c 0 sset 'Mono ADC Boost' 2")
        os.system("amixer -c 0 sset 'ADC Boost' 1")
        
        # 3. Wiedergabe
        os.system("amixer -c 0 sset 'Speaker' 60%")
        os.system("amixer -c 0 sset 'Speaker Channel' unmute")
        
        return True
    except Exception as e:
        print(f" [Audio Error] {e}")
        return False

def set_system_volume(volume_level):
    """Sets local system volume."""
    try:
        vol = max(0, min(100, int(volume_level)))
        os.system(f"amixer -c 0 sset 'Speaker' {vol}%")
        return f"Systemlautstärke auf {vol} Prozent gesetzt."
    except:
        return "Fehler beim Einstellen der Systemlautstärke."

def restart_service():
    """Restarts the systemd service via subprocess."""
    print(" [System] Service Restart initiated...")
    try:
        subprocess.Popen(
            "sleep 5; sudo systemctl restart jarvis.service",
            shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        return "Der Service startet sich in 5 Sekunden neu."
    except Exception as e:
        print(f" [RESTART ERROR] {e}")
        return f"Fehler: {e}"