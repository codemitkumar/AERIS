import socket
import math

class FlightGearSender:
    def __init__(self, host="127.0.0.1", port=5500):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addr = (host, port)

    def send(self, state):
        pitch = math.degrees(state["pitch"])
        roll = math.degrees(state["roll"])
        heading = math.degrees(state["heading"])

        message = (
            f"{state['lat']},"
            f"{state['lon']},"
            f"{state['altitude']},"
            f"{state['airspeed']},"
            f"{pitch},"
            f"{roll},"
            f"{heading}\n"
        )

        print("SENT:", message)
        self.sock.sendto(message.encode(), self.addr)