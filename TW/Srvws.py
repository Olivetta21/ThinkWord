import asyncio
import websockets
import random
import json

class Player:
    serial = 0
    def nextSerial():
        Player.serial += 1
        return Player.serial

    def __init__(self, ws):
        self.ws = ws
        self.name = None

class Room:
    serial = 0
    def nextSerial():
        Room.serial += 1
        return Room.serial

    def __init__(self):
        self.msgs = asyncio.Queue()
        self.users = set()

players = {}
rooms = {}

async def hp_messages(pid):
    p = players[pid]
    async for msg in p.ws:
        if msg == "ping":
            await p.ws.send("pong")

async def handler(ws):
    pid = Player.nextSerial()
    players[pid] = Player(ws)

    try:
        print(f"Player {pid} connected")
        await hp_messages(pid)
    finally:
        del players[pid]
        print(f"Player {pid} disconnected")

async def main():
    server = await websockets.serve(handler, "localhost", 8765)
    print("Server started")
    await server.wait_closed()

asyncio.run(main())