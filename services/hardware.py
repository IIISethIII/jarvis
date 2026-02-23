import time
import threading
from smbus2 import SMBus
from gpiozero import Button

class BonnetHardware:
    # KTD2027B I2C LED-Treiber Register für das Voice Bonnet V2
    I2C_ADDR = 0x31
    REG_CONTROL = 0x00
    REG_CH_ENABLE = 0x04
    REG_BRIGHT_R = 0x06  # 0x06=R, 0x07=G, 0x08=B

    def __init__(self, button_pin=23):
        self.bus = SMBus(1)
        self.button = Button(button_pin)
        self._pulse_thread = None
        self._stop_event = threading.Event()
        self._current_enable_state = None # Status-Cache verhindert I2C-Spam
        
        # Einmalig den Chip aufwecken und LEDs initial ausschalten
        try:
            self.bus.write_byte_data(self.I2C_ADDR, self.REG_CONTROL, 0x00)
            self._set_channels(0x00)
        except:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _set_channels(self, state):
        """Setzt das Enable-Register nur bei Statusänderung, um Flackern zu verhindern."""
        if self._current_enable_state != state:
            try:
                self.bus.write_byte_data(self.I2C_ADDR, self.REG_CH_ENABLE, state)
                self._current_enable_state = state
            except:
                pass

    def set_led(self, r_or_color, g=None, b=None):
        """
        Setzt die LED Farbe atomar. Akzeptiert entweder:
        set_led(255, 0, 0) oder set_led(DIM_BLUE)
        """
        if g is None and b is None:
            # Wenn ein Tupel übergeben wurde (z.B. aus config.py)
            r, g, b = r_or_color
        else:
            r = r_or_color

        r, g, b = int(r), int(g), int(b)

        try:
            self.bus.write_i2c_block_data(self.I2C_ADDR, self.REG_BRIGHT_R, [r, g, b])
            # IMMER aktiv lassen, um den PWM-Reset zu verhindern
            self._set_channels(0x15) 
        except Exception:
            pass

    def _pulse_animation(self, r, g, b):
        """Hintergrund-Thread für den Atmen-Effekt (weiche Gamma-Kurve)."""
        steps = 50
        while not self._stop_event.is_set():
            # Aufdimmen
            for i in range(0, steps + 1):
                if self._stop_event.is_set(): break
                # Quadratische Kurve für natürliches, logarithmisches Fade-In
                f = (i / steps) ** 2 
                self.set_led(int(r * f), int(g * f), int(b * f))
                time.sleep(0.02) # ~50 Hz Update-Rate
            
            # Abdimmen
            for i in range(steps, -1, -1):
                if self._stop_event.is_set(): break
                f = (i / steps) ** 2 
                self.set_led(int(r * f), int(g * f), int(b * f))
                time.sleep(0.02)

    def start_pulse(self, r_or_color, g=None, b=None):
        """Startet den Effekt ohne den Haupt-Thread zu blockieren."""
        if g is None and b is None:
            r, g, b = r_or_color
        else:
            r = r_or_color

        self.stop_effect()
        self._stop_event.clear()
        self._pulse_thread = threading.Thread(target=self._pulse_animation, args=(r, g, b), daemon=True)
        self._pulse_thread.start()

    def stop_effect(self):
        self._stop_event.set()
        if self._pulse_thread:
            self._pulse_thread.join(timeout=0.2)
        
        # Erst Farbe auf 0 setzen, DANN den Kanal abschalten
        try:
            self.bus.write_i2c_block_data(self.I2C_ADDR, self.REG_BRIGHT_R, [0, 0, 0])
            self._set_channels(0x00)
        except:
            pass

    def close(self):
        self.stop_effect()
        self.bus.close()