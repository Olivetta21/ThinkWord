import { WebSocketServer } from "ws";
import pg from "pg";
import dotenv from "dotenv";

dotenv.config();

const { Pool } = pg;
const pool = new Pool({ connectionString: process.env.DB_URL });

// ─────────────────────────────────────────────
// Error table
// ─────────────────────────────────────────────
const ERRORTABLE = {
  RNF: "Sala não encontrada",
  PNF: "Jogador não encontrado",
  NPN: "Você ainda não disse seu nome.",
  NIR: "Você não está em uma sala.",
  NEP: "Jogadores insuficientes.",
  PAR: "Você já está em uma sala.",
  RAC: "Essa sala já existe.",
  PNL: "Nome do jogador deve ter entre 4 e 10 caracteres.",
  RNL: "Nome da sala deve ter entre 8 e 20 caracteres.",
  PNO: "Você não é o dono da sala.",
  GAS: "Partida já em andamento.",
  NAU: "Nome já utilizado"
};

// ─────────────────────────────────────────────
// FMsg — message factories
// ─────────────────────────────────────────────
const FMsg = {
  errtable: () => JSON.stringify({ t: "errtable", e: ERRORTABLE }),

  error: (err) => {
    if (!(err in ERRORTABLE)) err = "UNK";
    return JSON.stringify({ t: "err", e: err });
  },

  rc: (msg, pid = -1) => JSON.stringify({ t: "rc", f: pid, m: msg }),

  rooms: () =>
    JSON.stringify({
      t: "rl",
      r: Object.fromEntries(
        Object.values(rooms).map((sala) => [
          sala.code,
          {
            players: [...sala.players].map((pid) => players[pid].name),
            started: sala.gameState !== 0,
          },
        ])
      ),
    }),

  enteringRoom: (roomCode) => JSON.stringify({ t: "er", r: roomCode }),

  gameLetters: (letters) => JSON.stringify({ t: "gl", l: letters }),

  gameState: (stateId, pidChosen = null) =>
    JSON.stringify({ t: "gs", s: stateId, p: pidChosen }),

  playerPoints: (pid, points) =>
    JSON.stringify({ t: "pp", p: pid, pts: points }),

  identity: (pname, pid, me = false) =>
    JSON.stringify({ t: me ? "id" : "pid", n: pname, p: pid }),

  playersIDs: (plist, ownerPid = null) => {
    console.log(`Players IDs: ${plist}`);
    return JSON.stringify({
      t: "pids",
      p: Object.fromEntries(plist.map((pid) => [pid, players[pid].name])),
      o: ownerPid,
    });
  },

  newOwner: (pid) => JSON.stringify({ t: "own", p: pid }),

  playerLeft: (pid) => JSON.stringify({ t: "pl", p: pid }),

  playerTyping: (msg) => JSON.stringify({ t: "pt", m: msg }),

  playerWord: (msg, result) => JSON.stringify({ t: "pw", m: msg, r: result }),
};

// ─────────────────────────────────────────────
// Player
// ─────────────────────────────────────────────
class Player {
  static serial = 0;
  static nextSerial() {
    return ++Player.serial;
  }

  constructor(ws, pid) {
    this.ws = ws;
    this.name = null;
    this.room = null;
    this.pid = pid;
  }
}

// ─────────────────────────────────────────────
// Async queue (mirrors asyncio.Queue)
// ─────────────────────────────────────────────
class AsyncQueue {
  constructor() {
    this._items = [];
    this._resolvers = [];
  }

  put(item) {
    if (this._resolvers.length > 0) {
      this._resolvers.shift()(item);
    } else {
      this._items.push(item);
    }
  }

  get(timeoutMs = null) {
    if (this._items.length > 0) {
      return Promise.resolve(this._items.shift());
    }
    return new Promise((resolve, reject) => {
      let timer;
      const resolver = (item) => {
        clearTimeout(timer);
        resolve(item);
      };
      this._resolvers.push(resolver);
      if (timeoutMs !== null) {
        timer = setTimeout(() => {
          const idx = this._resolvers.indexOf(resolver);
          if (idx !== -1) this._resolvers.splice(idx, 1);
          reject(new Error("TimeoutError"));
        }, timeoutMs);
      }
    });
  }

  empty() {
    return this._items.length === 0;
  }

  clear() {
    this._items = [];
  }
}

