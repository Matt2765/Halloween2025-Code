# rooms/graveyard.py
import time as t
from context import house
from control.audio_manager import play_audio
from utils.tools import BreakCheck, log_event
import random
import threading
from control.dimmer_controller import dim, dimmer_flicker
from control.arduino import m1Digital_Write
from control import cannons

Scripted_Event = False

def run():
    log_event("[Graveyard] Starting...")

    while house.HouseActive or house.Demo:
        log_event("[Graveyard] Running loop...")

        #testEvent()

        #idleEvent()

        MedallionCallsEvent()
        for i in range(3):
            t.sleep(1)
            if BreakCheck():
                return        
            
        #BeckettsDeathEvent()
        
        for i in range(30):
            t.sleep(1)
            if BreakCheck():
                return

        if BreakCheck() or house.Demo:  # end on breakCheck or if demo'ing
            house.Demo = False
            break

    log_event("[Graveyard] Exiting.")
    
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
    
    log_event("[Graveyard] Beckett's Death Event Starting...")
    play_audio("graveyard", "GraveyardScene2v2.wav", gain=.1)
    
    for i in range(58):
        t.sleep(1)
        if BreakCheck():
            return
        
    play_audio("graveyard", "CannonDesigned_2.wav", gain=1)
    
    for i in range(5):
        t.sleep(1)
        if BreakCheck():
            return
    
    threading.Thread(target=randCannons, daemon=True).start()
    
    for i in range(288):
        t.sleep(1)
        if BreakCheck():
            return
        
    log_event("[Graveyard] Beckett's Death Event Ending...")
    Scripted_Event = False
    
    
def MedallionCallsEvent():
    global Scripted_Event 
    Scripted_Event = True
    
    log_event("[Graveyard] Medallion Calls Event Starting...")
    
    play_audio("graveyard", "TheMedallionCalls.wav", gain=.2)
        
    for i in range(17):
        t.sleep(1)
        if BreakCheck():
            return
            
    threading.Thread(target=randAttackerCannons, daemon=True).start()

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
    play_audio("graveyard", "impactDebris01.wav", gain=.7)
        
    for i in range(8):  # 28.8
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
    for i in range(4):  # 42
        t.sleep(1)
        if BreakCheck():
            return
        
    play_audio("graveyard", "waterWave02.wav", gain=1)
    t.sleep(.6)
    play_audio("graveyard", "impactDebris04.wav", gain=1)
    
    t.sleep(.2)
    for i in range(7):  # 49
        t.sleep(1)
        if BreakCheck():
            return
        
    cannons.fire_cannon(1)
    for i in range(6):  # 55
        t.sleep(1)
        if BreakCheck():
            return
    cannons.fire_cannon(2)
    
    for i in range(4):  # 60
        t.sleep(1)
        if BreakCheck():
            return
        
    cannons.fire_cannon(3)

    for i in range(1):  # 60
        t.sleep(1)
        if BreakCheck():
            return
        
    play_audio("graveyard", "waterWave01.wav", gain=1)
    
    for i in range(5):  # 65
        t.sleep(1)
        if BreakCheck():
            return

    cannons.fire_cannon(2)
    for i in range(4):  # 69
        t.sleep(1)
        if BreakCheck():
            return
    cannons.fire_cannon(1)
    
    for i in range(8):  # 77
        t.sleep(1)
        if BreakCheck():
            return
        
    play_audio("graveyard", "waterWave03.wav", gain=1)
    t.sleep(.5)
    play_audio("graveyard", "impactDebris03.wav", gain=1)
    
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
        
    log_event("[Graveyard] Medallion Calls Event Ending...")
    Scripted_Event = False


def randAttackerCannons():
    audioFiles = [
        "CannonFireLow01.wav",
        "CannonFireLow02.wav",
        "CannonFireLow04.wav"
    ]
    while Scripted_Event and house.HouseActive:
        audio = random.choice(audioFiles)
        play_audio("graveyard", audio, gain=.2)
        t.sleep(random.uniform(.2, 5))
        

def randCannons():
    audioFiles = [
        "CannonDesigned_1.wav",
        "CannonDesigned_2.wav",
        "CannonDesigned_3.wav",
        "CannonDesigned_4.wav"
    ]
    while Scripted_Event and house.HouseActive:
        audio = random.choice(audioFiles)
        play_audio("graveyard", audio, gain=1)
        t.sleep(random.uniform(10, 20))


def testEvent():
    global Scripted_Event 
    Scripted_Event = True
    
    log_event("[Graveyard] Test Event Starting...")

    t.sleep(1)

    dimmer_flicker(     # ambient lights flicker
        channel=1,
        duration_s=10, 
        intensity_min=45, 
        intensity_max=70, 
        flicker_length_min=0.1, 
        flicker_length_max=0.5
    )

    dimmer_flicker(     # ambient lights flicker
        channel=2,
        duration_s=10, 
        intensity_min=45, 
        intensity_max=70, 
        flicker_length_min=0.1, 
        flicker_length_max=0.5
    )

    '''dimmer_flicker(     # ambient lights flicker
        channel=2,
        duration_s=10, 
        intensity_min=45, 
        intensity_max=70, 
        flicker_length_min=0.1, 
        flicker_length_max=0.5
    )'''

    #t.sleep(10)

    '''dimmer(1, 35)  # Treasure room lights (remove later)
    t.sleep(2)
    dimmer(2, 100)  # Fire lights
    t.sleep(2)
    #m1Digital_Write(32, 0)  # Deck strobe
    #m1Digital_Write(29, 0)  # Deck lightning
    dimmer(7, 100)  # Ambient lights
    t.sleep(2)
    dimmer(1, 100)  # Treasure room lights (remove later)
    t.sleep(2)
    dimmer(1, 50)  # Treasure room lights (remove later)

    t.sleep(2)
    dimmer(2, 75)  # Fire lights
    t.sleep(2)
    #m1Digital_Write(32, 0)  # Deck strobe
    #m1Digital_Write(29, 0)  # Deck lightning
    dimmer(7, 50)  # Ambient lights
    t.sleep(2)
    while True:
        dimmer(1, 100)  # Treasure room lights (remove later)
        t.sleep(2)
        dimmer(1, 75)  # Treasure room lights (remove later)
        t.sleep(2)
        dimmer(1, 70)  # Treasure room lights (remove later)
        t.sleep(2)
        dimmer(1, 67)  # Treasure room lights (remove later)
        t.sleep(2)'''

    return


    for i in range(5):
        t.sleep(1)
        if BreakCheck():
            return
        
    dimmer(2, 0)  # Fire lights
    m1Digital_Write(32, 1)  # Deck strobe
    m1Digital_Write(29, 1)  # Deck lightning
    dimmer(7, 0)  # Ambient lights
        
    dimmer_flicker(     # ambient lights flicker
        channel=7,
        duration_s=10, 
        intensity_min=45, 
        intensity_max=70, 
        flicker_length_min=0.1, 
        flicker_length_max=0.5
    )

    for i in range(10):
        t.sleep(1)
        if BreakCheck():
            return
        
    threading.Thread(target=lightning_bolt, daemon=True).start()
        
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

    threading.Thread(target=lightning_bolt, daemon=True).start()

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
        
    dimmer(7,0)  # Ambient lights OFF

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

def lightning_bolt():
    log_event("[Graveyard] Lightning Bolt Triggered")

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