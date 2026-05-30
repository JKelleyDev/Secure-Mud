"""Story content loader.

Reads story/*.txt and exposes ROOMS / ITEMS / MOBS / QUESTS / CLANS /
STORYLINE dicts in the same shape the engine has always consumed. The
creative writer edits story/*.txt — this file only handles parsing.

File format (RFC822-ish):
    [type: id]
    field: value
    field: first part of a multi-line value
      continuation of the previous field (leading whitespace)

Blank lines and lines starting with '#' are ignored. Section types are
room | item | mob | quest | clan | storyline.
"""
import copy
import os
import re

STORY_DIR = os.path.join(os.path.dirname(__file__), "..", "story")

# ---------------- field coercion ----------------
def _exits(s):
    return dict(pair.strip().split("=") for pair in s.split(",") if "=" in pair)

def _idlist(s):
    return [x.strip() for x in s.split(",") if x.strip()]

def _range(s):
    a, b = s.split("-")
    return (int(a), int(b))

def _droptable(s):
    if not s.strip():
        return {}
    out = {}
    for pair in s.split(","):
        k, v = pair.strip().split("=")
        out[k.strip()] = float(v)
    return out

def _needs(s):
    if not s.strip():
        return {}
    out = {}
    for pair in s.split(","):
        k, v = pair.strip().split("=")
        out[k.strip()] = int(v)
    return out

def _bool(s):
    return s.strip().lower() in ("true", "yes", "1")

# field name -> coercer, per section type. Anything not listed stays a string.
SCHEMAS = {
    "room":  {"exits": _exits, "mobs": _idlist, "shop": _idlist},
    "item":  {"atk": int, "defn": int, "heal": int, "price": int},
    "mob":   {"hp": int, "atk": int, "defn": int, "xp": int,
              "gold": _range, "loot": _droptable, "respawn": int,
              "boss": _bool, "npc": _bool},
    "quest": {"needs": _needs, "reward_gold": int, "reward_xp": int},
    "clan":  {},
    "storyline": {},
}

# fields that must exist on every entry of a type, with their default value
DEFAULTS = {
    "mob": {"loot": {}},
}

SECTION_RE = re.compile(r"^\[(\w+):\s*(\w+)\]\s*$")
FIELD_RE = re.compile(r"^(\w+):\s*(.*)$")


def _parse_file(path):
    """Yield (section_type, section_id, raw_fields_dict) tuples."""
    stype = sid = None
    fields = {}
    current = None
    with open(path) as f:
        for raw in f:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                current = None
                continue
            m = SECTION_RE.match(line)
            if m:
                if stype:
                    yield stype, sid, fields
                stype, sid = m.group(1), m.group(2)
                fields = {}
                current = None
                continue
            if line[0].isspace() and current:
                fields[current] += " " + stripped
                continue
            m = FIELD_RE.match(line)
            if m:
                current = m.group(1)
                fields[current] = m.group(2)
    if stype:
        yield stype, sid, fields


def _coerce(stype, fields):
    schema = SCHEMAS.get(stype, {})
    out = {}
    for k, v in fields.items():
        out[k] = schema[k](v) if k in schema else v.strip()
    for k, default in DEFAULTS.get(stype, {}).items():
        out.setdefault(k, copy.deepcopy(default))
    return out


def load_story(story_dir=STORY_DIR):
    rooms, items, mobs, quests, clans = {}, {}, {}, {}, {}
    storyline = {}
    buckets = {"room": rooms, "item": items, "mob": mobs,
               "quest": quests, "clan": clans}
    if not os.path.isdir(story_dir):
        raise FileNotFoundError(f"Story directory missing: {story_dir}")
    for fname in sorted(os.listdir(story_dir)):
        if not fname.endswith(".txt"):
            continue
        for stype, sid, raw in _parse_file(os.path.join(story_dir, fname)):
            data = _coerce(stype, raw)
            if stype == "storyline":
                storyline[sid] = data.get("desc", "")
            elif stype in buckets:
                buckets[stype][sid] = data
            else:
                raise ValueError(f"Unknown section type [{stype}: {sid}] in {fname}")
    return rooms, items, mobs, quests, clans, storyline


ROOMS, ITEMS, MOBS, QUESTS, CLANS, STORYLINE = load_story()
