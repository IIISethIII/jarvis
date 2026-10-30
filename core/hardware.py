import os
import time
import threading
try:
    from smbus2 import SMBus
    HAS_SMBUS = True
except ImportError:
    HAS_SMBUS = False

# GPIO of the Voice Bonnet button; set JARVIS_BUTTON_PIN to another pin, or to "none" to disable it
DEFAULT_BUTTON_PIN = 23

class BonnetHardware:
    # KTD2027B I2C LED Driver for Voice Bonnet V2
    I2C_ADDR = 0x31
    REG_CONTROL = 0x00
    REG_CH_ENABLE = 0x04
    REG_BRIGHT_R = 0x06  

    def __init__(self, button_pin=None):
        self._pulse_thread = None
        self._stop_event = threading.Event()
        self._current_enable_state = None
        self._flash_until = 0.0
        self.button = self._init_button(button_pin)

        self.bus = None
        if HAS_SMBUS:
            try:
                self.bus = SMBus(1)
                self.bus.write_byte_data(self.I2C_ADDR, self.REG_CONTROL, 0x00)
                self._set_channels(0x00)
            except Exception as e:
                print(f"[Mock LED] I2C Init Failed: {e}")
                self.bus = None
        else:
            print("[Mock LED] smbus2 not installed.")

    @staticmethod
    def _init_button(button_pin):
        """Returns a gpiozero Button, or None if no button is configured or GPIO is unavailable."""
        if button_pin is None:
            env_pin = os.getenv("JARVIS_BUTTON_PIN", str(DEFAULT_BUTTON_PIN)).strip().lower()
            if env_pin in ("", "none", "off"):
                print("[Button] Disabled via JARVIS_BUTTON_PIN.")
                return None
            button_pin = int(env_pin)
        try:
            from gpiozero import Button
            # bounce_time filters contact chatter; the Bonnet button pulls the pin low when pressed
            return Button(button_pin, bounce_time=0.05)
        except Exception as e:
            print(f"[Button] Not available on GPIO {button_pin}: {e}")
            return None

    def set_button_handler(self, callback):
        """Calls callback() on every button press (from a gpiozero background thread)."""
        if self.button:
            self.button.when_pressed = callback

    def _is_flashing(self):
        return time.time() < self._flash_until

    def flash(self, r, g, b, duration=0.5):
        """Shows a colour briefly, then turns the LED off. Blocks for `duration`;
        set_color/turn_off calls from other threads are ignored meanwhile."""
        if not self.bus:
            print(f"[Mock LED] flash ({r}, {g}, {b})")
            return
        self.stop_effect()
        self._flash_until = time.time() + duration
        self._set_led(r, g, b)
        time.sleep(duration)
        self._flash_until = 0.0
        self.stop_effect()

    def _set_channels(self, state):
        if self.bus and self._current_enable_state != state:
            try:
                self.bus.write_byte_data(self.I2C_ADDR, self.REG_CH_ENABLE, state)
                self._current_enable_state = state
            except:
                pass

    def set_color(self, color_name, blink=False):
        """Adapter for V3 interface using the custom V2 I2C driver."""
        colors = {
            "blue": (0, 0, 255),
            "red": (255, 0, 0),
            "green": (0, 255, 0),
            "yellow": (255, 255, 0),
            "purple": (128, 0, 128),
            "white": (255, 255, 255),
            "off": (0, 0, 0)
        }
        r, g, b = colors.get(color_name.lower(), (0,0,0))

        if self._is_flashing():
            return
        if not self.bus:
            print(f"[Mock LED] {color_name} (blink={blink})")
            return
            
        if blink:
            self.start_pulse(r, g, b)
        else:
            self.stop_effect()
            self._set_led(r, g, b)

    def _set_led(self, r, g, b):
        if not self.bus: return
        try:
            self.bus.write_i2c_block_data(self.I2C_ADDR, self.REG_BRIGHT_R, [r, g, b])
            self._set_channels(0x15) 
        except Exception:
            pass

    def _pulse_animation(self, r, g, b):
        steps = 20
        while not self._stop_event.is_set():
            for i in range(0, steps + 1):
                if self._stop_event.is_set(): break
                f = (i / steps) ** 2 
                self._set_led(int(r * f), int(g * f), int(b * f))
                time.sleep(0.02) 
            for i in range(steps, -1, -1):
                if self._stop_event.is_set(): break
                f = (i / steps) ** 2 
                self._set_led(int(r * f), int(g * f), int(b * f))
                time.sleep(0.02)

    def start_pulse(self, r, g, b):
        self.stop_effect()
        if not self.bus: return
        self._stop_event.clear()
        self._pulse_thread = threading.Thread(target=self._pulse_animation, args=(r, g, b), daemon=True)
        self._pulse_thread.start()

    def stop_effect(self):
        self._stop_event.set()
        if self._pulse_thread:
            self._pulse_thread.join(timeout=0.2)
        if self.bus:
            try:
                self.bus.write_i2c_block_data(self.I2C_ADDR, self.REG_BRIGHT_R, [0, 0, 0])
                self._set_channels(0x00)
            except: pass

    def turn_off(self):
        if self._is_flashing():
            return
        self.stop_effect()

    def close(self):
        self.stop_effect()
        if self.button: self.button.close()
        if self.bus: self.bus.close()
