# SecureMUD

A terminal-played, TLS-secured Multi-User Dungeon. Unlike classic MUDs that used
plaintext telnet, **all gameplay traffic is encrypted** (TLS 1.2+, negotiates 1.3).
The world has a living storyline, an economy, quests, clans, stats, and levels,
and persists to disk so progress survives restarts.

## Quick start

```bash
pip install cryptography textual    # cryptography (server); textual (client UI)

# 1. Start the server (generates server.crt/server.key on first run)
python3 server/server.py                 # listens on 0.0.0.0:4443

# 2. Connect a client (in another terminal / another machine)
python3 client/client.py <host> 4443
```

The client is a full-screen [Textual](https://textual.textualize.io/) TUI —
a four-panel layout (narrative · scene art · map · stats) with an input
bar at the bottom. The server speaks a JSON-line protocol (see
`server/protocol.py`); a raw `nc` connection will see JSON, not prose.

Override host/port with env vars: `MUD_HOST=0.0.0.0 MUD_PORT=4443 python3 server/server.py`

## First moves

```
register Hero hunter2          # create an account (PBKDF2-hashed password)
look                           # see the room
south  → talk → accept         # get your first quest from the hooded figure
help                           # full command list
```

## Project layout

```
.
├── server/                 # game backend (run server.py)
│   ├── server.py           # TLS socket listener + per-client thread
│   ├── engine.py           # command dispatch, combat, ticks, broadcast
│   ├── models.py           # Player class, persistence, password hashing
│   ├── protocol.py         # JSON event types sent to the client
│   └── world.py            # story content loader (DO NOT edit by hand)
├── client/
│   └── client.py           # Textual TUI client (4-panel layout)
├── story/                  # creative-writer territory — plain text
│   ├── rooms.txt           # rooms, exits, region tags, shop assignments
│   ├── items.txt           # weapons, armor, consumables, quest objects
│   ├── mobs.txt            # monsters and NPCs (stats, loot, respawn)
│   ├── quests.txt          # quest definitions, rewards, chain links
│   ├── clans.txt           # clan names, sigils, lore (mechanics in code)
│   └── storyline.txt       # world phases / ambient narrative
├── data/                   # runtime state (auto-created)
│   ├── players.json        # accounts, inventory, levels (saved every tick)
│   └── world_state.json    # current storyline phase, clan control
├── test_integration.py     # smoke test of the full server/client flow
├── Dockerfile              # production container image (see Deployment)
├── fly.toml                # Fly.io deploy config (see Deployment)
├── CLAUDE.md → AGENTS.md   # project intent / model handoff notes
└── README.md               # this file
```

## Security architecture

- **Transport:** every client connection is wrapped in TLS (`ssl.SSLContext`,
  `minimum_version = TLSv1_2`). No game bytes ever cross the wire in clear text.
- **Identity:** self-signed cert auto-generated on first boot. For production,
  drop in a real cert (`server.crt`/`server.key`) and have clients verify with
  `client.py --cafile server.crt`.
- **Credentials:** passwords stored as PBKDF2-HMAC-SHA256 (100k iterations,
  per-user salt); verified with constant-time compare. Never stored or
  transmitted in plaintext.
- **Concurrency:** one thread per client; all world mutation runs under a
  single re-entrant lock, so state stays consistent.

## Game systems

| System | Details |
|---|---|
| **Stats & levels** | HP, ATK, DEF; XP curve `100 * level^1.5`; level-ups raise HP/ATK |
| **Combat** | Turn-based `attack`/`flee`; loot tables; death costs gold + respawn in town |
| **Economy** | Gold from kills/quests; buy/sell at shops; sell value = ½ buy price |
| **Quests** | Branching chain (`cull_the_pack → goblin_menace → slay_the_lich`) |
| **Clans** | Ironbound (+HP), Ashveil (+gold), Verdant (+healing); join at the guildhall |
| **Adaptive story** | Slaying the Ember Lich flips the world into a new phase, broadcast to all players |
| **Persistence** | Players + world state saved to `data/*.json` every tick |
| **Multiplayer** | Room presence, `say`, `who`, realm-wide event broadcasts |

## Command reference

```
Movement  n/s/e/w/u/d · look · map · score · inventory
Combat    attack <mob> · flee · use <item> · equip/unequip
Economy   shop · buy <item> · sell <item> · get/drop
Progress  talk · accept · turnin · quests
Clans     clans · join <clan> · clan
Social    say <msg> · who · help · quit
```

---

# For the creative writer — adding content

You can build out the entire game by editing files in `story/`. **No Python
required.** The server re-reads `story/*.txt` every time it boots.

Every entry uses this shape:

```
[type: unique_id]
field: value
another_field: value
desc: text that can span multiple lines.
  Indented continuation lines are joined onto the previous field.
```

Blank lines separate entries. Lines starting with `#` are comments. Section
types are `room`, `item`, `mob`, `quest`, `clan`, `storyline`.

### Adding a room

In `story/rooms.txt`:

```
[room: river_crossing]
title: The Old River Crossing
region: wilds
exits: west=deep_wilds, east=eastern_road
mobs: dire_wolf
desc: Black water slips silently between mossy stones. The bridge has
  long since collapsed; only the foundations remain.
```

Then link it from another room's `exits` (e.g. add `east=river_crossing` to
`deep_wilds`). Any mob id you list must exist in `mobs.txt`; any item id you
add to `shop:` must exist in `items.txt`.

