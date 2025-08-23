import random
import sys
import time as t
import threading
import _thread
from http.server import HTTPServer, BaseHTTPRequestHandler
import tkinter as tk
from pymata4 import pymata4
from pydub import AudioSegment
from pydub.playback import play
import multiprocessing

# NOTES:
# -MUST EVALUATE BREAK CHECK EXTREMELY FREQUENTLY IN LOOPS, AS THIS LISTENS FOR HOUSE STOP COMMANDS. THAT BEING SAID, LONG SLEEP FUNCTIONS SHOULD BE WRITTEN WITH A FOR LOOP


def main():
    def init():
        global HouseActive  # WHEN TRUE HOUSE IS FULLY OPERATIONAL
        global CRstate
        global MRstate
        global MkRstate
        global SRstate
        global systemState  # IF ONLINE, HOUSE IS READY TO BE STARTED, IF SoftShutdown or EmergencyShutoff, HOUSE IS NOT READY
        global Demo  # TRUE IF DEMO RUNNING
        global SOUND  # TRUE IF SOUND ENABLED (DISABLE FOR DEBUGGING)
        global testing  # TRUE IF ITERATING THROUGH ALL HOUSE COMPONENTS
        global houseLights
        # TRUE IF ALREADY AN INSTANCE OF FRAME RESET FUNCTION RUNNING, PREVENTS REDUNDANT THREADS
        global FRthreadRunning
        global playMixer  # PASS PLAYER ID TO PLAY AUDIO
        global M2AnalogValues

        M2AnalogValues = [0] * 16
        DEBUG_INFO = False  # Enable if debugging
        Demo = False
        testing = False
        houseLights = False
        HouseActive = False
        FRthreadRunning = False
        SOUND = True
        CRstate = "INACTIVE"
        MRstate = "INACTIVE"
        SRstate = "INACTIVE"
        MkRstate = "INACTIVE"

        playMixer = multiprocessing.Queue()
        multiprocessing.Process(target=audioMixer, args=(playMixer,)).start()

        connectArduino()
        shutdown()

        threading.Thread(target=HTTP_SERVER).start()  # START LAN HTTP SERVER
        threading.Thread(target=MainGUI).start()  # START GUI
        # threading.Thread(target=AnalogUpdate).start()

        t.sleep(.2)

        if DEBUG_INFO:
            # ONLY ENABLED WHEN DEBUGGING
            threading.Thread(target=debugDoors).start()
            threading.Thread(target=debugSensors).start()

        systemState = "ONLINE"
        threading.Thread(target=shutdownDetector).start()

    def childMain():
        global HouseActive

        print("Preparing house systems...")
        HouseActive = False
        spawnDoors()
        threading.Thread(target=canLights).start()
        t.sleep(1)
        m2Digital_Write(48, 1)
        print("Audio mixer ON")
        print("House systems online. Awaiting start.")

        '''t.sleep(2)
        while True:
            playMixer.put("MkRhit")
            t.sleep(105)'''

        while True:
            if HouseActive and not Demo:
                break
            elif systemState != "ONLINE":
                return
            else:
                t.sleep(1)

        # -------------------------------------------   INIT BEFORE MAIN SEQUENCE
        setDoorState(1, "CLOSED")
        setDoorState(2, "CLOSED")

        toggleHouseLights(False)

        threading.Thread(target=Graveyard).start()

        t.sleep(5)

        threading.Thread(target=CaveRoom).start()
        threading.Thread(target=MirrorRoom).start()
        threading.Thread(target=SwampRoom).start()
        threading.Thread(target=MaskRoom).start()

        m2Digital_Write(53, 1)
        print("SR Swamp Lasers ON")

        while HouseActive:  # --------------------------------------------------   MAIN SEQUENCE

            # Add sequencing here

            if BreakCheck():
                t.sleep(2)
                break

            t.sleep(5)

        print("Child Main Ended")

    def spawnDoors():
        global DoorState
        global DoorSensPins
        global DoorSolenoidPins
        DoorSensPins = {  # REPLACE WITH ANALOG PINS DESIGNATED TO DOOR SENSORS
            1: 0,
            2: 1,
        }
        DoorSolenoidPins = {
            1: 56,
            2: 58,
        }
        DoorState = {
            1: "OPEN",
            2: "OPEN",
        }
        data = []
        for i in range(2):
            data.append(threading.Thread(target=Doors, args=([i + 1])))
            data[i].start()

    def end():
        print("Main sequence terminated.")

    init()
    while True:  # ----------------------------------------------------   SYSTEM LOOP
        childMain()
        end()
        t.sleep(3)
        while systemState != "ONLINE":
            t.sleep(1)


