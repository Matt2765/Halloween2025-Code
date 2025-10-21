# control/arduino.py
import time as t
from pymata4 import pymata4
from utils.tools import log_event
from context import house

M1PORT = "COM4"
M2PORT = "COM12"
M1 = None
M2 = None
M1_available = False
M2_available = False

def m1Digital_Write(pin, value):
    if M1_available:
        M1.digital_write(pin, value)
    else:
        log_event(f"[Arduino] (Simulated) m1Digital_Write(pin={pin}, value={value})")


def m2Digital_Write(pin, value):
    if M2_available:
        M2.digital_write(pin, value)
    else:
        log_event(f"[Arduino] (Simulated) m2Digital_Write(pin={pin}, value={value})")


def m2Read_Analog(pin):
    if M2_available:
        if house.DEBUG_INFO == True:
            log_event(f"[Arduino] m2Read_Analog(pin={pin}) returning {M2.analog_read(pin)[0]}")
        return M2.analog_read(pin)[0]
    else:
        if house.DEBUG_INFO == True:
            log_event(f"[Arduino] (Simulated) m2Read_Analog(pin={pin}) returning 0")
        return 0

def connectArduino():
    global M1
    global M2
    global M1_available
    global M2_available
    
    log_event("[Arduino] Attempting to establish connection with Arduino...")
    try:
        # Connects to arduino board
        M1 = pymata4.Pymata4(
            com_port=M1PORT, baud_rate=250000, sleep_tune=0.05)
        log_event(f"[Arduino] Communication to board on {M1PORT} successfully started.")
        M1_available = True
    except Exception as e:
        log_event(f"[Arduino] Board not found on {M1PORT}. Connection not established. Error: [{e}]")
        M1_available = False

    try:
        M2 = pymata4.Pymata4(com_port=M2PORT, baud_rate=250000, sleep_tune=.05)
        log_event(f"[Arduino] Communication to board on {M2PORT} successfully started.")
        M2_available = True
    except Exception as e:
        log_event(f"[Arduino] Board not found on {M2PORT}. Connection not established. Error: [{e}]")
        M2_available = False
        
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
    except Exception as e:
        log_event(f"[Arduino] Board M1 not connected, skipping pin configuration.  Error: [{e}]")
        
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
    except Exception as e:
        log_event(f"[Arduino] Board M2 not connected, skipping pin configuration. Error: [{e}]")

    t.sleep(1)  # Allows everything to boot properly