"""Wire protocol for SecureMUD.

Server -> Client: one JSON object per line, terminated with '\\n'.
Each object has a 'type' field plus type-specific payload. The client
routes events to its panels by type.

Client -> Server: still raw text lines (commands). The engine parses
these as it always has — structuring this direction would just add
complexity for no benefit in a command-line MUD.

Wave 1: only NARRATE and ERROR are emitted; the engine still returns
ANSI-colored strings from cmd_* and those get wrapped into NARRATE
events by Client.send. Future waves add ROOM, STATS, MAP, ENCOUNTER,
COMBAT, and PROMPT so the dedicated panels can come alive.
"""
import json


class T:
    NARRATE   = "narrate"     # text into the Narrative panel
    ROOM      = "room"        # entered/looked at a room (title, desc, exits, mobs)
    STATS     = "stats"       # full or partial player stats refresh
    MAP       = "map"         # minimap update (current room + neighbors)
    ENCOUNTER = "encounter"   # scene-panel art swap (mob_id, npc_id, or null)
    COMBAT    = "combat"      # combat round result
    ERROR     = "error"       # an error to display prominently
    PROMPT    = "prompt"      # server is waiting for a specific input mode
    QUIT      = "quit"        # server is closing the connection


def encode(event: dict) -> bytes:
    """Serialize a single event to a UTF-8 framed line."""
    return (json.dumps(event, separators=(",", ":")) + "\n").encode()


# Constructors — keep call sites readable.

def narrate(text: str) -> dict:
    return {"type": T.NARRATE, "text": text}


def error(text: str) -> dict:
    return {"type": T.ERROR, "text": text}


def prompt(kind: str) -> dict:
    return {"type": T.PROMPT, "kind": kind}


def quit_() -> dict:
    return {"type": T.QUIT}