def shutdown():  # MAIN SHUTDOWN FUNCTION, ALSO REFERENCE FOR ALL HOUSE COMPONENTS
    print("SHUTDOWN - MAIN:")
    m2Digital_Write(48, 0)
    print("Audio mixer OFF")
    m2Digital_Write(47, 0)
    print("Smoke Machine OFF")

    print("SHUTDOWN - CAVE ROOM:")
    m1Digital_Write(54, 0)
    print("CR Air Blast 4 OFF")
    m1Digital_Write(27, 0)
    print("CR Ambient Lights 1 OFF")
    m1Digital_Write(35, 0)
    print("CR Lightning 1 OFF")
    m1Digital_Write(34, 0)
    print("CR Lightning 2 OFF")
    m1Digital_Write(36, 0)
    print("Can Lights OFF")
    m1Digital_Write(32, 0)
    print("CR Strobe 2 OFF")
    m1Digital_Write(30, 0)
    print("CR Swamp Monster Light OFF")
    m1Digital_Write(55, 0)
    print("CR Swamp Monster Solenoid OFF")

    print("SHUTDOWN - MIRROR ROOM:")
    m1Digital_Write(41, 0)
    print("MR Ambient Lights 2 OFF")
    m1Digital_Write(31, 0)
    print("MR Mirror Light OFF")

    print("SHUTDOWN - SWAMP ROOM:")
    m1Digital_Write(40, 0)
    print("SR Lightning 3 OFF")
    m1Digital_Write(24, 0)
    print("SR Lightning 4 OFF")
    m1Digital_Write(28, 0)
    print("SR Lightning 5 OFF")
    m1Digital_Write(61, 0)
    print("SR Air Explosion 2 OFF")
    m2Digital_Write(53, 0)
    print("SR Swamp Lasers OFF")
    m1Digital_Write(37, 0)
    print("SR Overhang Safety OFF")
    m1Digital_Write(62, 0)
    print("Bu Forward OFF")
    m1Digital_Write(63, 0)
    print("Bu Up/Down OFF")

    print("SHUTDOWN - MASK ROOM:")
    m1Digital_Write(33, 0)
    print("MkR Ambient Light 4 Blacklight OFF")
    m1Digital_Write(23, 0)
    print("MkR Strobe 3 OFF")
    m1Digital_Write(59, 0)
    print("MkR Air Blast 3 OFF")

    print("SHUTDOWN - GRAVEYARD:")
    m1Digital_Write(57, 0)
    print("GY Rock Spider Solenoid OFF")

    t.sleep(1)
    toggleHouseLights(True)


def connectArduino():
    M1PORT = "COM15"
    M2PORT = "COM12"
    global M1
    global M2

    print("Attempting to establish connection with Arduino...")
    try:
        # Connects to arduino board
        M1 = pymata4.Pymata4(
            com_port=M1PORT, baud_rate=250000, sleep_tune=0.05)
        print(f"Communication to board on {M1PORT} successfully started.")
    except:
        print(f"Board not found on {M1PORT}. Connection not established.")
        M1 = False

    try:
        M2 = pymata4.Pymata4(com_port=M2PORT, baud_rate=250000, sleep_tune=.05)
        print(f"Communication to board on {M2PORT} successfully started.")
    except:
        print(f"Board not found on {M2PORT}. Connection not established.")
        M2 = False
    try:
        for i in range(1, 71):  # Sets all digital pins as outputs for M1
            #  if i == 14 or i == 15:      #Pins we do not want to edit
            #      pass
            # else:
            M1.set_pin_mode_digital_output(i)

        for i in range(2, 14):  # Sets up M1 PWM pins
            if i == 13:
                i = 44
            M1.set_pin_mode_servo(i)
    except:
        print("Board M1 not connected, skipping pin configuration.")
    try:
        for i in range(1, 54):  # Sets all digital pins as outputs for M2
            if i == 14 or i == 15:  # Pins we do not want to edit
                continue
            M2.set_pin_mode_digital_output(i)

        # Sets all analog pins as input for M2 and disables reporting to prevent flood
        for i in range(16):
            M2.set_pin_mode_analog_input(i)
            M2.disable_analog_reporting(i)
        for i in range(8):  # Enable ONLY necessary sensors that need to be monitored constantly
            M2.enable_analog_reporting(i)
    except:
        print("Board M1 not connected, skipping pin configuration.")

    t.sleep(1)  # Allows everything to boot properly

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# SPECIAL SYNTAX FOR BOARD WRITING


def m1Digital_Write(pin, value):
    try:
        M1.digital_write(pin, value)
    except:
        print(f"Error writing to pin {pin} on board M1")


def m1PWM_Write(pin, value):
    try:
        M1.servo_write(pin, value)
    except:
        print(f"Error writing to PWM pin {pin} on board M1")


def m2Digital_Write(pin, value):
    try:
        M2.digital_write(pin, value)
    except:
        print(f"Error writing to pin {pin} on board M2")


def m2Read_Analog(pin):
    global M2AnalogValues
    # M2.enable_analog_reporting(pin)     #ONLY TURN ON ANALOG REPORTING WHEN IT ABSOLUTELY NEEDS TO BE ON, SO SERIAL DOES NOT FLOOD
    # for i in range(5):      #Clear old data
    # value = M2.analog_read(pin)
    # M2.disable_analog_reporting(pin)

    # value = M2AnalogValues[pin]
    return M2.analog_read(pin)  # value


