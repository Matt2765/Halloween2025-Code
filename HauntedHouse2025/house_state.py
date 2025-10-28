# house_state.py
class HouseState:
    def __init__(self):
        self.Boot = True
        self.HouseActive = False
        self.systemState = "OFFLINE"
        self.Demo = False
        self.SOUND = True
        self.testing = False
        self.houseLights = True
        self.FRthreadRunning = False
        self.remote_sensor_value = None

        self.TRstate = "INACTIVE"
        self.TRstate = "INACTIVE"
        self.SRstate = "INACTIVE"
        self.MkRstate = "INACTIVE"

        self.DoorState = {}
        self.TargetDoorState = {}

        self.smokeSequence = 1
        self.laserSequence = False

        self.DEBUG_INFO = False
        self.DEBUG_BREAKCHECK = True