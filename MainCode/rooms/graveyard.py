# rooms/graveyard.py
import time as t
from context import house
from control.audio_manager import play_audio
from utils.tools import BreakCheck, log_event
import random
import threading
from control.dimmer_controller import dim, dimmer_flicker
from control import dimmer_controller as d
from control.arduino import m1Digital_Write
from control import cannons
from control import remote_sensor_monitor as rsm
from control.houseLights import toggleHouseLights
import wave, contextlib
from pathlib import Path

Scripted_Event = False

DEFAULT_SOUND_DIR = (Path(__file__).resolve().parents[3] / "Assets" / "SoundDir").resolve()

# ---------- duration helpers (cached) ----------
_DURATION_CACHE: dict[str, float] = {}

def _wav_seconds(filename: str, folder: Path = DEFAULT_SOUND_DIR) -> float | None:
    """Return duration (s) of a WAV in DEFAULT_SOUND_DIR. Caches results. None on error."""
    if filename in _DURATION_CACHE:
        return _DURATION_CACHE[filename]
    try:
        p = (folder / filename)
        with contextlib.closing(wave.open(str(p), "rb")) as wf:
            secs = wf.getnframes() / float(wf.getframerate())
            _DURATION_CACHE[filename] = secs
            return secs
    except Exception:
        # Missing/invalid → unknown; when close to target we conservatively exclude it
        return None

def run():
    log_event("[Graveyard] Starting...")

    threading.Thread(target=steeringWheel, daemon=True, name="Steering Wheel").start()

    while house.HouseActive or house.Demo:
        log_event("[Graveyard] Running loop...")

        m1Digital_Write(6, 0) # ship lights ON
        m1Digital_Write(7, 0)

        m1Digital_Write(8, 0) # deck ambient ON

        log_event("[graveyard] Ship Lights ON")
        log_event("[graveyard] Deck Ambient Lights ON")

        '''while True:  #SERVO TESTING ONLY
            try:
                angle = float(input("Enter servo angle (0–180, q to quit): "))
                rsm.servo("SERVO1", angle=angle, ramp_ms=3000)
            except ValueError:
                print("Exiting...")
                break'''

        '''while True:
            while not rsm.get_button_value("BTN2"):
                t.sleep(.05)
                if BreakCheck():
                    return
            lightning_bolt(threaded=False)'''
        
        '''while True:
            dimmer_flicker(
                duration=10,
                min_intensity=20,
                max_intensity=80,
                flicker_length_min=0.05,
                flicker_length_max=.18,
                threaded=True
            )
            t.sleep(11)'''
        
        '''while house.systemState == "ONLINE":
            play_audio("sprite 1 file 001")
            rsm.sprite_play("SPRITE1", 1)
            t.sleep(20)
            play_audio("sprite 1 file 002")
            rsm.sprite_play("SPRITE1", 2)
            t.sleep(20)

        if BreakCheck():
            return'''
        
        '''while True:
            m1Digital_Write(31,0)
            t.sleep(7)
            m1Digital_Write(31,1)
            t.sleep(7)
            if BreakCheck():
                return'''
        
        threading.Thread(target=randCannonsIdle, daemon=True, name="random cannons idle").start()
        #idleMusic(min_idle_time=random.randint(300,400)) #10-15 mins
        m1Digital_Write(8, 0) # deck ambient ON
        #idleMusic2()
        m1Digital_Write(8, 0) # deck ambient ON
        if BreakCheck():
            return
        m1Digital_Write(8, 0) # deck ambient ON
        MedallionCallsEvent()
        m1Digital_Write(8, 0) # deck ambient ON
        threading.Thread(target=randCannonsIdle, daemon=True, name="random cannons idle").start()
        #idleMusic(min_idle_time=210) #3.5 mins
        #idleMusic2()
        m1Digital_Write(8, 0) # deck ambient ON
        if BreakCheck():
            return

        BeckettsDeathEvent()

        if BreakCheck():
            return

        #testEvent()

        

        if BreakCheck() or house.Demo: # end on breakCheck or if demo'ing
            if house.Demo:
                house.Demo = False
                house.HouseActive = False
            toggleHouseLights(True)
            return

    log_event("[Graveyard] Exiting.")