def AnalogUpdate():
    global M2AnalogValues
    while True:
        for i in range(16):
            M2AnalogValues[i] = M2.analog_read(i)
        t.sleep(.2)
        # print(M2AnalogValues)

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def Doors(id):  # DOOR PROCESSES
    global DoorState
    global DoorSensPins
    print(f"Door {id} process created.")

    def main():
        lastDoorState = "OPEN"
        while systemState == "ONLINE":
            if lastDoorState != DoorState[id]:
                lastDoorState = handleChange(lastDoorState)
                t.sleep(.5)
            t.sleep(.5)

        print(f"Shutdown detected - Door {id} OPEN and process terminated.")
        open()

    def handleChange(lastDoorState):
        global DoorState
        if DoorState[id] == "OPEN":
            if not open():
                print(f"Door {id} interrupted")
                return handleChange(lastDoorState)
            return "OPEN"

        elif DoorState[id] == "CLOPEN":  # OPENS DOOR, WAITS X SECONDS, THEN CLOSES
            open()
            if id == 3 or id == 2:  # DOOR 3 and 2 CLOSE SLOWER AFTER OPENING
                for i in range(12):  # WAIT
                    if BreakCheck():
                        return "CLOSED"
                    t.sleep(1)
            else:  # DOOR 1 CLOSES FASTER AFTER OPENING
                for i in range(6):  # WAIT
                    if BreakCheck():
                        return "CLOSED"
                    t.sleep(1)
            while True:
                if BreakCheck():
                    return "CLOSED"
                if not close():
                    print(f"Door {id} obstructed, opening door.")
                    openFast()
                    t.sleep(1)
                    DoorState[id] = "CLOSED"
                    return handleChange(lastDoorState)
                return "CLOSED"

        else:
            x = close()
            if not x:  # IF DOOR OBSTRUCTED
                print(f"Door {id} obstructed, opening door.")
                openFast()
                # t.sleep(3)
                DoorState[id] = "CLOSED"
                return handleChange(lastDoorState)
            elif x == "ChangeTarget":
                return handleChange(lastDoorState)
            return "CLOSED"

    def open():
        targetState = DoorState[id]
        m1Digital_Write(DoorSolenoidPins[id], 1)  # ACTIVATE SOLENOID
        print(f"Door {id} opening...")
        for i in range(12):  # WAITING 6 SECONDS WITH SENSOR CHECKING FOR CHANGE IN DIRECTION
            if DoorState[id] != targetState:
                return False
            t.sleep(.5)
        DoorState[id] = "OPEN"
        print(f"Door {id} OPEN")
        return True

    def openFast():
        targetState = DoorState[id]
        m1Digital_Write(DoorSolenoidPins[id], 1)  # ACTIVATE SOLENOID
        print(f"Door {id} opening...")
        for i in range(4):  # WAITING 2 SECONDS WITH SENSOR CHECKING FOR CHANGE IN DIRECTION
            if DoorState[id] != targetState:
                return False
            t.sleep(.5)
        DoorState[id] = "OPEN"
        print(f"Door {id} OPEN")
        return True

    def close():
        targetState = DoorState[id]
        m1Digital_Write(DoorSolenoidPins[id], 0)
        print(f"Door {id} closing...")
        for i in range(5):
            doorSensCheck()  # OPENS ANALOG READ TO REMOVE OLD DATA
            t.sleep(.1)
        for i in range(16):  # WAITING 4.8 SECONDS WITH SENSOR CHECKING FOR OBSTRUCTION
            if doorSensCheck():
                return False
            if DoorState[id] != targetState:
                print(f"Door {id} interrupted")
                return "ChangeTarget"
            t.sleep(.3)
        DoorState[id] = "CLOSED"
        print(f"Door {id} CLOSED")
        return True

    def doorSensCheck():
        # print("TEST")
        # print(m2Read_Analog(DoorSensPins[id]))
        if m2Read_Analog(DoorSensPins[id]) > 200:
            return True
        else:
            return False

    open()
    main()


def setDoorState(id, state):
    global DoorState
    DoorState[id] = state


def Graveyard():
    print("Beginning Graveyard Sequence...")
    while HouseActive:
        # print("Graveyard running...")
        for i in range(random.randint(60, 65)):  # WAIT
            t.sleep(1)
            if BreakCheck():
                return

        m1Digital_Write(57, 1)
        print("GY Rock Spider Solenoid ON")

        for i in range(random.randint(1, 3)):  # WAIT
            t.sleep(1)
            if BreakCheck():
                return

        m1Digital_Write(57, 0)
        print("GY Rock Spider Solenoid OFF")

    print("Ending Graveyard Sequence...")


