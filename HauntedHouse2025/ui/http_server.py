from http.server import HTTPServer, BaseHTTPRequestHandler
from control.doors import setDoorState
from control.houseLights import toggleHouseLights
from utils.tools import log_event
from context import house

HOST = "0.0.0.0"  # Listen on all interfaces
PORT = 9999

class HalloweenHTTP(BaseHTTPRequestHandler):
    def do_GET(self):
        from control.system import StartHouse
        
        message = self.path
        log_event(f"[HTTP] Received request: {message}")

        if message == "/START":
            StartHouse()

        elif message == "/EMERGENCY_SHUTOFF":
            house.systemState = "EmergencyShutoff"

        elif message == "/SOFT_SHUTDOWN":
            house.systemState = "SoftShutdown"

        elif message == "/Door1Open":
            setDoorState(1, "OPEN")

        elif message == "/Door1Close":
            setDoorState(1, "CLOSED")

        elif message == "/Door2Open":
            setDoorState(2, "OPEN")

        elif message == "/Door2Close":
            setDoorState(2, "CLOSED")

        elif message == "/ToggleHouseLights":
            toggleHouseLights()

        # Demo endpoints commented out for now
        # elif message == "/Demogangway":
        #     demoEvent("TR")
        # elif message == "/DemotreasureRoom":
        #     demoEvent("TR")
        # elif message == "/DemoQuarterdeck":
        #     demoEvent("SR")
        # elif message == "/DemocargoHold":
        #     demoEvent("MkR")

        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(bytes(f"<html><body><h1>Received {message}</h1></body></html>", "utf-8"))

def HTTP_SERVER():
    log_event(f"[HTTP] Attempting to host server at http://{HOST}:{PORT}")
    server = HTTPServer((HOST, PORT), HalloweenHTTP)
    log_event(f"[HTTP] Server started at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        log_event("[HTTP] Server stopped.")
