from control.arduino import m1Digital_Write
import time as t
from utils.tools import log_event
from control.audio_manager import play_audio
import random
import threading

cannon_solenoid_pins = {
    1: 39,
    2: 41,
    3: 45,
}

cannon_light_pins = {
    1: 25,
    2: 27,
    3: 31,
}

cannon_smoke_pins = {
    1: 61,
    2: 60,
    3: 58,
}

audioFiles = [
        "CannonDesigned_1.wav",
        "CannonDesigned_2.wav",
        "CannonDesigned_3.wav",
        "CannonDesigned_4.wav"
    ]

interior_audioFiles = [
        "CannonFireInterior_1.wav",
        "CannonFireInterior_2.wav"
    ]

def fire_cannon(cannon_id:int):
    def main():
        """Fires the specified cannon by activating its solenoid, light, and smoke effects."""
        if cannon_id not in cannon_solenoid_pins:
            log_event(f"[cannons] Invalid cannon ID: {cannon_id}")
            return

        solenoid_pin = cannon_solenoid_pins[cannon_id]
        light_pin = cannon_light_pins[cannon_id]
        smoke_pin = cannon_smoke_pins[cannon_id]

        #log_event(f"DEBUG: solenoid:{solenoid_pin}, light:{light_pin}, smoke:{smoke_pin}")

        log_event(f"[cannons] Firing cannon {cannon_id}")

        # Activate smoke
        m1Digital_Write(smoke_pin, 0)
        log_event(f"[cannons] Activated smoke for cannon {cannon_id}")

        t.sleep(.1)  # Brief delay before firing

        audio = random.choice(audioFiles)
        interiorAudio = random.choice(interior_audioFiles)
        if cannon_id == 3:
            play_audio("beckettPA", audio, gain=1)
        else:
            play_audio("graveyard", audio, gain=1)
        play_audio("cargoHold", interiorAudio, gain=1)
        play_audio("quarterdeck", interiorAudio, gain=1)
        play_audio("gangway", interiorAudio, gain=1)

        # Activate light
        m1Digital_Write(light_pin, 0)
        log_event(f"[cannons] Activated light for cannon {cannon_id}")

        t.sleep(.1)

        # Activate solenoid to fire
        m1Digital_Write(solenoid_pin, 0)
        log_event(f"[cannons] Activated solenoid for cannon {cannon_id}")

        t.sleep(1)

        # Deactivate smoke
        m1Digital_Write(smoke_pin, 1)
        log_event(f"[cannons] Deactivated smoke for cannon {cannon_id}")
        
        t.sleep(.8)

        # Deactivate light
        m1Digital_Write(light_pin, 1)
        log_event(f"[cannons] Deactivated light for cannon {cannon_id}")

        t.sleep(3)

        m1Digital_Write(solenoid_pin, 1)
        log_event(f"[cannons] Deactivated solenoid for cannon {cannon_id}")

    threading.Thread(target=main, daemon=True, name=f"Cannon {cannon_id}").start()