def CaveRoom():
    global CRstate, Demo
    print("Starting Cave Room Process...")

    while HouseActive or Demo:
        CRstate = "ACTIVE"
        t.sleep(.2)
        if m2Read_Analog(7) > 200 or Demo:
            print("Starting Cave Room Sequence...")
            setDoorState(1, "CLOPEN")

            playSound("CRintroLEFT")
            m1Digital_Write(27, 1)
            print("CR Ambient Lights 1 ON")

            threading.Thread(target=CRairBlast).start()

            while m2Read_Analog(5) < 200:
                t.sleep(.5)
                if BreakCheck():
                    return

            print("CR Swamp Monster Light ON")
            for i in range(3):
                m1Digital_Write(55, 1)
                print("CR Swamp Monster Solenoid ON")
                for i in range(5):
                    m1Digital_Write(30, 1)
                    t.sleep(.1)
                    m1Digital_Write(30, 0)
                    t.sleep(.1)
                    if BreakCheck():
                        return
                m1Digital_Write(55, 0)
                print("CR Swamp Monster Solenoid OFF")
                for i in range(5):
                    m1Digital_Write(30, 1)
                    t.sleep(.1)
                    m1Digital_Write(30, 0)
                    t.sleep(.1)
                    if BreakCheck():
                        return

            t.sleep(1)

            m1Digital_Write(27, 0)
            print("CR Ambient Lights 1 OFF")

            playSound("CRthunderLEFT")
            t.sleep(.1)

            print("CR Lightning 1 ON")
            for i in range(3):
                m1Digital_Write(35, 1)
                t.sleep(.05)
                m1Digital_Write(35, 0)
                t.sleep(.05)
                m1Digital_Write(35, 1)
                t.sleep(.05)
                m1Digital_Write(35, 0)
                print("CR Lightning 1 OFF")

                t.sleep(.1)
                if BreakCheck():
                    return

                print("CR Lightning 2 ON")
                m1Digital_Write(34, 1)
                t.sleep(.2)
                print("CR Lightning 2 OFF")
                m1Digital_Write(34, 0)

            for i in range(3):  # WAIT
                t.sleep(1)
                if BreakCheck():
                    return

            playSound("CRhitLEFT")
            t.sleep(.1)
            m1Digital_Write(32, 1)
            print("CR Strobe 2 ON")

            for i in range(4):  # WAIT
                t.sleep(1)
                if BreakCheck():
                    return

            m1Digital_Write(32, 0)
            print("CR Strobe 2 OFF")

            threading.Thread(target=smokeMachine).start()

            t.sleep(2)

            setDoorState(2, "CLOPEN")

            for i in range(3):  # WAIT
                t.sleep(1)
                if BreakCheck():
                    return

            m1Digital_Write(27, 1)
            print("CR Ambient Lights 1 ON")

            CRstate = "INACTIVE"
            if Demo:
                return

    print("Ending Cave Room Sequence...")
    CRstate = "INACTIVE"

def CRairBlast():
    t.sleep(8)
    for i in range(3):
        m1Digital_Write(54, 1)
        print("CR Air Blast 4 ON")
        t.sleep(.2)
        m1Digital_Write(54, 0)
        print("CR Air Blast 4 OFF")
        t.sleep(.1)


def MirrorRoom():
    global MRstate, CRstate, Demo

    while HouseActive or Demo:
        MRstate = "ACTIVE"
        # playSound("Quip02")

        m1Digital_Write(41, 1)
        print("MR Ambient Lights 2 ON")

        while m2Read_Analog(3) < 200:
            t.sleep(.5)
            if BreakCheck():
                return

        playMixer.put("MRhit")
        t.sleep(.1)

        print("MR Mirror Light ON")
        for i in range(10):
            m1Digital_Write(31, 1)
            t.sleep(.15)
            m1Digital_Write(31, 0)
            t.sleep(.15)
        print("MR Mirror Light OFF")

        if Demo:
            return

    print("Ending Mirror Room Sequence...")
    MRstate = "INACTIVE"


def SwampRoom():
    global SRstate, Demo, smokeSequence, laserSequence
    smokeSequence = 1
    laserSequence = False

    while HouseActive or Demo:
        SRstate = "ACTIVE"

        while m2Read_Analog(2) < 200:
            t.sleep(1)
            if BreakCheck():
                return
            if Demo:
                threading.Thread(target=smokeMachine).start()
                break

        playMixer.put("MRswampEntrance")
        playSound("swampThunderRIGHT")
        t.sleep(.1)

        m2Digital_Write(53, 1)
        print("SR Swamp Lasers ON")

        for i in range(3):
            for i in range(random.randint(2,3)):
                m1Digital_Write(40,1)
                t.sleep(randMixedNum(.05,.1))
                m1Digital_Write(40,0)
                t.sleep(randMixedNum(.05,.17))    
            if BreakCheck():
                return

            t.sleep(.7)

            for i in range(3):
                m1Digital_Write(28,1)
                t.sleep(randMixedNum(.05,.1))
                m1Digital_Write(28,0)
                t.sleep(randMixedNum(.05,.17))   

            if BreakCheck():
                return

            t.sleep(.5)

        while m2Read_Analog(4) < 200:
            t.sleep(.2)

        playSound("swampSplashRIGHT")
        t.sleep(.1)

        if BreakCheck():
            return

        m1Digital_Write(61, 1)
        print("SR Air Explosion 2 ON")

        for i in range(10):
            m2Digital_Write(53, 1)
            print("SR Swamp Lasers ON")
            t.sleep(.1)
            m2Digital_Write(53, 0)
            print("SR Swamp Lasers OFF")
            t.sleep(.1)

        m1Digital_Write(61, 0)
        print("SR Air Explosion 2 OFF")

        for i in range(1):
            t.sleep(1)
            if BreakCheck():
                return

        playSound("swampHitRIGHT")

        m1Digital_Write(26, 1)
        print("SR Strobe 1 ON")

        t.sleep(.5)

        m1Digital_Write(62, 1)
        print("Bu Forward ON")
        t.sleep(.7)
        m1Digital_Write(63, 1)
        print("Bu Up/Down ON")
        t.sleep(1)

        if BreakCheck():
            return
        
        for i in range(8):
            m1Digital_Write(63, 0)
            print("Bu Up/Down OFF")
            t.sleep(randMixedNum(.05,.6))
            m1Digital_Write(63, 1)
            print("Bu Up/Down ON")
            t.sleep(randMixedNum(.05,.6))

        m1Digital_Write(62, 0)
        print("Bu Forward OFF")

        if BreakCheck():
            return
        
        laserSequence = True
        threading.Thread(target=swampLasers).start()
        
        for i in range(5):
            m1Digital_Write(63, 0)
            print("Bu Up/Down OFF")
            t.sleep(randMixedNum(.05,.6))
            m1Digital_Write(63, 1)
            print("Bu Up/Down ON")
            t.sleep(randMixedNum(.05,.6))

        laserSequence = False

        m1Digital_Write(63, 0)
        print("Bu Up/Down OFF")

        m2Digital_Write(53, 1)
        print("SR Swamp Lasers ON")
        m1Digital_Write(37, 1)
        print("SR Overhang Safety ON")
        m1Digital_Write(26, 0)
        print("SR Strobe 1 OFF")

        for i in range(20): #6 seconds
            m2Digital_Write(53, 1)
            print("SR Swamp Lasers ON")
            t.sleep(.2)
            m2Digital_Write(53, 0)
            print("SR Swamp Lasers OFF")
            t.sleep(.1)

        m2Digital_Write(53, 1)
        print("SR Swamp Lasers ON")

        m1Digital_Write(37, 0)
        print("SR Overhang Safety OFF")

        print("Swamp Room Sequence Ended")
        SRstate = "INACTIVE"
        if Demo:
            return
        