// ─────────────────────────────────────────────
// Room
// ─────────────────────────────────────────────
class Room {
  static serial = 0;
  static nextSerial() {
    return ++Room.serial;
  }

  constructor(code, rid) {
    this.msgs = new AsyncQueue();
    this.players = new Set();
    this.code = code;
    this.rid = rid;
    this.gameState = 0;
    this.stopRequested = false;
    this.dictionary = [];
    this.letters = "";
  }

  getOwnerPid() {
    return this.players.values().next().value ?? null;
  }

  sendPlayersState() {
    const plist = [...this.players];
    const ownerPid = this.getOwnerPid();
    const payload = FMsg.playersIDs(plist, ownerPid);
    for (const pid of this.players) {
      players[pid].ws.send(payload);
    }
  }

  async echo(msg, senderPid = null) {
    for (const pid of this.players) {
      if (pid !== senderPid) {
        players[pid].ws.send(msg);
      }
    }
  }

  addPlayer(pid) {
    const p = players[pid];
    this.players.add(pid);
    p.room = this.rid;
    p.ws.send(FMsg.enteringRoom(this.code));
    this.sendPlayersState();
    this.echo(FMsg.identity(p.name, pid), pid);
  }

  removePlayer(pid) {
    const previousOwnerPid = this.getOwnerPid();

    if (this.players.has(pid)) {
      players[pid].room = null;
    }
    this.players.delete(pid);
    this.echo(FMsg.playerLeft(pid));

    const newOwnerPid = this.getOwnerPid();
    if (newOwnerPid !== null && newOwnerPid !== previousOwnerPid) {
      players[newOwnerPid].ws.send(FMsg.newOwner(newOwnerPid));
    }

    this.sendPlayersState();

    if (this.players.size < 2) {
      this.stopGame();
    }

    return this.players.size === 0;
  }

  stopGame() {
    this.stopRequested = true;
    this.gameState = 0;
    this.msgs.clear();
    this.msgs.put([null, "__stop__", null]);
  }

  async setGameState(stateId, pidChosen = null) {
    this.gameState = stateId;
    await this.echo(FMsg.gameState(stateId, pidChosen));
  }

  async loadDictionary() {
    this.dictionary = [];
    this.letters = "";

    const minDictionarySize = 50;
    let tmpDictionary = [];

    while (tmpDictionary.length < minDictionarySize) {
      const minLen = 2;
      let maxLen = 4;

      const wordRes = await pool.query(
        "SELECT w FROM w_ptbr ORDER BY random() LIMIT 1"
      );
      const word = wordRes.rows[0].w;
      const wLen = word.length;

      if (wLen < maxLen) maxLen = wLen;

      const qntd = minLen + Math.floor(Math.random() * (maxLen - minLen + 1));
      const start = Math.floor(Math.random() * (wLen - qntd + 1));
      this.letters = word.slice(start, start + qntd);

      console.log(`Selected letters: ${this.letters} from word: ${word}`);

      const dictRes = await pool.query(
        `SELECT w FROM w_ptbr WHERE w LIKE '%${this.letters}%'`
      );
      this.letters = normalizar(this.letters);
      tmpDictionary = dictRes.rows.map((r) => normalizar(r.w));
    }

    this.dictionary = tmpDictionary;
    console.log(`Dictionary loaded with ${this.dictionary.length} words`);
  }

  async getLefterPlayer() {
    //pega o player mais da esquerda que entrou na sala:
    return this.players.values().next().value;
  }

