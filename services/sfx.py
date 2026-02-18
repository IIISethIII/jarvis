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

def get_sound(path_or_key, volume=0.9):
    if not _initialized: init()
    s = None
    if path_or_key in _sounds:
        s = _sounds[path_or_key]
    elif os.path.exists(path_or_key):
        try:
            s = pygame.mixer.Sound(path_or_key)
            _sounds[path_or_key] = s
        except Exception as e:
            print(f" [SFX Error] Load failed: {e}")
            return None

    if s:
        s.set_volume(volume)
    return s

def play(path_or_key, volume=0.9):
    """Spielt Sound asynchron (Feuer & Vergessen)."""
    s = get_sound(path_or_key, volume=volume)
    if s: s.play()

def play_blocking(path_or_key, interrupt_check=None, volume=0.9):
    """
    Spielt Sound und blockiert, erlaubt aber Unterbrechung.
    interrupt_check: Eine Funktion, die True zurückgibt, wenn abgebrochen werden soll.
    Rückgabe: True wenn unterbrochen wurde, sonst False.
    """
    if not _initialized: init()
    
    was_interrupted = False

    if os.path.exists(path_or_key):
        try:
            s = pygame.mixer.Sound(path_or_key)
            s.set_volume(volume) 
            channel = s.play()
            
            # Warten bis fertig ODER Unterbrechung
            while channel and channel.get_busy():
                # Prüfen ob unterbrochen werden soll
                if interrupt_check and interrupt_check():
                    channel.stop()
                    was_interrupted = True
                    break
                
                time.sleep(0.05) # Kleines Sleep gegen 100% CPU
        except Exception as e:
            print(f" [SFX Error] Play blocking failed: {e}")
            
    return was_interrupted

def play_loop(path_or_key, volume=0.9):
    global _current_loop_channel
    stop_loop()
    s = get_sound(path_or_key, volume=volume)
    if s:
        _current_loop_channel = s.play(loops=-1, fade_ms=200)

def stop_loop():
    global _current_loop_channel
    if _current_loop_channel:
        _current_loop_channel.fadeout(300)
        _current_loop_channel = None