def swampLasers():
    while laserSequence:
        m2Digital_Write(53, 1)
        print("SR Swamp Lasers ON")
        t.sleep(.2)
        m2Digital_Write(53, 0)
        print("SR Swamp Lasers OFF")
        t.sleep(.1)

def smokeMachine():
    global smokeSequence

    if smokeSequence == 1:
        m2Digital_Write(47, 1)
        print("Smoke Machine ON")
        t.sleep(3)
        m2Digital_Write(47, 0)
        print("Smoke Machine OFF")
        t.sleep(3)
        m2Digital_Write(47, 1)
        print("Smoke Machine ON")
        t.sleep(1.5)
        m2Digital_Write(47, 0)
        print("Smoke Machine OFF")
        t.sleep(3)
        m2Digital_Write(47, 1)
        print("Smoke Machine ON")
        t.sleep(.6)
        m2Digital_Write(47, 0)
        print("Smoke Machine OFF")

        smokeSequence = 2

    elif smokeSequence == 2:
        m2Digital_Write(47, 1)
        print("Smoke Machine ON")
        t.sleep(1.4)
        m2Digital_Write(47, 0)
        print("Smoke Machine OFF")
        t.sleep(3)
        m2Digital_Write(47, 1)
        print("Smoke Machine ON")
        t.sleep(.6)
        m2Digital_Write(47, 0)
        print("Smoke Machine OFF")

        smokeSequence = 1

def MaskRoom():
    global MkRstate, Demo
    threading.Thread(target=MaskRoomMusicLoop).start()

    while HouseActive or Demo:
        MkRstate = "ACTIVE"

        m1Digital_Write(33, 1)
        print("MkR Ambient Light 4 Blacklight ON")

        while m2Read_Analog(6) < 200:
            t.sleep(1)
            if BreakCheck():
                return
            
        playMixer.put("MkRhit")

        #m1Digital_Write(33, 0)
        #print("MkR Ambient Light 4 Blacklight OFF")

        m1Digital_Write(23, 1)
        print("MkR Strobe 3 ON")

        t.sleep(1)

        if BreakCheck():
            return

        for i in range(3):
            m1Digital_Write(59, 1)
            print("MkR Air Blast 3 ON")
            t.sleep(1)
            m1Digital_Write(59, 0)
            print("MkR Air Blast 3 OFF")
            t.sleep(.3)

        if BreakCheck():
            return
        
        m1Digital_Write(23, 0)
        print("MkR Strobe 3 OFF")

        m1Digital_Write(33, 1)
        print("MkR Ambient Light 4 Blacklight ON")

        MkRstate = "INACTIVE"
        if Demo:
            return

    #playMixer.put("PR2")  # ambience

    MkRstate = "INACTIVE"

def MaskRoomMusicLoop():
    playMixer.put("MkRmusic")
    for i in range(53): #wait 105 seconds
        t.sleep(2)
        if BreakCheck():
            return
        