  async startGame() {
    if (this.gameState !== 0) return;

    const partida = 10;
    let pIdx = -1;
    const playerPlayed = {};
    const wordsUsed = [];

    const allPlayersPlayed = () => {
      for (const pid of this.players) {
        if (!playerPlayed[pid] || playerPlayed[pid].played < partida) {
          return false;
        }
      }
      return true;
    };

    while (!allPlayersPlayed()) {
      if (this.stopRequested) break;

      await this.echo(FMsg.playerTyping(""));
      await this.setGameState(1); // Loading
      await this.loadDictionary();
      if (this.stopRequested) break;

      wordsUsed.push(this.letters);

      await this.setGameState(2); // Selecting player
      const playerList = [...this.players];

      if (pIdx + 1 < playerList.length) {
        pIdx += 1;
      } else {
        pIdx = 0;
      }

      const pid = playerList[pIdx];

      if (!playerPlayed[pid]) {
        playerPlayed[pid] = { played: 0, points: 0 };
      }
      playerPlayed[pid].played += 1;

      await this.setGameState(3, pid);
      await sleep(1000);
      if (this.stopRequested) break;

      await this.echo(FMsg.gameLetters(this.letters));

      this.msgs.clear();

      const inicio = Date.now();
      let acertou = false;

      while (Date.now() - inicio < 10000) {
        if (this.stopRequested) break;

        try {
          const remaining = 10000 - (Date.now() - inicio);
          const [pid_, type, msg] = await this.msgs.get(Math.min(100, remaining));

          if (this.stopRequested || type === "__stop__") {
            break;
          }

          if (pid_ === pid) {
            if (type === "t") {
              await this.echo(FMsg.playerTyping(msg), pid);
            } else if (type === "m") {
              const normalized = normalizar(msg);
              if (
                this.dictionary.includes(normalized) &&
                !wordsUsed.includes(normalized)
              ) {
                wordsUsed.push(normalized);

                let playerPoints = 1;
                if (this.letters.length > 3) playerPoints += 1;
                if (normalized.length > 7) playerPoints += 1;
                if (this.dictionary.length < 100) playerPoints += 1;

                acertou = true;
                playerPlayed[pid].points += playerPoints;
                await this.echo(FMsg.playerWord(normalized, 1));
                break;
              } else {
                await this.echo(FMsg.playerWord(normalized, 0));
              }
            }
          }
        } catch (e) {
          if (e.message === "TimeoutError") continue;
          throw e;
        }
      }

      if (this.stopRequested) break;

      if (!acertou) {
        playerPlayed[pid].points -= 1;
        await this.echo(FMsg.playerWord("Tempo esgotado", 0));
      }
      await this.echo(FMsg.playerPoints(pid, playerPlayed[pid].points));
    }

    await this.setGameState(0);
    this.stopRequested = false;
    this.letters = "";
    this.dictionary = [];
  }
}

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function normalizar(texto) {
    return texto
        .toLowerCase()
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '') // remove acentos
        .replace(/[^a-z0-9\s]/g, '')     // remove caracteres especiais
        .replace(/\s+/g, ' ')            // normaliza espaços múltiplos
        .trim();
}

function remPlayerFromRoom(pid, rid = null) {
  const roomsToDelete = [];

  if (rid !== null && rooms[rid]) {
    if (rooms[rid].removePlayer(pid)) {
      roomsToDelete.push(rid);
    }
  } else {
    for (const [rId, room] of Object.entries(rooms)) {
      if (room.players.has(pid)) {
        if (room.removePlayer(pid)) {
          roomsToDelete.push(rId);
        }
      }
    }
  }

  for (const rId of roomsToDelete) {
    delete rooms[rId];
    console.log(`Room ${rId} deleted`);
  }
}

function getClientIp(request) {
  const forwardedFor = request.headers["x-forwarded-for"];
  if (typeof forwardedFor === "string" && forwardedFor.length > 0) {
    return forwardedFor.split(",")[0].trim();
  }

  const realIp = request.headers["x-real-ip"];
  if (typeof realIp === "string" && realIp.length > 0) {
    return realIp.trim();
  }

  const remoteAddress = request.socket?.remoteAddress ?? "unknown";
  return remoteAddress.startsWith("::ffff:") ? remoteAddress.slice(7) : remoteAddress;
}

// ─────────────────────────────────────────────
// State
// ─────────────────────────────────────────────
const players = {};
const rooms = {};