def cannonsButton():
    while not rsm.get_button_value("BTN2"):
        t.sleep(.05)
        if BreakCheck():
            return
        
def waterBlast(duration=2, threaded=False):
    audio_files = [
        "splash1.wav",
        "splash2.wav",
        "splash3.wav"
    ]
    def main():
        audio = random.choice(audio_files)
        play_audio("graveyard", audio, gain=.7)
        t.sleep(.1)
        m1Digital_Write(40,0)
        t.sleep(duration)
        m1Digital_Write(40,1)

    if threaded:
        threading.Thread(target=main, daemon=True, name="water blast").start()
    else:
        main()

def idleMusic2():
    audio_files = [
        "piratesLifeForMe.wav",
        "DavyJones.wav",
        "DontThinkNowBestTime.wav",
        "FamilyAffair.wav",
        "GuiltyJackSparrow.wav",
        # highlighted additions
        "bloodRitual.wav",
        "bootstrapsBootstraps.wav",
        "moonlightSerenade.wav",
        "spanishSuite.wav",
        "theKraken.wav",
        "walkThePlank.wav",
        "hesaPirate.wav",
        "toThePiratesCave.wav"
    ]
    audio = random.choice(audio_files)
    play_audio("graveyard", audio, gain=1, threaded=False)

def idleMusic(min_idle_time: float = 0, buffer_seconds: int = 180):
    """
    Plays back-to-back songs until total >= min_idle_time (never mid-track).
    - Probes durations for all listed files in DEFAULT_SOUND_DIR (no hardcoding).
    - Near target (remaining <= buffer_seconds), only picks songs whose duration
      fits within remaining + buffer_seconds.
    - Never repeats the same song twice in a row.
    - If only viable pick equals last-played, choose the second-shortest song overall.
    - Non-threaded; loop exits cleanly on BreakCheck().
    """
    audio_files = [
        "piratesLifeForMe.wav",
        "DavyJones.wav",
        "DontThinkNowBestTime.wav",
        "FamilyAffair.wav",
        "GuiltyJackSparrow.wav",
        # highlighted additions
        "bloodRitual.wav",
        "bootstrapsBootstraps.wav",
        "moonlightSerenade.wav",
        "spanishSuite.wav",
        "theKraken.wav",
        "walkThePlank.wav",
        "hesaPirate.wav",
        "toThePiratesCave.wav"
    ]

    # Probe durations (best-effort) from DEFAULT_SOUND_DIR
    durations: dict[str, float | None] = {f: _wav_seconds(f, DEFAULT_SOUND_DIR) for f in audio_files}

    total_elapsed = 0.0
    last_played: str | None = None

    def _second_shortest(files: list[str]) -> str:
        """Pick second-shortest by known duration; unknowns sorted to end."""
        ranked = sorted(
            ((f, durations.get(f) if durations.get(f) is not None else float("inf")) for f in files),
            key=lambda x: (x[1], x[0]),
        )
        if len(ranked) >= 2:
            return ranked[1][0]
        return ranked[0][0]

    while (house.HouseActive or house.Demo) and not BreakCheck():
        remaining = max(0.0, float(min_idle_time) - total_elapsed)

        # When far from target → allow all. When close → only tracks that "fit".
        def allowed(f: str) -> bool:
            d = durations.get(f)
            if remaining > buffer_seconds:
                return True
            if d is None:
                # Unknown length when close → be conservative and exclude
                return False
            return d <= (remaining + buffer_seconds)

        candidates = [f for f in audio_files if allowed(f)]
        if not candidates:
            # If nothing "fits", fall back to full list to avoid stalling
            candidates = audio_files[:]

        # Avoid repeating the last track if we can
        non_repeat = [f for f in candidates if f != last_played]
        if non_repeat:
            pick_from = non_repeat
        else:
            # Only viable pick equals last_played → choose second-shortest overall
            pick_from = [_second_shortest(audio_files)]

        if BreakCheck():
            break

        audio = random.choice(pick_from)

        # Play one track (blocking). Your audio manager will stop audio if BreakCheck() flips elsewhere.
        start = t.time()
        play_audio("graveyard", audio, gain=0.4, threaded=False)
        elapsed = t.time() - start

        total_elapsed += elapsed
        last_played = audio

        log_event(
            f"[idleMusic] Played {audio} "
            f"(track: {elapsed:.2f}s, total: {total_elapsed:.2f}s, "
            f"remaining: {max(0.0, min_idle_time - total_elapsed):.2f}s)"
        )

        # Exit after finishing the current track if target reached or break requested
        if BreakCheck() or total_elapsed >= min_idle_time:
            break