def functionTest():
    global testing
    testing = True
    while testing:
        state = "ON"
        dValue = 1
        pwmValue = 180
        for i in range(2):
            print("TEST - MAIN:")
            print(f"Audio mixer {state}")
            t.sleep(.1)

            print("TEST - INFERNO ROOM:")
            m1Digital_Write(54, dValue)
            print(f"Industrial fan {state}")
            t.sleep(.1)
            m1Digital_Write(65, dValue)
            print(f"IR flickering light {state}")
            t.sleep(.1)
            m1Digital_Write(68, dValue)
            print(f"Sparks {state}")
            t.sleep(2)
            m1Digital_Write(68, 0)
            m1Digital_Write(25, dValue)
            print(f"IR Strobe {state}")
            t.sleep(.1)
            m1Digital_Write(39, dValue)
            print(f"IR Flashbang {state}")
            t.sleep(.1)
            m1Digital_Write(24, dValue)
            print(f"IR Ambient Light {state}")
            t.sleep(.1)
            m1PWM_Write(4, pwmValue)
            print(f"IR Projector Cover {state}")
            t.sleep(.1)
            m1Digital_Write(33, dValue)
            print(f"IR Fire Lights {state}")
            t.sleep(.1)
            m1PWM_Write(5, pwmValue)
            print(f"IR Dimmer Servo {state}")
            t.sleep(.1)
            m1Digital_Write(23, dValue)
            print(f"IR FireIce {state}")
            t.sleep(.1)

            print("TEST - MIRROR ROOM:")
            m1PWM_Write(2, pwmValue)
            print(f"MR Dimmer Servo {state}")
            t.sleep(.1)
            m1Digital_Write(34, dValue)
            print(f"MR Dimming Light {state}")
            t.sleep(.1)
            m1Digital_Write(27, dValue)
            print(f"MR Strobe {state}")
            t.sleep(.1)
            m1Digital_Write(35, dValue)
            print(f"MR Creepy Light {state}")
            t.sleep(.1)
            m1Digital_Write(26, dValue)
            print(f"MR Lightning {state}")
            t.sleep(.1)

            print("TEST - PIPE ROOM:")
            m1PWM_Write(3, pwmValue)
            print(f"PR LFrame Servo {state}")
            t.sleep(.1)
            m1PWM_Write(6, pwmValue)
            print(f"PR RFrame Servo {state}")
            t.sleep(.1)
            dValue = 0
            pwmValue = 0
            state = "OFF"


def canLights():
    print("Starting can lights process...")
    while systemState == "ONLINE":
        m1Digital_Write(36, 1)
        t.sleep(random.randint(1, 10))
        m1Digital_Write(36, 0)
        t.sleep(randMixedNum(.05, .2))
    print("Can lights process terminated.")


def HTTP_SERVER():

    HOST = "192.168.7.2"
    PORT = 9999

    class HalloweenHTTP(BaseHTTPRequestHandler):
        def do_GET(self):
            message = self.path
            # VARIABLE self.path RETURNS THE WEB BROWSER URL REQUESTED. EXAMPLE: http://192.168.1.76:9999/START RETURNS /START
            print(f"Received HTTP request: {message}")

            if message == "/START":
                StartHouse()
            if message == "/EMERGENCY_SHUTOFF":
                EmergencyShutoff()
            if message == "/SOFT_SHUTDOWN":
                SoftShutdown()
            elif message == "/Door1Open":
                DoorState[1] = "OPEN"
            elif message == "/Door1Close":
                DoorState[1] = "CLOSED"
            elif message == "/Door2Open":
                DoorState[2] = "OPEN"
            elif message == "/Door2Close":
                DoorState[2] = "CLOSED"
            elif message == "/DemoCaveRoom":
                demoEvent("CR")
            elif message == "/DemoMirrorRoom":
                demoEvent("MR")
            elif message == "/DemoSwampRoom":
                demoEvent("SR")
            elif message == "/DemoMaskRoom":
                demoEvent("MkR")
            elif message == "/ToggleHouseLights":
                toggleHouseLights()

            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(bytes("<html><body><h1>Received " +
                             message + "</h1></body></html>", "utf-8"))

    server = HTTPServer((HOST, PORT), HalloweenHTTP)
    print("Server now running...")

    server.serve_forever()
    server.server_close()
    print("Server Stopped.")