// ─────────────────────────────────────────────
// Message handler
// ─────────────────────────────────────────────
async function hpMessages(pid, ws) {
  ws.on("message", async (raw) => {
    const m = JSON.parse(raw.toString());
    const p = players[pid];
    console.log(`${pid}:`, m);

    //O Jogador está no menu inicial:
    if ("c" in m) {
      const c = m.c;

      if (c === "ping") {
        ws.send(FMsg.rc("pong"));
      }
      
      else if (c.startsWith("me:")) {
        const name = c.slice(3);
        if (name.length < 4 || name.length > 10) { ws.send(FMsg.error("PNL")); return; }
        if (Object.values(players).some((p) => p.name === name)) { ws.send(FMsg.error("NAU")); return; }
        p.name = name;
        ws.send(FMsg.identity(p.name, pid, true));
      }
      
      else if (c === "rooms") {
        ws.send(FMsg.rooms());
      }
      
      else if (c.startsWith("create:")) {
        if (p.name === null) { ws.send(FMsg.error("NPN")); return; }
        if (p.room !== null) { ws.send(FMsg.error("PAR")); return; }
        const roomCode = c.slice(7);
        if (roomCode.length < 8 || roomCode.length > 20) {
          ws.send(FMsg.error("RNL")); return;
        }
        if (Object.values(rooms).some((r) => r.code === roomCode)) {
          ws.send(FMsg.error("RAC")); return;
        }
        const rid = Room.nextSerial();
        rooms[rid] = new Room(roomCode, rid);
        rooms[rid].addPlayer(pid);
        ws.send(FMsg.rc(`Room(${rid}) ${roomCode} created`));
      }
      
      else if (c.startsWith("join:")) {
        if (p.name === null) { ws.send(FMsg.error("NPN")); return; }
        if (p.room !== null) { ws.send(FMsg.error("PAR")); return; }
        const roomCode = c.slice(5);
        let found = false;
        for (const [, room] of Object.entries(rooms)) {
          if (room.code === roomCode) {
            if (room.gameState !== 0) { ws.send(FMsg.error("GAS")); return; }
            room.addPlayer(pid);
            ws.send(FMsg.rc("Entrou na sala"));
            found = true;
            break;
          }
        }
        if (!found) ws.send(FMsg.error("RNF"));
      }
      
      else if (c.startsWith("errtable")) {
        ws.send(FMsg.errtable());
      }
    }
    
    //O Jogador está dentro de uma sala:
    else if ("r" in m) {
      const r = m.r;
      if (p.room === null) { ws.send(FMsg.error("NIR")); return; }
      if (!rooms[p.room]) { ws.send(FMsg.error("RNF")); return; }

      if (r.startsWith("c:")) {
        if (r.length > 50) return;
        const msg = r.slice(2);
        if (!msg) return;
        await rooms[p.room].echo(FMsg.rc(msg, pid), pid);
      }
      
      else if (r.startsWith("leave")) {
        remPlayerFromRoom(pid, p.room);
        ws.send(FMsg.enteringRoom(-1));
      }
    }
    
    //O Jogador está jogando:
    else if ("g" in m) {
      const g = m.g;
      if (p.room === null) { ws.send(FMsg.error("NIR")); return; }
      if (!rooms[p.room]) { ws.send(FMsg.error("RNF")); return; }

      if (g.startsWith("start")) {
        if (await rooms[p.room].getLefterPlayer() !== pid) { ws.send(FMsg.error("PNO")); return; }
        if (rooms[p.room].players.size < 2) { ws.send(FMsg.error("NEP")); return; }
        if (rooms[p.room].gameState !== 0) { ws.send(FMsg.error("GAS")); return; }
        rooms[p.room].startGame().catch(console.error);
      } else if (g.startsWith("stop")) {
        if (await rooms[p.room].getLefterPlayer() !== pid) { ws.send(FMsg.error("PNO")); return; }
        rooms[p.room].stopGame();
      } else if (g.startsWith("t:")) {
        if (g.length > 50) return;
        rooms[p.room].msgs.put([pid, "t", g.slice(2)]);
      } else if (g.startsWith("m:")) {
        if (g.length > 50) return;
        rooms[p.room].msgs.put([pid, "m", g.slice(2)]);
      } else if (g.startsWith("gs")) {
        ws.send(FMsg.gameState(rooms[p.room].gameState));
      }
    }
  });
}

// ─────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────
const wss = new WebSocketServer({ host: "0.0.0.0", port: 8085 });

wss.on("connection", (ws, request) => {
  const clientIp = getClientIp(request);
  const pid = Player.nextSerial();
  players[pid] = new Player(ws, pid);
  console.log(`Player ${pid} connected from ${clientIp}`);

  hpMessages(pid, ws).catch(console.error);

  ws.on("close", () => {
    remPlayerFromRoom(pid);
    delete players[pid];
    console.log(`Player ${pid} disconnected`);
  });

  ws.on("error", (err) => {
    console.error(`Player ${pid} error:`, err);
  });
});

console.log("Server started");