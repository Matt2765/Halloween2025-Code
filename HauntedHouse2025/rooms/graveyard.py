# rooms/graveyard.py
import time as t
from context import house
from control.audio_manager import play_to_named_channel_async
from utils.tools import BreakCheck, log_event
import random
import threading

Scripted_Event = False

def run():
    log_event("[Graveyard] Starting...")

    while house.HouseActive or house.Demo:
        log_event("[Graveyard] Running loop...")
        MedallionCallsEvent()
        BeckettsDeathEvent()
        
        for i in range(30):
            t.sleep(1)
            if BreakCheck():
                return

        if BreakCheck() or house.Demo: # end on breakCheck or if demo'ing
            house.Demo = False
            break

    log_event("[Graveyard] Exiting.")
    
def BeckettsDeathEvent():
    global Scripted_Event 
    Scripted_Event = True
    
    log_event("[Graveyard] Beckett's Death Event Starting...")
    play_to_named_channel_async("GraveyardScene2v2.wav", "graveyard", gain_override=.6)
    
    for i in range(58):
        t.sleep(1)
        if BreakCheck():
            return
        
    play_to_named_channel_async("CannonDesigned_2.wav", "graveyard", gain_override=1.2)
    
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
    
    play_to_named_channel_async("TheMedallionCalls.wav", "graveyard", gain_override=.6)
        
    for i in range(17):
        t.sleep(1)
        if BreakCheck():
            return
            
    threading.Thread(target=randAttackerCannons, daemon=True).start()
        
    for i in range(3): #22
        t.sleep(1)
        if BreakCheck():
            return
        
    play_to_named_channel_async("waterWave01.wav", "graveyard", gain_override=1)
    t.sleep(.8)
    play_to_named_channel_async("impactDebris01.wav", "graveyard", gain_override=1)
        
    for i in range(8): #28.8
        t.sleep(1)
        if BreakCheck():
            return
        
    play_to_named_channel_async("CannonDesigned_1.wav", "graveyard", gain_override=1.2)
    for i in range(5): #33.8
        t.sleep(1)
        if BreakCheck():
            return
    play_to_named_channel_async("CannonDesigned_4.wav", "graveyard", gain_override=1.2)
    
    t.sleep(.2)
    for i in range(8): #42
        t.sleep(1)
        if BreakCheck():
            return
        
    play_to_named_channel_async("waterWave02.wav", "graveyard", gain_override=1)
    t.sleep(.6)
    play_to_named_channel_async("impactDebris04.wav", "graveyard", gain_override=1)
    
    t.sleep(.2)
    for i in range(7): #49
        t.sleep(1)
        if BreakCheck():
            return
        
    play_to_named_channel_async("CannonDesigned_2.wav", "graveyard", gain_override=1.2)
    for i in range(6): #55
        t.sleep(1)
        if BreakCheck():
            return
    play_to_named_channel_async("CannonDesigned_3.wav", "graveyard", gain_override=1.2)
    
    for i in range(5): #60
        t.sleep(1)
        if BreakCheck():
            return
        
    play_to_named_channel_async("waterWave01.wav", "graveyard", gain_override=1)
    
    for i in range(5): #65
        t.sleep(1)
        if BreakCheck():
            return

    play_to_named_channel_async("CannonDesigned_4.wav", "graveyard", gain_override=1.2)
    for i in range(4): #69
        t.sleep(1)
        if BreakCheck():
            return
    play_to_named_channel_async("CannonDesigned_1.wav", "graveyard", gain_override=1.2)
    
    for i in range(8): #77
        t.sleep(1)
        if BreakCheck():
            return
        
    play_to_named_channel_async("waterWave03.wav", "graveyard", gain_override=1)
    t.sleep(.5)
    play_to_named_channel_async("impactDebris03.wav", "graveyard", gain_override=1)
    
    for i in range(10): #87
        t.sleep(1)
        if BreakCheck():
            return
        
    play_to_named_channel_async("CannonDesigned_2.wav", "graveyard", gain_override=1.2)
    for i in range(4): #91
        t.sleep(1)
        if BreakCheck():
            return
    play_to_named_channel_async("CannonDesigned_4.wav", "graveyard", gain_override=1.2)
        
    #play_to_named_channel_async("CannonDesigned_1.wav", "graveyard")
    for i in range(16):
        t.sleep(1)
        if BreakCheck():
            return
        
    log_event("[Graveyard] Medallion Calls Event Ending...")
        
    Scripted_Event = False

def randAttackerCannons():
    audioFiles = ["CannonFireLow01.wav", 
                  "CannonFireLow02.wav", 
                  "CannonFireLow04.wav"]
    while Scripted_Event and house.HouseActive:
        audio = random.choice(audioFiles)
        play_to_named_channel_async(f"{audio}", "graveyard", gain_override=.2)
        t.sleep(random.uniform(.2, 5))
        
def randCannons():
    audioFiles = ["CannonDesigned_1.wav", 
                  "CannonDesigned_2.wav",
                  "CannonDesigned_3.wav", 
                  "CannonDesigned_4.wav"]
    while Scripted_Event and house.HouseActive:
        audio = random.choice(audioFiles)
        play_to_named_channel_async(f"{audio}", "graveyard", gain_override=1.2)
        t.sleep(random.uniform(10, 20))