def MainGUI():
    root = tk.Tk()
    root.configure(background="orange")
    root.title("Halloween 2024 Control Panel")
    root.geometry("465x1080")

    # entry1 = tk.Entry(root)
    # entry1.place(x=200, y=25)

    lblDoorControls = tk.Label(root, text="DOOR CONTROLS", font=(
        "Helvetica bold", 15), bg="orange")
    lblDoorControls.place(x=25, y=200)

    lblMains = tk.Label(root, text="MAINS", font=(
        "Helvetica bold", 15), bg="orange")
    lblMains.place(x=25, y=15)

    lblDemoControls = tk.Label(root, text="DEMO CONTROLS", font=(
        "Helvetica bold", 15), bg="orange")
    lblDemoControls.place(x=25, y=395)

    lblAdvControls = tk.Label(root, text="ADVANCED CONTROLS", font=(
        "Helvetica bold", 15), bg="orange")
    lblAdvControls.place(x=25, y=535)

    btnStart = tk.Button(root, text="START HAUNTED HOUSE",
                         height=3, width=25, bg="turquoise1", command=StartHouse)
    btnStart.place(x=250, y=50)

    btnEmergencyShutoff = tk.Button(
        root, text="EMERGENCY SHUTOFF", height=3, width=25, bg="red", command=EmergencyShutoff)
    btnEmergencyShutoff.place(x=25, y=50)

    btnSoftShutdown = tk.Button(
        root, text="SOFT SHUTDOWN", height=3, width=25, bg="yellow", command=SoftShutdown)
    btnSoftShutdown.place(x=25, y=125)

    btnDemoIR = tk.Button(root, text="Demo Cave Room",
                          height=2, width=15, command=lambda: demoEvent('CR'))
    btnDemoIR.place(x=150, y=430)

    btnDemoMR = tk.Button(root, text="Demo Mirror Room",
                          height=2, width=15, command=lambda: demoEvent("MR"))
    btnDemoMR.place(x=25, y=480)

    btnDemoSR = tk.Button(root, text="Demo Swamp Room",
                          height=2, width=15, command=lambda: demoEvent('SR'))
    btnDemoSR.place(x=25, y=430)

    btnDemoPR = tk.Button(root, text="Demo Mask Room",
                          height=2, width=15, command=lambda: demoEvent("MkR"))
    btnDemoPR.place(x=150, y=480)

    btnOpenDoor1 = tk.Button(root, text="Open Door 1", height=2,
                             width=15, command=lambda: setDoorState(1, "OPEN"))
    btnOpenDoor1.place(x=25, y=235)

    btnCloseDoor1 = tk.Button(root, text="Close Door 1", height=2,
                              width=15, command=lambda: setDoorState(1, "CLOSED"))
    btnCloseDoor1.place(x=150, y=235)

    btnOpenDoor2 = tk.Button(root, text="Open Door 2", height=2,
                             width=15, command=lambda: setDoorState(2, "OPEN"))
    btnOpenDoor2.place(x=25, y=285)

    btnCloseDoor2 = tk.Button(root, text="Close Door 2", height=2,
                              width=15, command=lambda: setDoorState(2, "CLOSED"))
    btnCloseDoor2.place(x=150, y=285)

    btnOpenDoor3 = tk.Button(root, text="Open Door 3", height=2,
                             width=15, command=lambda: setDoorState(3, "OPEN"))
    btnOpenDoor3.place(x=25, y=335)

    btnCloseDoor3 = tk.Button(root, text="Close Door 3", height=2,
                              width=15, command=lambda: setDoorState(3, "CLOSED"))
    btnCloseDoor3.place(x=150, y=335)

    btnFunctionTest = tk.Button(root, text="Start Testing", height=2, width=15,
                                command=lambda: threading.Thread(target=functionTest).start())
    btnFunctionTest.place(x=25, y=570)

    btnHouseLights = tk.Button(root, text="Toggle House Lights",
                               height=3, width=25, bg="chartreuse2", command=toggleHouseLights)
    btnHouseLights.place(x=250, y=125)

    btnDF1 = tk.Button(root, text="Cycle DF Player 1", height=2,
                       width=15, command=lambda: playMixer.put("MRswampEntrance"))
    btnDF1.place(x=150, y=570)

    btnDF2 = tk.Button(root, text="Cycle DF Player 2", height=2,
                       width=15, command=lambda: playMixer.put("MkRmusic"))
    btnDF2.place(x=150, y=620)

    btnDF3 = tk.Button(root, text="Cycle DF Player 3", height=2,
                       width=15, command=lambda: playMixer.put("MkRhit"))
    btnDF3.place(x=150, y=670)

    btnDF4 = tk.Button(root, text="Cycle DF Player 4", height=2,
                       width=15, command=lambda: playMixer.put("Unused"))
    btnDF4.place(x=150, y=720)

    btnDF5 = tk.Button(root, text="Cycle DF Player 5", height=2,
                       width=15, command=lambda: playMixer.put("MRhit"))
    btnDF5.place(x=150, y=770)

    root.mainloop()


def demoEvent(room):
    global Demo
    global HouseActive
    Demo = True
    HouseActive = True
    toggleHouseLights(False)
    print(f"Starting demo of {room}")
    if room == 'CR':
        # t.sleep(1)
        threading.Thread(target=CaveRoom).start()
    elif room == 'MR':
        threading.Thread(target=MirrorRoom).start()
    elif room == 'SR':
        threading.Thread(target=SwampRoom).start()
    elif room == 'MkR':
        threading.Thread(target=MaskRoom).start()

    t.sleep(2)

    threading.Thread(target=demoEventChecker).start()


def demoEventChecker():
    global HouseActive
    global Demo
    while (CRstate == "ACTIVE" or MRstate == "ACTIVE" or MkRstate == "ACTIVE" or SRstate == "ACTIVE"):
        if BreakCheck():
            break
        t.sleep(1)
        print(f"{CRstate}, {MRstate}, {MkRstate}, {SRstate}")

    toggleHouseLights(True)
    HouseActive = False
    Demo = False


# Function for random mixed number values rounded to hundredth place.
def randMixedNum(min, max):
    return round(random.uniform(min, max), 2)


# Use this function when controlling house lights. It will automatically turn on all lights that can be used to illuminate
def toggleHouseLights(state=None):
    # state=0 means no manual control, True sets lights to ON and False sets to OFF
    global houseLights

    if state is not None:  # Manual Control
        if state:
            HLon()
        else:
            HLoff()
    else:
        if houseLights:  # If not manual, switch to opposite state
            HLoff()
        else:
            HLon()

 # Use the following functions to setup all house lights