def cannonsButton_loop():
    def cannonsButton(long_press_ms: int = 600, debounce_ms: int = 40):
        """
        Waits for BTN2 press+release, then:
        - short press  -> fire random cannon (1 or 2)
        - long press   -> fire cannon 1, wait 2s, fire cannon 2
        Returns after acting once (non-looping). Aborts early if BreakCheck() is True.
        """

        # Wait for a *new* press (rising edge)
        while True:
            if BreakCheck():
                return
            if rsm.get_button_value("BTN2"):          # pressed
                # measure hold duration until release
                t0 = t.time()
                while rsm.get_button_value("BTN2"):
                    if BreakCheck():
                        return
                    t.sleep(0.01)

                # basic release debounce
                t.sleep(debounce_ms / 1000.0)

                held_s = t.time() - t0
                if held_s >= (long_press_ms / 1000.0):
                    # LONG PRESS: fire 1, then 2 after 2s
                    cannons.fire_cannon(1)

                    # Wait 2 seconds unless BreakCheck trips
                    end_wait = t.time() + 2.0
                    while t.time() < end_wait:
                        if BreakCheck():
                            return
                        t.sleep(0.05)

                    cannons.fire_cannon(2)
                else:
                    # SHORT PRESS: fire a single random cannon
                    cannons.fire_cannon(random.choice([1, 2]))

                return  # done after one action

            # idle polling
            t.sleep(0.01)

    while not BreakCheck():
        if not Scripted_Event:
            cannonsButton()  # handles one press + action
            t.sleep(0.05)
        else:
            t.sleep(5)
    

def idleEvent():
    while house.HouseActive or house.Demo:
        cannons.fire_cannon(1)

        for i in range(random.randint(3, 10)):
            t.sleep(1)
            if BreakCheck():
                return

        cannons.fire_cannon(2)

        for i in range(random.randint(30, 60)):
            t.sleep(1)
            if BreakCheck():
                return

