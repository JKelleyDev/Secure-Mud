"""Player model + persistence + derived stat math."""
import json, os, time, hashlib, secrets

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
PLAYERS_FILE = os.path.join(DATA_DIR, "players.json")
WORLD_FILE = os.path.join(DATA_DIR, "world_state.json")

def _hash_pw(password, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return salt + "$" + h.hex()

def verify_pw(password, stored):
    salt = stored.split("$", 1)[0]
    return secrets.compare_digest(_hash_pw(password, salt), stored)

def xp_for_level(level):
    return int(100 * (level ** 1.5))

class Player:
    def __init__(self, name, pw_hash):
        self.name = name
        self.pw_hash = pw_hash
        self.level = 1
        self.xp = 0
        self.max_hp = 50
        self.hp = 50
        self.base_atk = 5
        self.base_def = 0
        self.gold = 25
        self.room = "town_square"
        self.inventory = {}          # item_id -> count
        self.equipped = {"weapon": None, "armor": None}
        self.clan = None
        self.quests = {}             # quest_id -> "active"|"done"
        self.created = time.time()

    # ---- derived stats (clan bonuses applied here) ----
    @property
    def atk(self):
        from world import ITEMS
        w = self.equipped["weapon"]
        return self.base_atk + (ITEMS[w]["atk"] if w else 0) + self.level
    @property
    def defense(self):
        from world import ITEMS
        a = self.equipped["armor"]
        return self.base_def + (ITEMS[a]["defn"] if a else 0)
    @property
    def effective_max_hp(self):
        if self.clan == "ironbound":
            return int(self.max_hp * 1.10)
        return self.max_hp

    def gold_multiplier(self):
        return 1.15 if self.clan == "ashveil" else 1.0
    def heal_multiplier(self):
        return 1.20 if self.clan == "verdant" else 1.0

    def gain_xp(self, amount):
        self.xp += amount
        msgs = []
        while self.xp >= xp_for_level(self.level):
            self.xp -= xp_for_level(self.level)
            self.level += 1
            self.max_hp += 12
            self.base_atk += 2
            self.hp = self.effective_max_hp
            msgs.append(f"*** You reached level {self.level}! Max HP and ATK increased. ***")
        return msgs

    def to_dict(self):
        d = self.__dict__.copy()
        return d
    @classmethod
    def from_dict(cls, d):
        p = cls(d["name"], d["pw_hash"])
        p.__dict__.update(d)
        return p

# ------------------------- persistence -------------------------
def _ensure():
    os.makedirs(DATA_DIR, exist_ok=True)

def load_players():
    _ensure()
    if not os.path.exists(PLAYERS_FILE):
        return {}
    with open(PLAYERS_FILE) as f:
        raw = json.load(f)
    return {n: Player.from_dict(d) for n, d in raw.items()}

def save_players(players):
    _ensure()
    tmp = PLAYERS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({n: p.to_dict() for n, p in players.items()}, f, indent=2)
    os.replace(tmp, PLAYERS_FILE)

def create_player(name, password):
    return Player(name, _hash_pw(password))

def load_world_state():
    _ensure()
    if os.path.exists(WORLD_FILE):
        with open(WORLD_FILE) as f:
            return json.load(f)
    return {"storyline": "rising", "clan_control": {}}

def save_world_state(state):
    _ensure()
    tmp = WORLD_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, WORLD_FILE)
