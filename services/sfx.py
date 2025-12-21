# jarvis/services/sfx.py
import os
import time
import pygame

_initialized = False
_sounds = {}
_current_loop_channel = None

def init():
    global _initialized
    if _initialized: return
    try:
        # Frequenz 24kHz passt gut zu Google TTS (Journey Voice)
        # buffer=2048 reduziert CPU Last, erhöht Latenz minimal (unhörbar für TTS)
        pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=2048)
        _initialized = True
        print(" [SFX] Pygame Mixer initialized.")
    except Exception as e:
        print(f" [SFX Error] Init failed: {e}")

def get_sound(path_or_key):
    if not _initialized: init()
    if path_or_key in _sounds: return _sounds[path_or_key]
    
    if os.path.exists(path_or_key):
        try:
            s = pygame.mixer.Sound(path_or_key)
            s.set_volume(0.9)
            _sounds[path_or_key] = s
            return s
        except Exception as e:
            print(f" [SFX Error] Load failed: {e}")
    return None

def play(path_or_key):
    """Spielt Sound asynchron (Feuer & Vergessen)."""
    s = get_sound(path_or_key)
    if s: s.play()

def play_blocking(path_or_key):
    """Spielt Sound und blockiert den Code, bis er fertig ist (für TTS)."""
    if not _initialized: init()
    
    # Hier laden wir den Sound meist frisch (für TTS temp files), daher kein Caching
    if os.path.exists(path_or_key):
        try:
            s = pygame.mixer.Sound(path_or_key)
            # TTS darf etwas lauter sein als UI Sounds
            s.set_volume(0.9) 
            channel = s.play()
            
            # Warten bis fertig
            while channel and channel.get_busy():
                time.sleep(0.05)
        except Exception as e:
            print(f" [SFX Error] Play blocking failed: {e}")

def play_loop(path_or_key):
    global _current_loop_channel
    stop_loop()
    s = get_sound(path_or_key)
    if s:
        _current_loop_channel = s.play(loops=-1, fade_ms=200)

def stop_loop():
    global _current_loop_channel
    if _current_loop_channel:
        _current_loop_channel.fadeout(300)
        _current_loop_channel = None