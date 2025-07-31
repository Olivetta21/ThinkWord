import asyncio
import websockets
import random
import json

class Player:
    serial = 0
    def nextSerial(self):
        self.serial += 1
        return self.serial

    def __init__(self, ws):
        self.ws = ws
        self.name = None


class Room:
    serial = 0
    def nextSerial(self):
        self.serial += 1
        return self.serial
    
    def __init__(self):
        self.msgs = asyncio.Queue()
        self.users = set()



if __name__ == "__main__":
