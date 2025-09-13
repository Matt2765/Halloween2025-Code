# house_state.py
class HouseState:
    def __init__(self):
        self.Boot = True
        self.HouseActive = False
        self.systemState = "OFFLINE"
        self.Demo = False
        self.SOUND = True
        self.testing = False
        self.houseLights = False
        self.FRthreadRunning = False
        self.remote_sensor_value = None

        self.CRstate = "INACTIVE"
        self.MRstate = "INACTIVE"
        self.SRstate = "INACTIVE"
        self.MkRstate = "INACTIVE"

        self.M2AnalogValues = [0] * 16

        self.DoorState = {}
        self.DoorSensPins = {}
        self.DoorSolenoidPins = {}

        self.smokeSequence = 1
        self.laserSequence = False

        self.DEBUG_INFO = False