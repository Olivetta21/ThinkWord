import asyncio
import websockets
import random
import json


class FMsg:
    def rc(msg, pname=None):
        return json.dumps({
            "t": "rc",
            "f": pname if pname else "srv",
            "m": msg
        })
    
    def rooms():
        return json.dumps({
            "t": "rl",
            "r": {sala.code: [players[pid].name for pid in sala.players] for sala in rooms.values()}
        })

    def gameState(state_id):
        return json.dumps({
            "t": "gs",
            "s": state_id
        })
    
    def playerTyping(msg):
        return json.dumps({
            "t": "pt",
            "m": msg
        })
    
    def playerWord(msg, result):
        return json.dumps({
            "t": "pw",
            "m": msg,
            "r": result
        })

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

    async def echo(self, msg, sender_pid=None):
        for pid in self.players:
            if pid != sender_pid:
                await players[pid].ws.send(msg)

    def addPlayer(self, pid):
        self.players.add(pid)
        players[pid].room = self.rid
        asyncio.create_task(self.echo(FMsg.rc(f"{players[pid].name} joined the room")))

    def removePlayer(self, pid):
        if pid in self.players:
            players[pid].room = None
        self.players.discard(pid)
        asyncio.create_task(self.echo(FMsg.rc(f"{players[pid].name} left the room")))
        return len(self.players) == 0
    
    async def startGame(self):
        partida = 10
        while partida > 0:
            partida -= 1
            await self.echo(FMsg.playerTyping(""))
            await self.echo(FMsg.gameState(1)) # Loading
            await asyncio.sleep(2)
            await self.echo(FMsg.gameState(2)) # Selecting player
            await asyncio.sleep(2)
            pid = random.choice(list(self.players))
            p = players[pid]
            await self.echo(FMsg.rc(f"{p.name} is the chosen player for this round"))
            await asyncio.sleep(5)
            await self.echo(FMsg.gameState(3), pid) # Jogadores não escolhidos
            await p.ws.send(FMsg.gameState(4)) # Jogador escolhido        
            
            inicio = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - inicio < 10:
                try:
                    pid_, type, msg = await asyncio.wait_for(self.msgs.get(), timeout=0.1)
                    if pid_ == pid:
                        if type == "t": # Player typing
                            await self.echo(FMsg.playerTyping(msg), pid)
                        elif type == "m": # Player message
                            # Logica de acerto mock
                            if msg.startswith("a"):
                                await self.echo(FMsg.playerWord(msg, 1))
                                break
                            else:
                                await self.echo(FMsg.playerWord(msg, 0))
                except asyncio.TimeoutError:
                    continue
            
        await self.echo(FMsg.gameState(0))


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
                await p.ws.send(FMsg.rc("pong"))
            elif c.startswith("me:"):
                p.name = c[3:]
                await p.ws.send(FMsg.rc(f"Olá {p.name}"))
            elif c == "rooms":
                await p.ws.send(FMsg.rooms())
            elif c.startswith("create:"):
                if p.name is None:
                    await p.ws.send(FMsg.rc("You must set your name first"))
                    continue
                if p.room is not None:
                    await p.ws.send(FMsg.rc("You are already in a room"))
                    continue
                room_code = c[7:]
                rid = Room.nextSerial()
                rooms[rid] = Room(room_code, rid)
                rooms[rid].addPlayer(pid)
                await p.ws.send(FMsg.rc(f"Room({rid}) {room_code} created"))
            elif c.startswith("join:"):
                if p.name is None:
                    await p.ws.send(FMsg.rc("You must set your name first"))
                    continue
                if p.room is not None:
                    await p.ws.send(FMsg.rc("You are already in a room"))
                    continue
                room_code = c[5:]
                found = False
                for rid, room in rooms.items():
                    if room.code == room_code:
                        room.addPlayer(pid)
                        found = True
                        break
                if not found:
                    await p.ws.send(FMsg.rc(f"Room {room_code} not found"))
            elif c.startswith("leave"):
                if p.room is None:
                    await p.ws.send(FMsg.rc("You are not in a room"))
                    continue
                if p.room in rooms:
                    rid = p.room
                    remPlayerFromRoom(pid, rid)
                else:
                    remPlayerFromRoom(pid)
                    p.room = None
                await p.ws.send(FMsg.rc("Left room"))
        elif "r" in m:  # Room
            r = m["r"]
            if r.startswith("c:"):
                if p.room is None:
                    await p.ws.send(FMsg.rc("You are not in a room"))
                    continue
                if p.room not in rooms:
                    await p.ws.send(FMsg.rc("Room not found"))
                    continue
                msg = r[2:]
                await rooms[p.room].echo(FMsg.rc(msg, p.name), pid)
        elif "g" in m:  # Game
            g = m["g"]
            if g.startswith("start"):
                if p.room is None:
                    await p.ws.send(FMsg.rc("You are not in a room"))
                    continue
                if p.room not in rooms:
                    await p.ws.send(FMsg.rc("Room not found"))
                    continue
                if len(rooms[p.room].players) < 2:
                    await p.ws.send(FMsg.rc("Not enough players to start the game"))
                    continue
                asyncio.create_task(rooms[p.room].startGame())
            elif g.startswith("t:"):
                if p.room is None:
                    await p.ws.send(FMsg.rc("You are not in a room"))
                    continue
                await rooms[p.room].msgs.put((pid, "t", g[2:]))
            elif g.startswith("m:"):
                if p.room is None:
                    await p.ws.send(FMsg.rc("You are not in a room"))
                    continue
                await rooms[p.room].msgs.put((pid, "m", g[2:]))

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
    server = await websockets.serve(handler, "0.0.0.0", 8765)
    print("Server started")
    await server.wait_closed()

asyncio.run(main())