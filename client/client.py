#!/usr/bin/env python3
"""SecureMUD Textual client.

Four-panel terminal UI:

    +-----------------------+----------+
    |                       |  SCENE   |
    |                       +----------+
    |     NARRATIVE         |   MAP    |
    |                       +----------+
    |                       |  STATS   |
    +-----------------------+----------+
    | >                                |
    +----------------------------------+

Connects to the server over TLS, reads one JSON event per line, and
routes each event to the appropriate panel. The Narrative panel is the
only one with real content during Wave 1 of the UI revamp; Scene, Map,
and Stats are wired and will fill in once the server emits structured
'room' / 'stats' / 'map' / 'encounter' events.

Usage:  python3 client/client.py [host] [port] [--cafile path]

  --cafile PATH   Verify the server cert against this CA (recommended
                  for production). Without it, the client accepts the
                  self-signed cert without identity verification — the
                  channel is still encrypted, just not authenticated.

Install:  pip install textual cryptography
"""
import argparse
import asyncio
import json
import os
import ssl

from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Input, RichLog, Static


CSS = """
Screen {
    layout: grid;
    grid-size: 2 4;
    grid-columns: 2fr 1fr;
    grid-rows: 1fr 1fr 1fr 3;
}
#narrative {
    row-span: 3;
    border: round $accent;
    padding: 0 1;
}
#scene {
    border: round $secondary;
    content-align: center middle;
    color: $warning;
}
#map {
    border: round $secondary;
    content-align: center middle;
}
#stats {
    border: round $success;
    padding: 0 1;
}
#input {
    column-span: 2;
    border: tall $accent;
}
"""

ART_DIR = os.path.join(os.path.dirname(__file__), "..", "story", "art")


def load_art(key: str | None) -> str:
    """Read story/art/<key>.txt if it exists; otherwise a placeholder."""
    if not key:
        return ""
    path = os.path.join(ART_DIR, f"{key}.txt")
    if not os.path.isfile(path):
        return f"[ {key} ]"
    with open(path) as f:
        return f.read().rstrip()


def render_stats(ev: dict) -> str:
    """Format a 'stats' event into a compact status block."""
    def bar(cur: int, mx: int, width: int = 10) -> str:
        if not mx:
            return "░" * width
        filled = max(0, min(width, int(width * cur / mx)))
        return "█" * filled + "░" * (width - filled)

    name = ev.get("name", "?")
    lvl = ev.get("level", 1)
    hp, mhp = ev.get("hp", 0), ev.get("max_hp", 1)
    xp, xnxt = ev.get("xp", 0), ev.get("xp_next", 1)
    gold = ev.get("gold", 0)
    clan = ev.get("clan") or "—"
    weapon = ev.get("weapon") or "—"
    armor = ev.get("armor") or "—"
    return (
        f"{name}  Lvl {lvl}\n"
        f"HP {bar(hp, mhp)} {hp}/{mhp}\n"
        f"XP {bar(xp, xnxt)} {xp}/{xnxt}\n"
        f"\n"
        f"Gold {gold}\n"
        f"Clan {clan}\n"
        f"Weap {weapon}\n"
        f"Arm  {armor}"
    )


def render_map(ev: dict) -> str:
    """Format a 'map' event into a compass-style minimap."""
    title = ev.get("center_title", "?")
    exits = ev.get("exits", {}) or {}
    n = exits.get("north", "")
    s = exits.get("south", "")
    e = exits.get("east", "")
    w = exits.get("west", "")
    u = exits.get("up", "")
    d = exits.get("down", "")
    here = title[:14]
    extra = []
    if u:
        extra.append(f"↑ up:   {u}")
    if d:
        extra.append(f"↓ down: {d}")
    body = (
        f"     {n}\n"
        f"       ↑\n"
        f"{w:>7} ◆ {e}\n"
        f"       ↓\n"
        f"     {s}\n"
        f"\n"
        f"  {here}"
    )
    if extra:
        body += "\n" + "\n".join(extra)
    return body


class MUDClient(App):
    CSS = CSS
    TITLE = "SecureMUD"

    def __init__(self, host: str, port: int, cafile: str | None = None) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.cafile = cafile
        self.writer: asyncio.StreamWriter | None = None

    def compose(self) -> ComposeResult:
        yield RichLog(id="narrative", wrap=True, markup=False, auto_scroll=True)
        yield Static("(scene)", id="scene")
        yield Static("(map)", id="map")
        yield Static("(stats)", id="stats")
        yield Input(placeholder="register Hero hunter2", id="input")

    async def on_mount(self) -> None:
        narr = self.query_one("#narrative", RichLog)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if self.cafile:
            ctx.load_verify_locations(self.cafile)
        else:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            reader, self.writer = await asyncio.open_connection(
                self.host, self.port, ssl=ctx, server_hostname=self.host)
        except OSError as e:
            narr.write(f"[connection failed: {e}]")
            return
        narr.write(f"[connected to {self.host}:{self.port}]")
        self.query_one("#input", Input).focus()
        asyncio.create_task(self._reader_loop(reader))

    async def _reader_loop(self, reader: asyncio.StreamReader) -> None:
        narr = self.query_one("#narrative", RichLog)
        while True:
            try:
                line = await reader.readline()
            except OSError as e:
                narr.write(f"[read error: {e}]")
                return
            if not line:
                narr.write("[connection closed]")
                return
            try:
                event = json.loads(line.decode())
            except json.JSONDecodeError:
                # Server sent a non-JSON line — show it raw so we don't
                # silently drop content during protocol transition.
                narr.write(line.decode(errors="replace").rstrip())
                continue
            self._dispatch(event)

    def _dispatch(self, event: dict) -> None:
        t = event.get("type")
        if t == "narrate":
            # The engine still emits ANSI-colored strings; Text.from_ansi
            # turns them into rich markup so RichLog renders the colors
            # instead of printing the escape sequences as literal text.
            self.query_one("#narrative", RichLog).write(
                Text.from_ansi(event.get("text", "")))
        elif t == "error":
            self.query_one("#narrative", RichLog).write(
                Text.from_ansi(f"!! {event.get('text', '')}"))
        elif t == "stats":
            self.query_one("#stats", Static).update(render_stats(event))
        elif t == "map":
            self.query_one("#map", Static).update(render_map(event))
        elif t == "encounter":
            art = load_art(event.get("art"))
            self.query_one("#scene", Static).update(art or "(quiet)")
        elif t == "room":
            # Wave 1: rooms still arrive as narrate strings. When the
            # server starts emitting structured 'room' events this branch
            # will format them into the Narrative panel + trigger a map
            # refresh.
            self.query_one("#narrative", RichLog).write(event.get("text", str(event)))
        elif t == "quit":
            self.exit()
        else:
            # Unknown event type — surface it so server protocol bugs
            # are visible during development.
            self.query_one("#narrative", RichLog).write(f"[?{t}] {event}")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value
        event.input.value = ""
        if self.writer is None:
            return
        self.writer.write((text + "\n").encode())
        try:
            await self.writer.drain()
        except OSError:
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description="SecureMUD Textual client")
    ap.add_argument("host", nargs="?", default="127.0.0.1")
    ap.add_argument("port", nargs="?", type=int, default=4443)
    ap.add_argument("--cafile",
                    help="CA/cert file to verify server (else trust-on-first-use)")
    args = ap.parse_args()
    MUDClient(args.host, args.port, args.cafile).run()


if __name__ == "__main__":
    main()
