"""Game engine: holds live state, parses commands, runs combat & ticks.

Thread-safe via a single global lock held during command execution.

Emits structured events (see server/protocol.py) for the panel-aware client:
  - `stats`     after every successful command (auto, in handle())
  - `room`      after look / move / death / flee — refreshes Narrative + Map
  - `encounter` on attack / talk / scene-clear   — refreshes Scene panel

Commands still return ANSI-colored narrative strings for the Narrative
panel. The auto-stats emission keeps the Stats panel in sync without
sprinkling refresh calls through every state-mutating command.
"""
import random, time, threading, copy
from world import ROOMS, ITEMS, MOBS, QUESTS, CLANS, STORYLINE
import models

C = {  # ANSI colors
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[31m", "grn": "\033[32m", "yel": "\033[33m",
    "blu": "\033[34m", "mag": "\033[35m", "cyn": "\033[36m", "wht": "\033[37m",
}
def col(s, c): return f"{C[c]}{s}{C['reset']}"

class MobInstance:
    def __init__(self, mob_id):
        self.id = mob_id
        m = MOBS[mob_id]
        self.name = m["name"]
        self.hp = m["hp"]; self.max_hp = m["hp"]
        self.dead_until = 0
    @property
    def alive(self): return self.hp > 0 and time.time() >= self.dead_until

class Session:
    """A connected client."""
    def __init__(self, send_fn):
        self.send = send_fn          # callable(str | dict) — str auto-wrapped as narrate
        self.player = None
        self.in_combat = None        # (room_id, mob_id)

class Engine:
    def __init__(self):
        self.lock = threading.RLock()
        self.players = models.load_players()
        self.world = models.load_world_state()
        self.sessions = {}           # player_name -> Session
        self.room_mobs = {}          # room_id -> [MobInstance]
        for rid, r in ROOMS.items():
            self.room_mobs[rid] = [MobInstance(mid) for mid in r.get("mobs", [])]
        threading.Thread(target=self._ticker, daemon=True).start()

    # ---------------- structured event constructors ----------------
    def stats_event(self, p):
        """Snapshot of player stats for the Stats panel."""
        return {
            "type": "stats",
            "name": p.name,
            "level": p.level,
            "hp": p.hp, "max_hp": p.effective_max_hp,
            "atk": p.atk, "defense": p.defense,
            "xp": p.xp, "xp_next": models.xp_for_level(p.level),
            "gold": p.gold,
            "clan": CLANS[p.clan]["name"] if p.clan else None,
            "weapon": ITEMS[p.equipped["weapon"]]["name"] if p.equipped["weapon"] else None,
            "armor": ITEMS[p.equipped["armor"]]["name"] if p.equipped["armor"] else None,
        }

    def room_event(self, p):
        """Snapshot of the player's current room — used by Narrative + Map."""
        r = ROOMS[p.room]
        live = [m for m in self.room_mobs[p.room] if m.alive]
        others = [n for n, s in self.sessions.items()
                  if s.player and s.player.room == p.room and n != p.name]
        return {
            "type": "room",
            "id": p.room,
            "title": r["title"],
            "desc": r["desc"],
            # exit values are destination room *titles* so the Map can label them
            "exits": {d: ROOMS[dest]["title"] for d, dest in r["exits"].items()},
            "mobs": [{"id": m.id, "name": m.name,
                      "boss": bool(MOBS[m.id].get("boss")),
                      "npc": bool(MOBS[m.id].get("npc"))}
                     for m in live],
            "others": others,
            "shop": "shop" in r,
            "storyline": STORYLINE[self.world["storyline"]] if p.room == "town_square" else None,
        }

    def encounter_event(self, art, caption=None):
        """Scene-panel update. art=None clears the scene."""
        return {"type": "encounter", "art": art, "caption": caption}

    # ---------------- broadcasting ----------------
    def broadcast_room(self, room_id, msg, exclude=None):
        for name, s in self.sessions.items():
            if s.player and s.player.room == room_id and name != exclude:
                s.send("\n" + msg)
    def broadcast_all(self, msg):
        for s in self.sessions.values():
            if s.player:
                s.send("\n" + col(msg, "mag"))

    # ---------------- background tick ----------------
    def _ticker(self):
        while True:
            time.sleep(5)
            with self.lock:
                for s in self.sessions.values():
                    p = s.player
                    if p and not s.in_combat and p.hp < p.effective_max_hp:
                        p.hp = min(p.effective_max_hp, p.hp + max(1, p.level))
                models.save_players(self.players)
                models.save_world_state(self.world)

    # ---------------- auth ----------------
    def authenticate(self, session, name, password, register):
        name = name.strip()[:16]
        if not name.isalnum():
            return False, "Name must be alphanumeric."
        if register:
            if name in self.players:
                return False, "That name is taken."
            self.players[name] = models.create_player(name, password)
            models.save_players(self.players)
        else:
            p = self.players.get(name)
            if not p or not models.verify_pw(password, p.pw_hash):
                return False, "Invalid name or password."
        if name in self.sessions:
            return False, "That character is already logged in."
        session.player = self.players[name]
        self.sessions[name] = session
        return True, None

    def disconnect(self, session):
        with self.lock:
            if session.player:
                self.broadcast_room(session.player.room,
                                    col(f"{session.player.name} fades away.", "dim"),
                                    exclude=session.player.name)
                self.sessions.pop(session.player.name, None)
                models.save_players(self.players)

    # ---------------- command dispatch ----------------
    def handle(self, session, line):
        with self.lock:
            try:
                out = self._dispatch(session, line)
            except Exception as e:
                return col(f"[error] {e}", "red")
            # Single point of stats emission — keeps the panel in sync
            # without scattering refresh calls through every cmd_*.
            if session.player and out != "__QUIT__":
                session.send(self.stats_event(session.player))
            return out

    def _dispatch(self, session, line):
        p = session.player
        parts = line.strip().split()
        if not parts:
            return ""
        cmd, args = parts[0].lower(), parts[1:]
        aliases = {"n":"north","s":"south","e":"east","w":"west","u":"up","d":"down",
                   "l":"look","i":"inventory","k":"attack","sc":"score"}
        cmd = aliases.get(cmd, cmd)
        fn = getattr(self, f"cmd_{cmd}", None)
        if not fn:
            return col("Unknown command. Type 'help'.", "red")
        return fn(p, session, args)

    # ---------------- commands ----------------
    def cmd_help(self, p, s, a):
        return col("Commands:\n", "cyn") + (
            "  look | go <dir> (n/s/e/w/u/d) | score | inventory\n"
            "  attack <mob> | flee | get <item> | drop <item>\n"
            "  equip <item> | unequip <weapon|armor> | use <item>\n"
            "  buy <item> | sell <item> | shop\n"
            "  quests | accept | turnin | talk <npc>\n"
            "  clans | join <clan> | clan\n"
            "  say <msg> | who | map | help | quit")

    def cmd_look(self, p, s, a):
        # The Narrative panel renders the room from the structured event;
        # no need to send a separate text version.
        s.send(self.room_event(p))
        return ""

    def cmd_go(self, p, s, a):
        if s.in_combat:
            return col("You're in combat! Type 'flee' first.", "red")
        if not a: return "Go where?"
        d = a[0].lower()
        d = {"n":"north","s":"south","e":"east","w":"west","u":"up","d":"down"}.get(d, d)
        exits = ROOMS[p.room]["exits"]
        if d not in exits:
            return col("You can't go that way.", "red")
        self.broadcast_room(p.room, col(f"{p.name} leaves {d}.", "dim"), exclude=p.name)
        p.room = exits[d]
        self.broadcast_room(p.room, col(f"{p.name} arrives.", "dim"), exclude=p.name)
        s.send(self.encounter_event(None))   # leaving the previous scene
        s.send(self.room_event(p))
        return ""

    def cmd_north(self,p,s,a): return self.cmd_go(p,s,["north"])
    def cmd_south(self,p,s,a): return self.cmd_go(p,s,["south"])
    def cmd_east(self,p,s,a):  return self.cmd_go(p,s,["east"])
    def cmd_west(self,p,s,a):  return self.cmd_go(p,s,["west"])
    def cmd_up(self,p,s,a):    return self.cmd_go(p,s,["up"])
    def cmd_down(self,p,s,a):  return self.cmd_go(p,s,["down"])

    def cmd_score(self, p, s, a):
        # Stats panel auto-refreshes after every command; this readout
        # remains for the Narrative scrollback.
        clan = CLANS[p.clan]["name"] if p.clan else "None"
        nxt = models.xp_for_level(p.level)
        return col(f"\n{p.name}  (Level {p.level})\n", "bold") + (
            f"  HP:    {p.hp}/{p.effective_max_hp}\n"
            f"  ATK:   {p.atk}    DEF: {p.defense}\n"
            f"  XP:    {p.xp}/{nxt}\n"
            f"  Gold:  {p.gold}\n"
            f"  Clan:  {clan}\n"
            f"  Weapon:{ITEMS[p.equipped['weapon']]['name'] if p.equipped['weapon'] else '-'}\n"
            f"  Armor: {ITEMS[p.equipped['armor']]['name'] if p.equipped['armor'] else '-'}")

    def cmd_inventory(self, p, s, a):
        if not p.inventory: return "Your pack is empty."
        lines = [col("Inventory:", "cyn")]
        for iid, n in p.inventory.items():
            lines.append(f"  {ITEMS[iid]['name']} x{n}")
        return "\n".join(lines)

    # ---- combat ----
    def _find_mob(self, p, name):
        for m in self.room_mobs[p.room]:
            if not m.alive:
                continue
            if name in m.id.replace("_", " ") or name in m.name.lower():
                return m
        return None

    def cmd_attack(self, p, s, a):
        if not a: return "Attack what?"
        target = " ".join(a).lower()
        m = self._find_mob(p, target)
        if not m:
            return col("No such target here.", "red")
        if MOBS[m.id].get("npc"):
            return col("That's not something you can fight.", "yel")
        if m.id == "ember_lich" and p.quests.get("slay_the_lich") != "active":
            return col("An unseen force repels you. (Accept the quest 'Slay the Ember Lich' first.)", "mag")
        s.in_combat = (p.room, m.id)
        s.send(self.encounter_event(m.id))
        return self._combat_round(p, s, m)

    def _combat_round(self, p, s, m):
        out = []
        dmg = max(1, p.atk - MOBS[m.id]["defn"] + random.randint(-2, 2))
        m.hp -= dmg
        out.append(col(f"You hit {m.name} for {dmg}. ({max(0,m.hp)}/{m.max_hp})", "grn"))
        if m.hp <= 0:
            return self._kill_mob(p, s, m, out)
        mdmg = max(1, MOBS[m.id]["atk"] - p.defense + random.randint(-2, 2))
        p.hp -= mdmg
        out.append(col(f"{m.name} hits you for {mdmg}. (HP {max(0,p.hp)}/{p.effective_max_hp})", "red"))
        if p.hp <= 0:
            return self._die(p, s, out)
        out.append(col("(attack again, or flee)", "dim"))
        return "\n".join(out)

    def _kill_mob(self, p, s, m, out):
        md = MOBS[m.id]
        s.in_combat = None
        m.hp = 0
        m.dead_until = time.time() + md["respawn"] if md["respawn"] else float("inf")
        out.append(col(f"You have slain {m.name}!", "bold"))
        gold = int(random.randint(*md["gold"]) * p.gold_multiplier())
        p.gold += gold
        out.append(col(f"You loot {gold} gold.", "yel"))
        for xpmsg in p.gain_xp(md["xp"]):
            out.append(col(xpmsg, "mag"))
        for iid, chance in md["loot"].items():
            if random.random() < chance:
                p.inventory[iid] = p.inventory.get(iid, 0) + 1
                out.append(col(f"You obtain {ITEMS[iid]['name']}.", "yel"))
        if md.get("boss") and m.id == "ember_lich":
            self.broadcast_all(f"{p.name} has slain the Ember Lich! The realm trembles.")
        self.broadcast_room(p.room, col(f"{p.name} slays {m.name}!", "dim"), exclude=p.name)
        s.send(self.encounter_event(None))   # scene clears once the target is down
        return "\n".join(out)

    def _die(self, p, s, out):
        s.in_combat = None
        lost = p.gold // 2
        p.gold -= lost
        p.hp = p.effective_max_hp
        p.room = "town_square"
        out.append(col(f"\nYou have died! You lose {lost} gold and wake in Emberhold.", "red"))
        s.send(self.encounter_event(None))
        s.send(self.room_event(p))   # respawn relocates the player; refresh Map
        return "\n".join(out)

    def cmd_flee(self, p, s, a):
        if not s.in_combat: return "You're not fighting."
        s.in_combat = None
        exits = list(ROOMS[p.room]["exits"].values())
        p.room = random.choice(exits)
        s.send(self.encounter_event(None))
        s.send(self.room_event(p))
        return col("You flee!", "yel")

    # ---- items ----
    def cmd_get(self, p, s, a):
        return col("There are no loose items to pick up here.", "dim")
    def cmd_drop(self, p, s, a):
        if not a: return "Drop what?"
        iid = self._match_item(p.inventory, " ".join(a))
        if not iid: return "You don't have that."
        p.inventory[iid] -= 1
        if p.inventory[iid] <= 0: del p.inventory[iid]
        return f"You drop {ITEMS[iid]['name']}."

    def _match_item(self, pool, name):
        name = name.lower()
        for iid in pool:
            if name in ITEMS[iid]["name"].lower() or name == iid:
                return iid
        return None

    def cmd_equip(self, p, s, a):
        if not a: return "Equip what?"
        iid = self._match_item(p.inventory, " ".join(a))
        if not iid: return "You don't have that."
        t = ITEMS[iid]["type"]
        if t not in ("weapon", "armor"):
            return "You can't equip that."
        p.equipped[t] = iid
        return col(f"You equip {ITEMS[iid]['name']}.", "grn")

    def cmd_unequip(self, p, s, a):
        if not a or a[0] not in ("weapon", "armor"): return "Unequip weapon or armor?"
        p.equipped[a[0]] = None
        return f"You unequip your {a[0]}."

    def cmd_use(self, p, s, a):
        if not a: return "Use what?"
        iid = self._match_item(p.inventory, " ".join(a))
        if not iid: return "You don't have that."
        it = ITEMS[iid]
        if it["type"] != "consumable":
            return "You can't use that."
        heal = int(it.get("heal", 0) * p.heal_multiplier())
        p.hp = min(p.effective_max_hp, p.hp + heal)
        p.inventory[iid] -= 1
        if p.inventory[iid] <= 0: del p.inventory[iid]
        return col(f"You use {it['name']} and recover {heal} HP. (HP {p.hp}/{p.effective_max_hp})", "grn")

    # ---- shop ----
    def cmd_shop(self, p, s, a):
        r = ROOMS[p.room]
        if "shop" not in r: return "There's no shop here."
        lines = [col("For sale:", "cyn")]
        for iid in r["shop"]:
            it = ITEMS[iid]
            lines.append(f"  {it['name']:<16} {it['price']:>5} gold  - {it['desc']}")
        return "\n".join(lines)

    def cmd_buy(self, p, s, a):
        r = ROOMS[p.room]
        if "shop" not in r: return "There's no shop here."
        iid = self._match_item({i:1 for i in r["shop"]}, " ".join(a))
        if not iid: return "Not sold here."
        price = ITEMS[iid]["price"]
        if p.gold < price: return col("You can't afford that.", "red")
        p.gold -= price
        p.inventory[iid] = p.inventory.get(iid, 0) + 1
        return col(f"You buy {ITEMS[iid]['name']} for {price} gold.", "grn")

    def cmd_sell(self, p, s, a):
        iid = self._match_item(p.inventory, " ".join(a))
        if not iid: return "You don't have that."
        price = max(1, ITEMS[iid]["price"] // 2)
        p.inventory[iid] -= 1
        if p.inventory[iid] <= 0: del p.inventory[iid]
        p.gold += price
        return col(f"You sell {ITEMS[iid]['name']} for {price} gold.", "yel")

    # ---- quests ----
    def cmd_talk(self, p, s, a):
        live = [m for m in self.room_mobs[p.room] if m.alive and MOBS[m.id].get("npc")]
        if not live:
            return "There's no one here to talk to."
        npc = live[0]
        s.send(self.encounter_event(npc.id))
        offer = self._current_quest_offer(p)
        if offer is None:
            return col('The hooded figure nods. "You have done all I asked... for now."', "mag")
        q = QUESTS[offer]
        return col(f'Hooded figure: "{q["desc"]}"', "mag") + \
               col(f"\n(type 'accept' to take '{q['name']}')", "dim")

    def _current_quest_offer(self, p):
        chain = ["cull_the_pack", "goblin_menace", "slay_the_lich"]
        for qid in chain:
            st = p.quests.get(qid)
            if st is None:
                idx = chain.index(qid)
                if idx == 0 or p.quests.get(chain[idx-1]) == "done":
                    return qid
                return None
            if st == "active":
                return qid
        return None

    def cmd_accept(self, p, s, a):
        qid = self._current_quest_offer(p)
        if not qid or p.quests.get(qid) == "active":
            return "No new quest to accept right now."
        p.quests[qid] = "active"
        return col(f"Quest accepted: {QUESTS[qid]['name']}", "grn")

    def cmd_turnin(self, p, s, a):
        if not any(MOBS[m.id].get("npc") for m in self.room_mobs[p.room] if m.alive):
            return "You must be with the quest giver (the tavern)."
        for qid, st in p.quests.items():
            if st != "active": continue
            q = QUESTS[qid]
            if all(p.inventory.get(i, 0) >= n for i, n in q["needs"].items()):
                for i, n in q["needs"].items():
                    p.inventory[i] -= n
                    if p.inventory[i] <= 0: del p.inventory[i]
                p.gold += q["reward_gold"]
                msgs = [col(f"Quest complete: {q['name']}!", "bold"),
                        col(f"Reward: {q['reward_gold']} gold, {q['reward_xp']} XP.", "yel")]
                msgs += [col(m, "mag") for m in p.gain_xp(q["reward_xp"])]
                if q.get("reward_item"):
                    p.inventory[q["reward_item"]] = p.inventory.get(q["reward_item"], 0) + 1
                    msgs.append(col(f"You receive {ITEMS[q['reward_item']]['name']}.", "yel"))
                p.quests[qid] = "done"
                if q.get("storyline"):
                    self.world["storyline"] = q["storyline"]
                    self.broadcast_all(STORYLINE[q["storyline"]])
                return "\n".join(msgs)
            else:
                need = ", ".join(f"{n}x {ITEMS[i]['name']}" for i, n in q["needs"].items())
                return col(f"You still need: {need}", "yel")
        return "You have no active quests to turn in."

    def cmd_quests(self, p, s, a):
        if not p.quests: return "You have no quests. Visit the tavern and 'talk'."
        lines = [col("Quests:", "cyn")]
        for qid, st in p.quests.items():
            q = QUESTS[qid]
            mark = col("✓", "grn") if st == "done" else col("…", "yel")
            lines.append(f"  {mark} {q['name']} - {q['desc']}")
        return "\n".join(lines)

    # ---- clans ----
    def cmd_clans(self, p, s, a):
        lines = [col("Clans of Emberhold:", "cyn")]
        for cid, c in CLANS.items():
            lines.append(f"  {c['sigil']} {c['name']:<18} {col(c['bonus'],'yel')} - {c['desc']}")
        lines.append(col("Join with: join <ironbound|ashveil|verdant>", "dim"))
        return "\n".join(lines)

    def cmd_join(self, p, s, a):
        if p.room != "guildhall":
            return col("You must be in the Hall of Clans (guildhall) to join.", "red")
        if not a: return "Join which clan?"
        cid = a[0].lower()
        if cid not in CLANS: return "No such clan."
        if p.clan: return f"You already belong to {CLANS[p.clan]['name']}."
        p.clan = cid
        self.broadcast_all(f"{p.name} has joined {CLANS[cid]['name']}!")
        return col(f"You pledge to {CLANS[cid]['name']}. Bonus: {CLANS[cid]['bonus']}", "grn")

    def cmd_clan(self, p, s, a):
        if not p.clan: return "You have no clan. See 'clans'."
        members = [n for n, sess in self.sessions.items()
                   if sess.player and sess.player.clan == p.clan]
        c = CLANS[p.clan]
        return col(f"{c['sigil']} {c['name']}\n", "bold") + \
               f"  Bonus: {c['bonus']}\n  Online members: {', '.join(members)}"

    # ---- social ----
    def cmd_say(self, p, s, a):
        if not a: return "Say what?"
        msg = " ".join(a)
        self.broadcast_room(p.room, col(f"{p.name} says: {msg}", "wht"), exclude=p.name)
        return col(f"You say: {msg}", "wht")

    def cmd_who(self, p, s, a):
        lines = [col("Online:", "cyn")]
        for n, sess in self.sessions.items():
            pl = sess.player
            clan = f" <{CLANS[pl.clan]['name']}>" if pl.clan else ""
            lines.append(f"  {n} (Lvl {pl.level}){clan}")
        return "\n".join(lines)

    def cmd_map(self, p, s, a):
        return col(
            "         [Market]\n"
            "             |\n"
            "[Guildhall]-[Square]-[Gate]-[Wilds]-[Deep Wilds]\n"
            "             |                  |\n"
            "         [Tavern]          [Ruins]-[Crypt]\n"
            f"\nYou are in: {ROOMS[p.room]['title']}", "cyn")

    def cmd_quit(self, p, s, a):
        return "__QUIT__"