def BeckettsDeathEvent():
    global Scripted_Event 
    Scripted_Event = True

    dim(100)
    t.sleep(1)
    dim(0)

    m1Digital_Write(6, 0) # ship lights ON
    log_event("[graveyard] Ship Lights ON")
    m1Digital_Write(7, 0)
    log_event("[graveyard] Ship Lights ON")

    m1Digital_Write(8, 0) # deck ambient ON
    log_event("[graveyard] Deck Ambient Lights ON")
    
    log_event("[Graveyard] Beckett's Death Event Starting...")
    play_audio("graveyard", "GraveyardScene2v3part1.wav", gain=1, threaded=True)
    
    for i in range(58):
        t.sleep(1)
        if BreakCheck():
            return
        
    cannons.fire_cannon(3)
    
    for i in range(2):
        t.sleep(1)
        if BreakCheck():
            return
        
    play_audio("graveyard", "waterWave01.wav", gain=.7)

    t.sleep(.8)
    waterBlast(duration=2, threaded=True)
    t.sleep(.2)

    for i in range(5):
        t.sleep(1)
        if BreakCheck():
            return

    cannons.fire_cannon(1)
    t.sleep(1)
    cannons.fire_cannon(2)

    for i in range(7):
        t.sleep(1)
        if BreakCheck():
            return
    
    threading.Thread(target=randCannons, daemon=True, name="rand cannons initiator").start() #just ship cannons
    cannons.fire_cannon(3)

    for i in range(2):
        t.sleep(1)
        if BreakCheck():
            return
        
    t.sleep(.8)
    waterBlast(duration=2, threaded=True)
        
    for i in range(17):
        t.sleep(1)
        if BreakCheck():
            return
        
    t.sleep(.2)
    
    cannons.fire_cannon(3)

    for i in range(2):
        t.sleep(1)
        if BreakCheck():
            return

    play_audio("graveyard", "waterWave02.wav", gain=.7)
    t.sleep(.8)
    m1Digital_Write(59,0) #smoke machine
    log_event("[graveyard] Smoke Machine ON")
    flickerAmbientLights(12, threaded=True)
    play_audio("graveyard", "impactDebris02.wav", gain=.5)

    for i in range(24):
        t.sleep(1)
        if BreakCheck():
            return
        
    t.sleep(.8)
        
    cannons.fire_cannon(3)

    rsm.sprite_play("SPRITE1", 1) #fire start

    dimmer_flicker(104, 20, 80, 0.05, 0.18, True)  # fire lights flicker

    for i in range(2):
        t.sleep(1)
        if BreakCheck():
            return
    
    play_audio("graveyard", "waterWave02.wav", gain=.7)

    t.sleep(.8)
    waterBlast(duration=2, threaded=True)
        
    for i in range(24):
        t.sleep(1)
        if BreakCheck():
            return
        
    t.sleep(.2)
    
    cannons.fire_cannon(3)

    for i in range(2):
        t.sleep(1)
        if BreakCheck():
            return

    play_audio("graveyard", "waterWave01.wav", gain=.7)
    t.sleep(.8)
    m1Digital_Write(59,0) #smoke machine
    log_event("[graveyard] Smoke Machine ON")
    flickerAmbientLights(12, threaded=True)
    play_audio("graveyard", "impactDebris01.wav", gain=.5)

    m1Digital_Write(43, 0) # mast
    t.sleep(.4)
    m1Digital_Write(43, 1) # mast
    t.sleep(.4)
    m1Digital_Write(43, 0) # mast
    t.sleep(.3)
    m1Digital_Write(43, 1) # mast
    t.sleep(.4)
    m1Digital_Write(43, 0) # mast
    t.sleep(.2)
    m1Digital_Write(43, 1) # mast

    for i in range(23):
        t.sleep(1)
        if BreakCheck():
            return
    
    #sword fight starts

    lightning_bolt(threaded=True)
    flickerAmbientLights(5, threaded=True)
    flashingShipLights(52, .5, threaded=True)

    for i in range(10):
        t.sleep(1)
        if BreakCheck():
            return
        
    lightning_bolt(threaded=True)
    flickerAmbientLights(5, threaded=True)

    for i in range(5):
        t.sleep(1)
        if BreakCheck():
            return
        
    cannons.fire_cannon(3)

    for i in range(2):
        t.sleep(1)
        if BreakCheck():
            return
        
    play_audio("graveyard", "waterWave01.wav", gain=.7)
    t.sleep(.8)

    m1Digital_Write(8, 1) # deck ambient
    log_event("[graveyard] Deck Ambient Lights OFF")
    dimmer_flicker(31, 20, 100, 0.05, 0.18, True)  # fire lights flicker
    fireLightsSmoke(1, threaded=True) 
    flickerAmbientLights(12, threaded=True)
    play_audio("graveyard", "impactDebris01.wav", gain=.5)
    m1Digital_Write(43, 0) #mast

    t.sleep(.2)

    for i in range(15):
        t.sleep(1)
        if BreakCheck():
            return
        
    lightning_bolt(threaded=True)
    flickerAmbientLights(5, threaded=True)
        
    for i in range(15):
        t.sleep(1)
        if BreakCheck():
            return
    
    #fireLightsSmoke(1, threaded=True)

    for i in range(3):
        t.sleep(1)
        if BreakCheck():
            return
        
    rsm.sprite_play("SPRITE1", 2) #fire end
        
    dim(0)
    
    m1Digital_Write(32, 0) #deck strobe
    log_event("[graveyard] Deck Strobe ON")
    m1Digital_Write(8, 1) # deck ambient
    log_event("[graveyard] Deck Ambient Lights OFF")
    m1Digital_Write(6, 1) # ship lights
    log_event("[graveyard] Ship Lights OFF")
    m1Digital_Write(7, 1)
    log_event("[graveyard] Ship Lights OFF")

    Scripted_Event = False

    m1Digital_Write(59, 0) #smoke machine
    log_event("[graveyard] Smoke Machine ON")

    log_event("GraveyardScene2v3part2 STARTED")

    play_audio("graveyard", "GraveyardScene2v3part2.wav", gain=.4, threaded=False)

    log_event("GraveyardScene2v3part2 ENDED")
    m1Digital_Write(59, 1) #smoke machine
    log_event("[graveyard] Smoke Machine OFF")

    m1Digital_Write(32, 1) #deck strobe
    log_event("[graveyard] Deck Strobe OFF")
        
    while not rsm.get_button_value("BTN2"):
        t.sleep(.05)
        if BreakCheck():
            return
    
    play_audio("graveyard", "OneLastShotEdited.wav", gain=.6)

    for i in range(7):
        t.sleep(1)
        if BreakCheck():
            return
    t.sleep(.5)

    flashingShipLights(7, .4, threaded=True)
    ambientLightsFireLightsSeq(10, .5, threaded=True)

    m1Digital_Write(43, 1) # mast

    m1Digital_Write(8, 0) # deck ambient ON
    log_event("[graveyard] Deck Ambient Lights ON")

    for i in range(8):
        t.sleep(1)
        if BreakCheck():
            return
        
    m1Digital_Write(6, 0) # ship lights ON
    log_event("[graveyard] Ship Lights ON")
    m1Digital_Write(7, 0)
    log_event("[graveyard] Ship Lights ON")

    for i in range(82):
        t.sleep(1)
        if BreakCheck():
            return
        
    log_event("[Graveyard] Beckett's Death Event Ending...")
    
    
