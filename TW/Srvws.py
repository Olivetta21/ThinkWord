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
        self.room = None

class Room:
    serial = 0
    def nextSerial():
        Room.serial += 1
        return Room.serial

    def __init__(self, code, rid):
        self.msgs = asyncio.Queue()
        self.players = set()
        self.code = code
        self.rid = rid
    
    def addPlayer(self, pid):
        self.players.add(pid)
        players[pid].room = self.rid

    def removePlayer(self, pid):
        if pid in self.players:
            players[pid].room = None
        self.players.discard(pid)
        return len(self.players) == 0

def remPlayerFromRoom(pid, rid = None):        
    rooms_to_delete = []

    if rid is not None and rid in rooms:
        if rooms[rid].removePlayer(pid):
            rooms_to_delete.append(rid)        
    else:
        for rid, room in rooms.items():
            if pid in room.players:
                if room.removePlayer(pid):
                    rooms_to_delete.append(rid)

    for rid in rooms_to_delete:
        del rooms[rid]
        print(f"Room {rid} deleted")

players = {}
rooms = {}

async def hp_messages(pid):
    p = players[pid]
    async for msg in p.ws:
        m = json.loads(msg)
        print(f"{pid}: {m}")

        if "c" in m: # Commands
            c = m["c"]
            if c == "ping":
                await p.ws.send("pong")
            elif c.startswith("me:"):
                p.name = c[3:]
                await p.ws.send(f"Olá {p.name}")
            elif c == "rooms":
                room_list = ""
                for rid, room in rooms.items():
                    players_list = ""
                    for uid in room.players:
                        players_list += f"{players[uid].name}, "
                    room_list += f"({rid}: {players_list})"
                await p.ws.send(room_list)
            elif c.startswith("create:"):
                if p.name is None:
                    await p.ws.send("You must set your name first")
                    continue
                if p.room is not None:
                    await p.ws.send("You are already in a room")
                    continue
                room_code = c[7:]
                rid = Room.nextSerial()
                rooms[rid] = Room(room_code, rid)
                rooms[rid].addPlayer(pid)
                await p.ws.send(f"Room({rid}) {room_code} created")
            elif c.startswith("join:"):
                if p.name is None:
                    await p.ws.send("You must set your name first")
                    continue
                if p.room is not None:
                    await p.ws.send("You are already in a room")
                    continue
                room_code = c[5:]
                found = False
                for rid, room in rooms.items():
                    if room.code == room_code:
                        room.addPlayer(pid)
                        found = True
                        await p.ws.send(f"Joined room({rid}) {room_code}")
                        break
                if not found:
                    await p.ws.send(f"Room {room_code} not found")
            elif c.startswith("leave"):
                if p.room is None:
                    await p.ws.send("You are not in a room")
                    continue
                if p.room in rooms:
                    remPlayerFromRoom(pid, p.room)
                else:
                    remPlayerFromRoom(pid)
                    p.room = None
                await p.ws.send(f"Left room")

async def handler(ws):
    pid = Player.nextSerial()
    players[pid] = Player(ws)

    try:
        print(f"Player {pid} connected")
        await hp_messages(pid)
    finally:
        remPlayerFromRoom(pid)
        del players[pid]
        print(f"Player {pid} disconnected")

async def main():
    server = await websockets.serve(handler, "localhost", 8765)
    print("Server started")
    await server.wait_closed()

asyncio.run(main())