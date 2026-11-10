"""Which machine answers the owner's phone: the home PC or the laptop (owner, 2026-11-10).

One small shared note, `phone.json`, lives in the brain-sync folder in OneDrive (next to the lean brain zip). It names
the machine that answers the phone. That machine is the JARVIS primary (Telegram, routines, calls, it pushes the
brain); every other machine is the standby (quiet, it pulls the brain). Without the note, each machine keeps the
role in its own role file, as before (T105).

Handing over never gives two answers:
  1. The machine that answers now pushes its brain, then writes the note naming the other machine.
  2. Its dashboard sees it is no longer named and stops polling Telegram, without taking any new message, so
     nothing is answered twice and nothing is lost: Telegram keeps unread messages for whoever polls next.
  3. The other machine's dashboard sees its own name, pulls the brain that was just pushed, then starts the phone.
OneDrive carries the note in seconds to a minute; during that gap nobody answers and Telegram holds the messages.

    bin/jarvis phone                 who answers now, and the machines known
    bin/jarvis phone laptop          hand the phone to the laptop (from the machine that answers now)
    bin/jarvis phone desktop         hand it back to the home PC
    bin/jarvis phone here --takeover take it on this machine when the other one is off or unreachable
On the phone itself: /laptop or /desktop.
"""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime
from pathlib import Path

NOTE = "phone.json"


class PhoneError(Exception):
    pass


def this_machine() -> str:
    return os.environ.get("JARVIS_MACHINE") or socket.gethostname()


def note_path() -> Path:
    from . import sync  # imported here: sync imports paths, and paths asks this module for the role
    return sync.sync_dir() / NOTE


def state() -> dict:
    try:
        data = json.loads(note_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def holder() -> str | None:
    """The machine named to answer the phone, or None when no one has been named yet."""
    name = state().get("holder")
    return name if isinstance(name, str) and name.strip() else None


def answers_here() -> bool | None:
    """True or False when the note names a machine, None when there is no note (the role file decides)."""
    name = holder()
    return None if name is None else name.lower() == this_machine().lower()


def machines() -> list[str]:
    """Every machine seen so far: this one, the note's, and whoever pushed the last brain."""
    from . import sync
    seen = [this_machine()] + list(state().get("machines") or [])
    seen += [state().get("holder"), state().get("by"), sync.manifest().get("machine")]
    out: list[str] = []
    for name in seen:
        if isinstance(name, str) and name.strip() and name.lower() not in (n.lower() for n in out):
            out.append(name)
    return out


def resolve(target: str) -> str:
    """'here', 'laptop', 'desktop' / 'pc' / 'home', or a machine name, to a known machine name."""
    t = target.strip().lower()
    known = machines()
    if t in ("here", "this", "me"):
        return this_machine()
    for name in known:
        if name.lower() == t:
            return name
    laptops = [n for n in known if "laptop" in n.lower()]
    others = [n for n in known if "laptop" not in n.lower()]
    if t in ("laptop", "notebook") and laptops:
        return laptops[0]
    if t in ("desktop", "pc", "home", "computer", "main") and others:
        return others[0]
    raise PhoneError(f"I don't know a machine called '{target}'. Known: {', '.join(known)}. "
                     "Open JARVIS on that machine once so it registers itself.")


def _write(data: dict) -> None:
    path = note_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(NOTE + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def register() -> None:
    """Add this machine to the note's list, so the other machine can hand the phone to it by name."""
    data = state()
    known = list(data.get("machines") or [])
    if any(n.lower() == this_machine().lower() for n in known):
        return
    data["machines"] = known + [this_machine()]
    try:
        _write(data)
    except OSError:
        pass  # OneDrive busy: the next start tries again


def hand_to(conn, target: str, takeover: bool = False) -> dict:
    """Name the machine that answers the phone. Pushes this machine's brain first when it hands the phone away."""
    from . import paths, sync
    name = resolve(target)
    here = this_machine()
    current = holder()
    answering_here = paths.machine_role() == "primary"
    if name.lower() == (current or "").lower():
        return {"holder": name, "changed": False, "note": f"{name} already answers the phone."}
    if name.lower() != here.lower() and not answering_here:
        raise PhoneError(f"Only the machine answering the phone now ({current or 'the primary'}) can hand it on. "
                         f"To take it on this machine, use: bin/jarvis phone here --takeover")
    if name.lower() == here.lower() and not answering_here and not takeover:
        raise PhoneError(f"{current or 'The other machine'} answers the phone now. Ask it to hand over "
                         "(text /laptop or /desktop), or if it's off use: bin/jarvis phone here --takeover")
    pushed = None
    if answering_here and name.lower() != here.lower():
        pushed = sync.push(conn, force=True)  # the new machine starts from this brain
    data = state()
    known = list(data.get("machines") or [])
    for n in (here, name):
        if not any(k.lower() == n.lower() for k in known):
            known.append(n)
    data.update(holder=name, by=here, since=datetime.now().astimezone().isoformat(timespec="seconds"),
                takeover=bool(takeover and name.lower() == here.lower()), machines=known)
    _write(data)
    note = f"{name} answers the phone now (it takes up to a minute while OneDrive carries the note)."
    if pushed:
        note += f" Brain pushed first: {pushed.get('note')}."
    return {"holder": name, "changed": True, "note": note, "pushed": pushed}


def status_line() -> str:
    name = holder()
    if not name:
        from . import paths
        return (f"Phone: no machine named yet; this machine ({this_machine()}) is the {paths.machine_role()} "
                f"by its own setting. Known machines: {', '.join(machines())}.")
    st = state()
    who = "this machine" if name.lower() == this_machine().lower() else "the other machine"
    return (f"Phone: {name} answers ({who}), since {st.get('since', '?')}, set by {st.get('by', '?')}. "
            f"Known machines: {', '.join(machines())}.")