def MedallionCallsEvent():
    global Scripted_Event 
    Scripted_Event = True
    
    log_event("[Graveyard] Medallion Calls Event Starting...")

    dim(100)
    t.sleep(1)
    dim(0)
    
    play_audio("graveyard", "TheMedallionCalls.wav", gain=1)
        
    for i in range(17):
        t.sleep(1)
        if BreakCheck():
            return
            
    threading.Thread(target=randAttackerCannons, daemon=True, name="randAttackerCannons").start()

    for i in range(1):  # 22
        t.sleep(1)
        if BreakCheck():
            return

    cannons.fire_cannon(3)
        
    for i in range(2):  # 22
        t.sleep(1)
        if BreakCheck():
            return
            
    play_audio("graveyard", "waterWave01.wav", gain=.7)
    t.sleep(.8)
    m1Digital_Write(59,0) #smoke machine
    log_event("[graveyard] Smoke Machine ON")
    flickerAmbientLights(12, threaded=True)
    m1Digital_Write(43, 0) # mast
    play_audio("graveyard", "impactDebris01.wav", gain=.5)
        
    for i in range(4):  # 28.8
        t.sleep(1)
        if BreakCheck():
            return
        
    m1Digital_Write(59,1) #smoke machine
    log_event("[graveyard] Smoke Machine OFF")

    for i in range(4):  # 28.8
        t.sleep(1)
        if BreakCheck():
            return
        
    cannons.fire_cannon(1)
    for i in range(5):  # 33.8
        t.sleep(1)
        if BreakCheck():
            return
    cannons.fire_cannon(2)

    for i in range(4):  # 42
        t.sleep(1)
        if BreakCheck():
            return

    cannons.fire_cannon(3)
    
    t.sleep(.2)
    for i in range(2):  # 42
        t.sleep(1)
        if BreakCheck():
            return
    
    play_audio("graveyard", "waterWave01.wav", gain=.7)

    t.sleep(.8)
    waterBlast(2, threaded=True)

    for i in range(1):  # 42
        t.sleep(1)
        if BreakCheck():
            return
    t.sleep(.2)
    
    flickerAmbientLights(4, threaded=True)
    play_audio("graveyard", "waterWave02.wav", gain=1)
    t.sleep(.6)
    play_audio("graveyard", "impactDebris04.wav", gain=.5)
    flickerAmbientLights(6, threaded=False)
    m1Digital_Write(8, 1) # ambient OFF
    log_event("[graveyard] Deck Ambient Lights OFF")
    
    t.sleep(.2)

    fireLightsSmoke(2, threaded=True) 

    rsm.sprite_play("SPRITE1", 1) #fire start

    dimmer_flicker(6, 20, 100, 0.05, 0.18, True)  # fire lights flicker
    for i in range(7):  # 49
        t.sleep(1)
        if BreakCheck():
            return
        
    dimmer_flicker(58, 20, 100, 0.05, 0.18, True)  # fire lights flicker
        
    cannons.fire_cannon(1)
    for i in range(6):  # 55
        t.sleep(1)
        if BreakCheck():
            return
    cannons.fire_cannon(2)

    fireLightsSmoke(1, threaded=True) 
    
    for i in range(4):  # 60
        t.sleep(1)
        if BreakCheck():
            return
        
    cannons.fire_cannon(3)

    for i in range(1):  # 60
        t.sleep(1)
        if BreakCheck():
            return
        
    fireLightsSmoke(2, threaded=True) 
    play_audio("graveyard", "waterWave01.wav", gain=1)
    
    for i in range(1):  # 65
        t.sleep(1)
        if BreakCheck():
            return
        
    waterBlast(duration=2, threaded=True)
        
    for i in range(4):  # 65
        t.sleep(1)
        if BreakCheck():
            return

    cannons.fire_cannon(2)
    for i in range(4):  # 69
        t.sleep(1)
        if BreakCheck():
            return
        
    flashingShipLights(20, .5, threaded=True)

    cannons.fire_cannon(1)
    
    for i in range(8):  # 77
        t.sleep(1)
        if BreakCheck():
            return

    play_audio("graveyard", "waterWave03.wav", gain=1)
    t.sleep(.5)
    play_audio("graveyard", "impactDebris03.wav", gain=.5)
    flickerAmbientLights(6, threaded=False)
    m1Digital_Write(8, 1) # ambient OFF
    log_event("[graveyard] Deck Ambient Lights OFF")
    
    for i in range(10):  # 87
        t.sleep(1)
        if BreakCheck():
            return
        
    cannons.fire_cannon(1)
    for i in range(4):  # 91
        t.sleep(1)
        if BreakCheck():
            return
    cannons.fire_cannon(2)
        
    for i in range(16):
        t.sleep(1)
        if BreakCheck():
            return
        
    rsm.sprite_play("SPRITE1", 2) #fire end
        
    Scripted_Event = False
        
    while not rsm.get_button_value("BTN2"):
        t.sleep(.05)
        if BreakCheck():
            return
    
    play_audio("graveyard", "OneLastShotEdited.wav", gain=1)

    for i in range(7):
        t.sleep(1)
        if BreakCheck():
            return
    t.sleep(.5)

    flashingShipLights(7, .4, threaded=True)
    ambientLightsFireLightsSeq(10, .5, threaded=True)

    m1Digital_Write(43, 1) # mast

    m1Digital_Write(8, 0) # deck ambient ON
    log_event("[graveyard] Deck Ambient Lights ON")

    for i in range(8):
        t.sleep(1)
        if BreakCheck():
            return
        
    m1Digital_Write(6, 0) # ship lights ON
    log_event("[graveyard] Ship Lights ON")
    m1Digital_Write(7, 0)
    log_event("[graveyard] Ship Lights ON")

    for i in range(82):
        t.sleep(1)
        if BreakCheck():
            return
        
    log_event("[Graveyard] Medallion Calls Event Ending...")