### Adding an item

In `story/items.txt`:

```
[item: silver_dagger]
name: silver dagger
type: weapon            # weapon | armor | consumable | quest | misc
atk: 12                 # weapons use atk; armor uses defn; consumables use heal
price: 180
desc: Thin and cold. Hums faintly near the undead.
```

Sell price is always `price // 2`. To sell it in a shop, add the id to that
room's `shop:` line.

### Adding a mob or NPC

In `story/mobs.txt`:

```
[mob: river_troll]
name: a river troll
hp: 90
atk: 18
defn: 7
xp: 70
gold: 30-60
loot: troll_hide=0.5, silver_dagger=0.1
respawn: 120
```

For a boss, add `boss: true` (it gets a `[BOSS]` tag and a realm-wide
broadcast on death). For a non-combat NPC, add `npc: true` (it becomes
talkable but can't be attacked; use `hp: 9999, atk: 0, defn: 999, respawn: 0`).

### Adding a quest

In `story/quests.txt`:

```
[quest: river_crossing]
name: Bridge the River
giver: river_elder
needs: troll_hide=2, silver_dagger=1
reward_gold: 200
reward_xp: 250
reward_item: leather_armor
next: northern_pass
desc: Slay two river trolls and bring me a silver dagger. Then we can
  rebuild the bridge.
```

`needs:` items are consumed on turn-in. Players turn in by being in the same
room as the NPC who gave the quest and typing `turnin`.

**Hooking the quest into the offer chain.** The engine currently advertises
quests in this hardcoded order (see `_current_quest_offer` in `engine.py`):

```python
chain = ["cull_the_pack", "goblin_menace", "slay_the_lich"]
```

To insert your new quest into this chain, edit that list. Quests outside the
chain still load and can be turned in — they just aren't auto-offered on `talk`.

**Branching the story.** Add `storyline: <phase_id>` to a quest to flip the
world into that phase on turn-in. The phase id must exist in `storyline.txt`.

### Adding a clan (lore side)

In `story/clans.txt`:

```
[clan: stoneblood]
name: The Stoneblood
sigil: ◈
bonus: +1 DEF per level
desc: Mountain-dwellers, slow to anger and slower to die.
```

This makes the clan **joinable and visible** in `clans` listings, but the
bonus text is decorative — it doesn't do anything until a developer wires
up the math (see "Adding a new clan bonus" below).

### Adding a storyline phase

In `story/storyline.txt`:

```
[storyline: second_dawn]
desc: A new sun rises over the ashes. Something hungry watches from the
  shadow of the broken temple.
```

Then point a quest at it (`storyline: second_dawn`). When that quest is
turned in, every online player sees the new phase broadcast in the town
square, and the world remembers (it's saved to `data/world_state.json`).

---

# For developers — class reference

All game classes live under `server/`. The codebase is intentionally small
and class-based; subclass anywhere you need richer behavior.

## `Player` — `server/models.py`

The persistent character record. Loaded from `data/players.json` at boot,
saved every tick.

**Key state**

| field | purpose |
|---|---|
| `name`, `pw_hash` | identity; `pw_hash` is PBKDF2-HMAC-SHA256 with per-user salt |
| `level`, `xp` | progression; level-up curve is `100 * level**1.5` |
| `max_hp`, `hp`, `base_atk`, `base_def` | raw stats before gear/clan modifiers |
| `gold` | currency |
| `room` | current room id |
| `inventory` | `{item_id: count}` |
| `equipped` | `{"weapon": item_id_or_None, "armor": item_id_or_None}` |
| `clan` | one of the clan ids in `story/clans.txt`, or `None` |
| `quests` | `{quest_id: "active"|"done"}` |

**Derived properties**

- `atk` — base + equipped weapon + level
- `defense` — base + equipped armor
- `effective_max_hp` — applies the Ironbound clan bonus
- `gold_multiplier()` — Ashveil bonus
- `heal_multiplier()` — Verdant bonus

**How to extend**

- **Add a new stat (e.g. `mana`).** Add it to `__init__` (default value), and to
  any derived property (e.g. `effective_max_mana`). Existing saved players
  will load fine because `from_dict` only copies what's present; new fields
  pick up the default until the player triggers a save.
- **Add a new clan bonus.** Add a method or property on `Player` (mirror the
  shape of `gold_multiplier`), then call it from the engine where the effect
  applies. Update `story/clans.txt` so it's visible in `clans`.
- **Subclass into character classes.** Create `Warrior(Player)`, `Mage(Player)`,
  override `atk`, add `cast_spell()`, etc. You'd also need to extend
  `create_player()` and `from_dict()` to choose which subclass to instantiate
  (store a `class` field on the player record).

## `Engine` — `server/engine.py`

The single, thread-safe game brain. Holds live state (players, sessions,
world phase, per-room mob instances), runs the background tick (HP regen +
persistence), and dispatches commands.

**Key state**

| field | purpose |
|---|---|
| `lock` | `RLock` held during every command — keeps mutations atomic |
| `players` | `{name: Player}` — same dict that `models.save_players` writes |
| `sessions` | `{name: Session}` — online players only |
| `room_mobs` | `{room_id: [MobInstance, ...]}` — live spawn state |
| `world` | `{"storyline": "rising", "clan_control": {}}` — persisted phase state |

**Command dispatch**

Every command is a method named `cmd_<name>` with signature
`(self, player, session, args) -> str`. The dispatcher (`_dispatch`)
lower-cases the first token, resolves aliases (`n` → `north`, `k` → `attack`,
etc.), then calls `cmd_<resolved>`. Return value is sent back to the player.

**How to extend**

- **Add a new command.** Define a `cmd_<name>` method on `Engine`. That's it —
  it's auto-discovered. Add the alias if you want a shortcut.
  ```python
  def cmd_pray(self, p, s, a):
      p.hp = p.effective_max_hp
      return col("You feel restored.", "grn")
  ```
- **Add a new mob behavior.** Subclass `MobInstance` (e.g. `BossMob`) and have
  `Engine.__init__` instantiate it for entries with `boss: true`. Override
  `_combat_round` logic by adding hooks (e.g. `mob.on_hit(player)`).
- **Add a new background tick.** Append work inside `_ticker()` under the
  lock. Keep it cheap — it runs every 5 seconds for every connected player.
- **Add a broadcast scope.** Mirror `broadcast_room` / `broadcast_all` for
  e.g. `broadcast_region` (iterate sessions whose room's `region` matches).

## `Session` — `server/engine.py`

A connected client's *gameplay* state. One per logged-in player.

| field | purpose |
|---|---|
| `send` | callable that writes a line to the socket |
| `player` | the `Player` object the session is driving (None before login) |
| `in_combat` | `(room_id, mob_id)` tuple or `None` |

Subclass if you need per-connection state (e.g. spell cooldowns, idle
timers). Update `handle_client` in `server.py` to instantiate your subclass.

## `MobInstance` — `server/engine.py`

The live, per-room instance of a mob defined in `mobs.txt`. The static stats
(hp ceiling, atk, loot table) live in `MOBS`; this class tracks the current
HP and respawn timestamp.

| field | purpose |
|---|---|
| `id` | mob id matching an entry in `mobs.txt` |
| `name` | display name (copied from the mob entry) |
| `hp`, `max_hp` | current and ceiling |
| `dead_until` | unix timestamp when the mob will be `alive` again |

Subclass for special-case enemies — see "Add a new mob behavior" above.

## `Client` — `server/server.py`

A thin buffered wrapper around the raw TLS socket. One per connected client.
Reads line-delimited input, sends UTF-8 output, swallows broken-pipe errors.
You rarely need to touch this; it's the network plumbing.

---

# Extending the engine — common recipes

### Adding a command

1. Add `cmd_<name>(self, p, s, a)` to `Engine` in `server/engine.py`.
2. (Optional) Add an alias in the `aliases` dict inside `_dispatch`.
3. (Optional) Document it in the `cmd_help` string.

### Adding a clan bonus

1. Add the lore entry in `story/clans.txt`.
2. Add the multiplier/method on `Player` in `server/models.py` (e.g.
   `def crit_multiplier(self): return 1.25 if self.clan == "stoneblood" else 1.0`).
3. Call it from the relevant place in `Engine` (e.g. in `_combat_round` when
   computing damage).

### Adding a new item type

The engine recognizes `weapon`, `armor`, `consumable`, `quest`, `misc`.
To add a type (e.g. `scroll`):

1. Define items with `type: scroll` in `story/items.txt` plus any new fields
   you need (e.g. `effect: fireball`).
2. Add the field name to the `SCHEMAS["item"]` block in `server/world.py` if
   it needs coercion (e.g. `int`, list, etc.).
3. Handle the new type wherever the engine branches on `ITEMS[iid]["type"]`
   (search for `"type"` in `engine.py` — primarily `cmd_equip` and `cmd_use`).

### Adding a new entity kind to story files

Want `[npc_dialogue: ...]` or `[weather: ...]` entries? Three steps in
`server/world.py`:

1. Add the type to `SCHEMAS` (and `DEFAULTS` if needed).
2. Add a bucket dict in `load_story` and export it.
3. Import the new dict from `engine.py` (or wherever consumes it).

---

# Deployment

SecureMUD is a long-lived TLS socket server, not an HTTP app — it needs a
host that supports persistent TCP on a custom port. The recommended setup
for portfolio / friends-only scale is two free services:

| Component     | Host    | Notes                              |
|---------------|---------|------------------------------------|
| Game server   | Fly.io  | Docker container, TCP on :4443     |
| Landing page  | Vercel  | Static or Next.js, install + docs  |
| Domain        | optional | ~$10/yr; not required to start    |

**Do NOT** try to host the game server on Vercel / Netlify / Cloudflare
Workers / AWS Lambda. Those are HTTP-only and time-capped (≤300s) —
SecureMUD needs persistent TCP and an always-on background tick.

### Game server on Fly.io

The repo includes `Dockerfile` and `fly.toml`. The Dockerfile installs
`cryptography`, copies `server/` and `story/`, and runs the server on
port 4443. The fly.toml exposes TCP/4443 (TLS terminates inside Python,
not at Fly's edge) and mounts a 1GB volume at `/app/data` for player
state.

```bash
brew install flyctl                                    # or curl -L https://fly.io/install.sh | sh
fly auth signup                                        # or fly auth login
# edit fly.toml: change app = "securemud" to a unique name
fly apps create <your-app-name>
fly volumes create mud_data --size 1 --region ord
fly deploy
fly logs                                               # tail server output
fly status                                             # machine + volume info
```

Once deployed the server is reachable at `<your-app-name>.fly.dev:4443`.
Players connect with:

```bash
python3 client/client.py <your-app-name>.fly.dev 4443 --cafile server.crt
```

**Cert persistence caveat.** `server/server.py` currently writes the
self-signed cert to `/app/server.crt`, which is *not* on the Fly volume.
After the first deploy the cert exists; on the next `fly deploy` the
image is rebuilt without the cert, the server regenerates a new one, and
any client pinned to the old cert will fail TLS verification. Two ways
to fix:

1. **Cert on the volume** (recommended). Change the `CERT` / `KEY` paths
   at the top of `server/server.py` from `..` to `os.path.join("..", "data")`
   so they resolve to `/app/data/server.crt` (on the volume) instead of
   `/app/server.crt` (in the ephemeral image).
2. **Cert baked into the image.** Generate the cert once locally with
   `openssl req -x509 ...`, commit it to the repo, and skip
   `ensure_cert()` in production. Less hassle long-term; commits a public
   cert (not the private key — `*.key` is gitignored).

**Scaling caveat.** The engine holds shared state in memory under a
single re-entrant lock; multiple Fly machines would diverge. Keep
`min_machines_running = 1` until you migrate to the Postgres backend
(see roadmap below).

### Landing page on Vercel

A single Next.js page (or even a static HTML file) is enough. Put it in
a `landing/` subdir to keep it separate from the server code, and host
the `server.crt` file so clients can download and trust it without
having to clone the whole repo.

Suggested page content:
- One-paragraph pitch (TLS-secured MUD, encrypted, classic terminal feel)
- Copy-pasteable connect block:
  ```bash
  curl -O https://<your-landing>.vercel.app/server.crt
  git clone https://github.com/JKelleyDev/Secure-Mud.git
  cd Secure-Mud
  python3 client/client.py <your-app-name>.fly.dev 4443 --cafile ../server.crt
  ```
- "First moves" example (`register`, `look`, `south → talk → accept`)
- Command reference (re-use the table above)
- Link to the GitHub repo

Deploy with:
```bash
npm i -g vercel
cd landing
vercel              # interactive: links the project
vercel --prod       # promotes to production
```

The free tier covers more traffic than any portfolio site will see, and
supports custom domains if you grab one later.

---

# Roadmap: persistence layer

The current persistence is JSON files in `data/` (one for players, one for
world state). Per `CLAUDE.md` the target is **Postgres + SQLAlchemy + Alembic**.

Migration plan when the time comes:

1. Add `sqlalchemy` and `alembic` to `requirements.txt`.
2. Create `server/db.py` with the engine/session factory.
3. Mirror the `Player` dataclass with a SQLAlchemy ORM model (same field
   names — the existing `to_dict`/`from_dict` helpers become unnecessary).
4. Replace `models.load_players` / `save_players` with ORM queries.
5. Replace `models.load_world_state` / `save_world_state` with a key/value
   table (or a single-row config table).
6. `alembic init alembic`, write the first migration, point at the database
   URL via env var.
7. Write a one-shot importer that reads existing `data/*.json` into the DB.

The story content stays in `story/*.txt`. The DB layer only owns mutable
player + world state.

---

# Story file format reference

```
# Comments start with '#'. Blank lines separate entries.

[<type>: <id>]                # type = room|item|mob|quest|clan|storyline
field: value                  # one field per line
another: value, value, value  # comma lists for shop/mobs/loot/needs
desc: First line of the description.
  Continuation lines start with whitespace; they're joined onto the
  previous field with a single space.
```

**Type-specific field coercion** (defined in `server/world.py:SCHEMAS`):

| section | field | parsed as |
|---|---|---|
| room | `exits` | `{dir: room_id}` (`north=foo, east=bar`) |
| room | `mobs`, `shop` | list of ids |
| item | `atk`, `defn`, `heal`, `price` | int |
| mob | `hp`, `atk`, `defn`, `xp`, `respawn` | int |
| mob | `gold` | tuple (`2-6`) |
| mob | `loot` | `{item_id: chance}` (`wolf_pelt=0.8`) |
| mob | `boss`, `npc` | bool (`true`/`yes`/`1`) |
| quest | `needs` | `{item_id: count}` (`wolf_pelt=3`) |
| quest | `reward_gold`, `reward_xp` | int |

Anything not listed stays a string. Adding a new typed field means adding
its name and coercer to `SCHEMAS` in `server/world.py`.