def HLon():
    global houseLights
    m1Digital_Write(65, 1)
    print("House Lights ON")
    m1Digital_Write(35, 1)
    m1Digital_Write(34, 1)
    print("CR Lightning ON")
    m1Digital_Write(40, 1)
    m1Digital_Write(24, 1)
    m1Digital_Write(28, 1)
    print("SR Lightning ON")
    m1Digital_Write(33, 1)
    print("MkR Ambient Light 4 Blacklight ON")
    houseLights = True


def HLoff():
    global houseLights
    m1Digital_Write(65, 0)
    print("House Lights OFF")
    m1Digital_Write(35, 0)
    m1Digital_Write(34, 0)
    print("CR Lightning OFF")
    m1Digital_Write(40, 0)
    m1Digital_Write(24, 0)
    m1Digital_Write(28, 0)
    print("SR Lightning OFF")
    m1Digital_Write(33, 0)
    print("MkR Ambient Light 4 Blacklight OFF")
    houseLights = False


def StartHouse():

    global HouseActive

    if not HouseActive and systemState == "ONLINE":
        print("Starting Haunted House...")
        HouseActive = True
    else:
        if not HouseActive and systemState != "ONLINE":
            print("Cannot start house while it is in a shutdown state.")
        else:
            print(
                "House is already active. Please stop the house before attemping to re-start it.")


def EmergencyShutoff():

    global HouseActive
    global systemState

    if systemState == "EmergencyShutoff":
        print("Emergency shutoff already activated.")
    else:
        systemState = "EmergencyShutoff"
        print("EMERGENCY SHUTOFF ACTIVATED")
        HouseActive = False
        shutdown()
        for i in range(3):
            toggleHouseLights()
            t.sleep(.1)
            toggleHouseLights()
            t.sleep(.1)
        toggleHouseLights(True)


def SoftShutdown():

    global HouseActive
    global systemState

    t.sleep(1.5)

    if systemState != "ONLINE":
        print("Cannot shutdown when the system is already in an offline state.")
    else:
        systemState = "SoftShutdown"
        print("SOFT SHUTDOWN ACTIVATED")
        HouseActive = False
        shutdown()
        for i in range(3):
            toggleHouseLights()
            t.sleep(.1)
            toggleHouseLights()
            t.sleep(.1)
        toggleHouseLights(True)


def BreakCheck():  # RETURNS TRUE IF HOUSE IS STOPPING, MUST RUN FREQUENTLY IN ALL LOOPS

    global HouseActive

    if not HouseActive:
        # print("Break Check Returned TRUE, breaking loop.")
        return True
    else:
        return False


def playSound(file):
    global SOUND
    if SOUND:
        sound = AudioSegment.from_mp3(
            f"C:\\Users\\School\\Desktop\\2024v3.0\\SoundDir\\{file}.mp3")
        print(f"Playing sound {file}")
        threading.Thread(target=play, args=([sound])).start()
    else:
        print("SOUND MUTED")


def audioMixer(playMixer):  # THIS IS A SEPARATE PROCESS
    MIXERUNOPORT = "COM16"
    MIXERUNO = pymata4.Pymata4(com_port=MIXERUNOPORT, sleep_tune=0.1)

    for i in range(2, 7):  # PINS 2-6
        MIXERUNO.set_pin_mode_digital_output(i)

    playerID = {
        "MRswampEntrance": 4,
        "MkRmusic": 5,
        "MkRhit": 6,
        "Unused": 2,
        "MRhit": 3
    }

    print("Audio Mixer Initialized.")
    while True:
        id = playMixer.get()
        pin = playerID[id]
        MIXERUNO.digital_write(pin, 1)
        print(f"Mixer player {id} enabled")
        t.sleep(.1)
        MIXERUNO.digital_write(pin, 0)


def debugDoors():
    t.sleep(5)
    while True:
        print(DoorState)
        t.sleep(3)


def debugSensors():
    while True:
        t.sleep(1)
        for i in range(8):
            x = m2Read_Analog(i)
            print(f"Analog {i}: {x}")


def shutdownDetector():
    global systemState
    global testing

    while systemState == "ONLINE":
        t.sleep(1)

    t.sleep(4)

    if systemState == "EmergencyShutoff":
        print("EMERGENCY SHUTOFF DETECTED - Please type keyword 'SAFE' into terminal to return to standby mode.")
        testing = False
        while True:
            input1 = input()
            input1 = input1.upper()
            if input1 == "SAFE":
                a = 5
                for i in range(5):
                    print(f"Returning to standby in {a} seconds.")
                    a = a - 1
                    t.sleep(1)
                systemState = "ONLINE"
            else:
                print(
                    "Invalid command. Please type keyword 'SAFE' into terminal to return to standby mode.")
    elif systemState == "SoftShutdown":
        print("SOFT SHUTDOWN DETECTED - Systems will be restarted to standby.")
        testing = False
        a = 5
        for i in range(5):
            print(f"Returning to standby in {a} seconds.")
            a = a - 1
            t.sleep(1)
        systemState = "ONLINE"
    else:
        print("Shutdown ID unknown - Please type keyword 'SAFE' into terminal to return to standby mode.")
        testing = False
        while True:
            input1 = input()
            input1 = input1.upper()
            if input1 == "SAFE":
                systemState = "ONLINE"
            else:
                print(
                    "Invalid command. Please type keyword 'SAFE' into terminal to return to standby mode.")


if __name__ == '__main__':
    main()