def ambientLightsFireLightsSeq(loops, speed, threaded=False):
    def main():
        for i in range(loops):
            dim(100)
            m1Digital_Write(8, 1) # deck ambient
            t.sleep(speed)
            dim(0)
            m1Digital_Write(8, 0) # deck ambient
            t.sleep(speed)

    if threaded:
        threading.Thread(target=main, daemon=True, name="ambient and fire lights seq").start()
    else:
        main()

def fireLightsSmoke(loops, threaded=False):
    def main():
        log_event(f"[graveyard] Enabling fire lights smoke for {loops} loops.")
        for i in range(loops):
            for i in range(3):
                m1Digital_Write(59,0) #smoke machine
                t.sleep(.3)
                m1Digital_Write(59,1) #smoke machine
                t.sleep(.3)
            t.sleep(1)
            m1Digital_Write(59,0) #smoke machine
            t.sleep(1)
            m1Digital_Write(59,1) #smoke machine
            t.sleep(1)
            m1Digital_Write(59,0) #smoke machine
            t.sleep(2)
            m1Digital_Write(59,1) #smoke machine
            if BreakCheck():
                return
    
    if threaded:
        threading.Thread(target=main, daemon=True, name="fire lights smoke").start()
    else:
        main()

def flashingShipLights(duration, delay_s, threaded=False):
    def main():
        log_event(f"[Graveyard] Flashing Ship Lights for {duration} seconds")
        end_time = t.time() + duration
        while t.time() < end_time and house.HouseActive:
            m1Digital_Write(6, 1) # ship lights
            m1Digital_Write(7, 0)
            t.sleep(delay_s - 0.3)
            m1Digital_Write(6, 0) # ship lights
            t.sleep(0.3)
            m1Digital_Write(7, 1)
            t.sleep(delay_s)
            if BreakCheck():    
                return
        m1Digital_Write(6, 0) # ship lights ON
        m1Digital_Write(7, 0)

    if threaded:
        threading.Thread(target=main, daemon=True, name="Ship Light Flasher").start()
    else:
        main()

