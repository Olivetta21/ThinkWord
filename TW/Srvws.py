import asyncio
import websockets
import random
import json

ERRORTABLE = {
    "RNF": "Room not found",
    "PNF": "Player not found",
    "NPN": "No player name",
    "NIR": "Not in room",
    "NEP": "Not enough players",
    "PAR": "Player already in room",
}

class FMsg:
    def errtable():
        return json.dumps({
            "t": "errtable",
            "e": ERRORTABLE
        })

    def error(err):
        if err not in ERRORTABLE:
            err = "UNK"

        return json.dumps({
            "t": "err",
            "e": err
        })

    def rc(msg, pid=-1):
        return json.dumps({
            "t": "rc",
            "f": pid,
            "m": msg
        })
    
    def rooms():
        return json.dumps({
            "t": "rl",
            "r": {sala.code: [players[pid].name for pid in sala.players] for sala in rooms.values()}
        })

    def gameState(state_id, pid_chosen=None):
        return json.dumps({
            "t": "gs",
            "s": state_id,
            "p": pid_chosen
        })
    
    def identity(pname, pid, me=False):
        return json.dumps({
            "t": "id" if me else "pid",
            "n": pname,
            "p": pid
        })
    
    def playersIDs(plist):
        print(f"Players IDs: {plist}")
        return json.dumps({
            "t": "pids",
            "p": {pid: players[pid].name for pid in plist}
        })
    
    def playerLeft(pid):
        return json.dumps({
            "t": "pl",
            "p": pid
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

    def __init__(self, ws, pid):
        self.ws = ws
        self.name = None
        self.room = None
        self.pid = pid

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
        self.gameState = 0

    async def echo(self, msg, sender_pid=None):
        for pid in self.players:
            if pid != sender_pid:
                await players[pid].ws.send(msg)

    def addPlayer(self, pid):
        p = players[pid]
        self.players.add(pid)
        p.room = self.rid
        asyncio.create_task(p.ws.send(FMsg.playersIDs(list(self.players)))) # Envia os jogados ja conectados
        asyncio.create_task(self.echo(FMsg.identity(p.name, pid), pid)) # Envia o novo jogador para os outros


    def removePlayer(self, pid):
        if pid in self.players:
            players[pid].room = None
        self.players.discard(pid)
        asyncio.create_task(self.echo(FMsg.playerLeft(pid))) # Notifica os outros jogadores da saída
        return len(self.players) == 0

    async def setGameState(self, state_id, pid_chosen=None):
        self.gameState = state_id
        await self.echo(FMsg.gameState(state_id, pid_chosen))

    async def startGame(self):
        partida = 3
        p_idx = -1
        player_played = {}

        def allPlayersPlayed():
            for pid in self.players:
                if pid not in player_played or player_played[pid] < partida:
                    return False
            return True

        while not allPlayersPlayed():
            await self.echo(FMsg.playerTyping(""))
            await self.setGameState(1) # Loading
            await asyncio.sleep(2)
            await self.setGameState(2) # Selecting player
            await asyncio.sleep(2)

            player_list = list(self.players)
            p_total = len(player_list)
            if p_idx + 1 < p_total:
                p_idx += 1
            else:
                p_idx = 0
            pid = player_list[p_idx]
            p = players[pid]

            if pid not in player_played:
                player_played[pid] = 0
            player_played[pid] += 1

            await asyncio.sleep(5)
            await self.setGameState(3, pid)

            while not self.msgs.empty():
                await self.msgs.get()

            inicio = asyncio.get_event_loop().time()
            acertou = False
            while asyncio.get_event_loop().time() - inicio < 10:
                try:
                    acertou = False
                    pid_, type, msg = await asyncio.wait_for(self.msgs.get(), timeout=0.1)
                    if pid_ == pid:
                        if type == "t": # Player typing
                            await self.echo(FMsg.playerTyping(msg), pid)
                        elif type == "m": # Player message
                            # Logica de acerto mock
                            if msg.startswith("a"):
                                acertou = True
                                await self.echo(FMsg.playerWord(msg, 1))
                                break
                            else:
                                await self.echo(FMsg.playerWord(msg, 0))
                except asyncio.TimeoutError:
                    continue
            if not acertou:
                await self.echo(FMsg.playerWord("Tempo esgotado", 0))
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
                await p.ws.send(FMsg.identity(p.name, pid, True))
            elif c == "rooms":
                await p.ws.send(FMsg.rooms())
            elif c.startswith("create:"):
                if p.name is None:
                    await p.ws.send(FMsg.error("NPN"))
                    continue
                if p.room is not None:
                    await p.ws.send(FMsg.error("PAR"))
                    continue
                room_code = c[7:]
                rid = Room.nextSerial()
                rooms[rid] = Room(room_code, rid)
                rooms[rid].addPlayer(pid)
                await p.ws.send(FMsg.rc(f"Room({rid}) {room_code} created"))
            elif c.startswith("join:"):
                if p.name is None:
                    await p.ws.send(FMsg.error("NPN"))
                    continue
                if p.room is not None:
                    await p.ws.send(FMsg.error("PAR"))
                    continue
                room_code = c[5:]
                found = False
                for rid, room in rooms.items():
                    if room.code == room_code:
                        room.addPlayer(pid)
                        await p.ws.send(FMsg.rc("Entrou na sala"))
                        found = True
                        break
                if not found:
                    await p.ws.send(FMsg.error("RNF"))
            elif c.startswith("errtable"):
                await p.ws.send(FMsg.errtable())
        elif "r" in m:  # Room
            r = m["r"]
            if r.startswith("c:"):
                if p.room is None:
                    await p.ws.send(FMsg.error("NIR"))
                    continue
                if p.room not in rooms:
                    await p.ws.send(FMsg.error("RNF"))
                    continue
                msg = r[2:]
                await rooms[p.room].echo(FMsg.rc(msg, pid), pid)
            elif r.startswith("leave"):
                if p.room is None:
                    await p.ws.send(FMsg.error("NIR"))
                    continue
                if p.room in rooms:
                    rid = p.room
                    remPlayerFromRoom(pid, rid)
                else:
                    remPlayerFromRoom(pid)
                    p.room = None
                await p.ws.send(FMsg.rc("Left room"))
        elif "g" in m:  # Game
            g = m["g"]
            if g.startswith("start"):
                if p.room is None:
                    await p.ws.send(FMsg.error("NIR"))
                    continue
                if p.room not in rooms:
                    await p.ws.send(FMsg.error("RNF"))
                    continue
                if len(rooms[p.room].players) < 2:
                    await p.ws.send(FMsg.error("NEP"))
                    continue
                asyncio.create_task(rooms[p.room].startGame())
            elif g.startswith("t:"):
                if p.room is None:
                    await p.ws.send(FMsg.error("NIR"))
                    continue
                await rooms[p.room].msgs.put((pid, "t", g[2:]))
            elif g.startswith("m:"):
                if p.room is None:
                    await p.ws.send(FMsg.error("NIR"))
                    continue
                await rooms[p.room].msgs.put((pid, "m", g[2:]))
            elif g.startswith("gs"):
                if p.room is None:
                    await p.ws.send(FMsg.error("NIR"))
                    continue
                if p.room not in rooms:
                    await p.ws.send(FMsg.error("RNF"))
                    continue
                await p.ws.send(FMsg.gameState(rooms[p.room].gameState))

async def handler(ws):
    pid = Player.nextSerial()
    players[pid] = Player(ws, pid)

    try:
        print(f"Player {pid} connected")
        await hp_messages(pid)
    finally:
        remPlayerFromRoom(pid)
        del players[pid]
        print(f"Player {pid} disconnected")

async def main():
    server = await websockets.serve(handler, "0.0.0.0", 15765)
    print("Server started")
    await server.wait_closed()

asyncio.run(main())