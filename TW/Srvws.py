import asyncio
import websockets
import random
import json
import psycopg
from dotenv import load_dotenv
import os

load_dotenv()
db = None
DB_URL = os.getenv("DB_URL")

def getDB():
    global db
    if db is None or db.closed:
        db = psycopg.connect(DB_URL)
    return db

ERRORTABLE = {
    "RNF": "Sala não encontrada",
    "PNF": "Jogador não encontrado",
    "NPN": "Você ainda não disse seu nome.",
    "NIR": "Você não está em uma sala.",
    "NEP": "Jogadores insuficientes.",
    "PAR": "Você já está em uma sala.",
    "RAC": "Essa sala já existe.",
    "PNL": "Nome do jogador deve ter entre 4 e 10 caracteres.",
    "RNL": "Nome da sala deve ter entre 8 e 20 caracteres.",
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
    
    def enteringRoom(room_code):
        return json.dumps({
            "t": "er",
            "r": room_code
        })
    
    def gameLetters(letters):
        return json.dumps({
            "t": "gl",
            "l": letters
        })

    def gameState(state_id, pid_chosen=None):
        return json.dumps({
            "t": "gs",
            "s": state_id,
            "p": pid_chosen
        })
    
    def playerPoints(pid, points):
        return json.dumps({
            "t": "pp",
            "p": pid,
            "pts": points
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
        self.dictionary = []
        self.letters = ""

    async def echo(self, msg, sender_pid=None):
        for pid in self.players:
            if pid != sender_pid:
                await players[pid].ws.send(msg)

    def addPlayer(self, pid):
        p = players[pid]
        self.players.add(pid)
        p.room = self.rid
        asyncio.create_task(p.ws.send(FMsg.enteringRoom(self.code))) # Notifica o jogador que entrou na sala
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

    async def loadDictionary(self):
        self.dictionary = []
        self.letters = ""
        db = getDB()

        min_dictionary_size = 50
        tmp_dictionary = []

        with db.cursor() as cur:
            while len(tmp_dictionary) < min_dictionary_size: # O dicionario deve ter pelo menos 50 palavras
                min_len = 2
                max_len = 4
                cur.execute("select w from w_ptbr order by random() limit 1;")
                word = cur.fetchone()[0]
                w_len = len(word)

                if w_len < max_len:
                    max_len = w_len

                qntd = random.randint(min_len, max_len)
                start = random.randint(0, w_len - qntd)            
                self.letters = word[start:start + qntd]

                print(f"Selected letters: {self.letters} from word: {word}")
                cur.execute(f"SELECT w FROM w_ptbr where w like '%{self.letters}%';")

                tmp_dictionary = [row[0] for row in cur.fetchall()]
        self.dictionary = tmp_dictionary
        print(f"Dictionary loaded with {len(self.dictionary)} words")

    async def startGame(self):
        if self.gameState != 0:
            return
        partida = 30
        p_idx = -1
        player_played = {}
        words_used = []

        def allPlayersPlayed():
            for pid in self.players:
                if pid not in player_played or player_played[pid]["played"] < partida:
                    return False
            return True

        while not allPlayersPlayed():
            await self.echo(FMsg.playerTyping(""))
            await self.setGameState(1) # Loading
            await self.loadDictionary()
            words_used.append(self.letters) # Não pode usar as letras escolhidas na rodada
            #await asyncio.sleep(1)
            await self.setGameState(2) # Selecting player
            player_list = list(self.players)
            if p_idx + 1 < len(player_list):
                p_idx += 1
            else:
                p_idx = 0
            pid = player_list[p_idx]

            if pid not in player_played:
                player_played[pid] = {
                    "played": 0,
                    "points": 0
                }
            player_played[pid]["played"] += 1
            #await asyncio.sleep(5)
            await self.setGameState(3, pid)
            await asyncio.sleep(1)
            await self.echo(FMsg.gameLetters(self.letters))

            while not self.msgs.empty():
                await self.msgs.get()

            inicio = asyncio.get_event_loop().time()
            acertou = False
            while asyncio.get_event_loop().time() - inicio < 10:
                try:
                    pid_, type, msg = await asyncio.wait_for(self.msgs.get(), timeout=0.1)
                    if pid_ == pid:
                        if type == "t": # Player typing
                            await self.echo(FMsg.playerTyping(msg), pid)
                        elif type == "m": # Player message
                            msg = msg.strip().upper()
                            if msg in self.dictionary and msg not in words_used:
                                words_used.append(msg)

                                # Logica Pontuação
                                player_points = 1
                                if len(self.letters) > 3:
                                    player_points += 1
                                if len(msg) > 7:
                                    player_points += 1
                                if len(self.dictionary) < 100:
                                    player_points += 1

                                acertou = True
                                player_played[pid]["points"] += player_points
                                await self.echo(FMsg.playerWord(msg, 1))
                                break
                            else:
                                await self.echo(FMsg.playerWord(msg, 0))
                except asyncio.TimeoutError:
                    continue
            if not acertou:
                player_played[pid]["points"] -= 1
                await self.echo(FMsg.playerWord("Tempo esgotado", 0))
            await self.echo(FMsg.playerPoints(pid, player_played[pid]["points"]))
        await self.setGameState(0)
        self.letters = ""
        self.dictionary = []


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
                if len(p.name) < 4 or len(p.name) > 10:
                    await p.ws.send(FMsg.error("PNL"))
                    continue
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
                if len(room_code) < 8 or len(room_code) > 20:
                    await p.ws.send(FMsg.error("RNL"))
                    continue
                if room_code in [room.code for room in rooms.values()]:
                    await p.ws.send(FMsg.error("RAC"))
                    continue
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
                if not msg:
                    continue
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
                await p.ws.send(FMsg.enteringRoom(-1))  # Notifica o jogador que saiu da sala
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