def flickerAmbientLights(loops, threaded=False):
    def main():
        log_event(f"[Graveyard] Flickering Ambient Lights {loops} times")
        for i in range(loops):
            m1Digital_Write(8, 1) # deck ambient OFF
            t.sleep(random.uniform(.05, .12))
            m1Digital_Write(8, 0) # deck ambient ON
            t.sleep(random.uniform(.05, .12))
            if BreakCheck():    
                return

    if threaded:
        threading.Thread(target=main, daemon=True, name="Graveyard Ambient Flicker").start()
    else:
        main()

def steeringWheel():
    while house.HouseActive or house.Demo:
        log_event("[gravyard] Running steering wheel...")
        rsm.servo("SERVO1",angle=0,ramp_ms=3000)
        t.sleep(4)
        if BreakCheck():
            return
        rsm.servo("SERVO1",angle=160,ramp_ms=3000)
        t.sleep(4)
        if BreakCheck():    
            return

def randAttackerCannons():
    audioFiles = [
        "CannonFireLow01.wav",
        "CannonFireLow02.wav",
        "CannonFireLow04.wav"
    ]

    log_event("[graveyard] Starting random attacker cannons loop...")
    while Scripted_Event and house.HouseActive:
        audio = random.choice(audioFiles)
        play_audio("graveyard", audio, gain=.2)
        t.sleep(random.uniform(.2, 5))
        

def randCannons():
    while Scripted_Event and house.HouseActive:
        if BreakCheck():
            return
        cannons.fire_cannon(random.randint(1,2))
        t.sleep(random.uniform(20, 30))

def randCannonsIdle():
    while not Scripted_Event and house.HouseActive:
        
        for i in range(random.randint(60, 100)):
            t.sleep(1)
            if BreakCheck() or Scripted_Event:
                return

        cannons.fire_cannon(1)

        for i in range(random.randint(60, 100)):
            t.sleep(1)
            if BreakCheck() or Scripted_Event:
                return

        cannons.fire_cannon(2)

def testEvent():
    global Scripted_Event 
    Scripted_Event = True
    
    log_event("[Graveyard] Test Event Starting...")

    t.sleep(1)

    for i in range(5):
        t.sleep(1)
        if BreakCheck():
            return
        
    m1Digital_Write(32, 1)  # Deck strobe
    log_event("[graveyard] Deck Strobe OFF")
    m1Digital_Write(29, 1)  # Deck lightning
    log_event("[graveyard] Deck Lightning OFF")

    for i in range(10):
        t.sleep(1)
        if BreakCheck():
            return
        
    threading.Thread(target=lightning_bolt, daemon=True, name="GY Lightning Bolt").start()
        
    dimmer_flicker(     # ambient lights flicker
        channel=7,
        duration_s=2, 
        intensity_min=0, 
        intensity_max=40, 
        flicker_length_min=0.01, 
        flicker_length_max=0.08
    )
    
    for i in range(2):
        t.sleep(1)
        if BreakCheck():
            return
        
    dimmer_flicker(
        channel=7,
        duration_s=5, 
        intensity_min=45, 
        intensity_max=70, 
        flicker_length_min=0.1, 
        flicker_length_max=0.5
    )

    for i in range(5):
        t.sleep(1)
        if BreakCheck():
            return

    threading.Thread(target=lightning_bolt, daemon=True, name="GY Lightning Bolt").start()

    dimmer_flicker(     # ambient lights flicker
        channel=7,
        duration_s=2, 
        intensity_min=0, 
        intensity_max=40, 
        flicker_length_min=0.01, 
        flicker_length_max=0.08
    )

    for i in range(2):
        t.sleep(1)
        if BreakCheck():
            return
        
    dimmer_flicker(
        channel=7,
        duration_s=6, 
        intensity_min=45, 
        intensity_max=70, 
        flicker_length_min=0.2, 
        flicker_length_max=0.5
    )

    dimmer_flicker(     # fire lights flicker
        channel=2,
        duration_s=6, 
        intensity_min=0, 
        intensity_max=20, 
        flicker_length_min=0.09, 
        flicker_length_max=0.3
    )

    for i in range(6):
        t.sleep(1)
        if BreakCheck():
            return

    dimmer_flicker(     # fire lights flicker
        channel=2,
        duration_s=3, 
        intensity_min=15, 
        intensity_max=30, 
        flicker_length_min=0.09, 
        flicker_length_max=0.3
    )

    for i in range(3):
        t.sleep(1)
        if BreakCheck():
            return

    dimmer_flicker(     # fire lights flicker
        channel=2,
        duration_s=3, 
        intensity_min=30, 
        intensity_max=45, 
        flicker_length_min=0.09, 
        flicker_length_max=0.3
    )

    for i in range(3):
        t.sleep(1)
        if BreakCheck():
            return

    dimmer_flicker(     # fire lights flicker
        channel=2,
        duration_s=60, 
        intensity_min=30, 
        intensity_max=50, 
        flicker_length_min=0.09, 
        flicker_length_max=0.3
    )
    
    for i in range(60):
        t.sleep(1)
        if BreakCheck():
            return
        
    log_event("[Graveyard] Test Event Ending...")
    Scripted_Event = False

def lightning_bolt(threaded=False):

    audioFiles = [
        "thunder1.wav",
        "thunder2.wav",
        "thunder3.wav",
        "thunder4.wav"
    ]

    def main():
        log_event("[Graveyard] Lightning Bolt Triggered")

        audio =  random.choice(audioFiles)

        play_audio("graveyard", audio, gain=.8)

        m1Digital_Write(32, 0)  # Deck strobe

        m1Digital_Write(29, 0)  # Deck lightning ON
        t.sleep(0.1)
        m1Digital_Write(29, 1)  # Deck lightning OFF
        t.sleep(1.1)
        m1Digital_Write(29, 0)  # Deck lightning ON
        t.sleep(0.07)
        m1Digital_Write(29, 1)  # Deck lightning OFF
        t.sleep(.5)
        m1Digital_Write(29, 0)  # Deck lightning ON
        t.sleep(0.07)
        m1Digital_Write(29, 1)  # Deck lightning OFF
        t.sleep(.5)
        m1Digital_Write(29, 0)  # Deck lightning ON
        t.sleep(0.07)
        m1Digital_Write(29, 1)  # Deck lightning OFF

        m1Digital_Write(32, 1)  # Deck strobe
    
    if threaded:
        threading.Thread(target=main, daemon=True, name="lightning bolt").start()
    else:
        main()
