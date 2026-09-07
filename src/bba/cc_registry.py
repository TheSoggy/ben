"""The convention cards this server will serve, and the only way a request names one.

`BBA/CC/` holds twenty `.bbsa` files, most of them *opponent profiles* — models of
other robots (Shark, WBridge5, Q-Plus, Blue Chip, Meadowlark, LIA, Micro Bridge)
rather than systems a person would say they play. This registry is the curated
subset: one file per BBA system type, so every entry is a real bidding system and
the five together cover the whole of `set_system_type`'s vocabulary
(0 = 2/1, 1 = SAYC, 2 = Polish Club, 3 = Precision, 4 = Acol).

**The keys are the API, and the filenames are not.** A caller sends
``file_us=acol``; it never sends a filename, an extension or a path. `resolve/2`
is an exact dictionary lookup followed by a join this module performs itself —
there is no `os.listdir`, no glob, and at no point is the caller's string joined
onto a path. That is deliberate and load-bearing: `/bids` used to do
``os.path.join(base_path, "BBA/CC/" + request.args.get("file_us"))``, which walks
out of the directory on `../` for the asking.

`verify_all()` is called at boot so a card missing from the image is a startup
failure with a filename in it, rather than a 500 the first time somebody plays
that system.
"""

import os

# key -> filename under BBA/CC/. One per BBA system type; see the module docstring
# for why the rest of the directory is not here. Keys cross the wire, so they are
# lowercase and underscored and must stay stable — the Elixir side stores them on
# `games` rows, and a renamed key orphans every table that used it.
CARDS = {
    # System type 0 — 2/1 game force. The site default, and what every robot
    # bid before convention cards existed.
    "gib_bbo": "GIB-BBO.bbsa",
    # System type 1 — Standard American Yellow Card. Note this is *not*
    # `default.bbsa`, which is also type 1 but is upstream's example file.
    "sayc": "BEN-SAYC.bbsa",
    # System type 4 — Acol.
    "acol": "Acol.bbsa",
    # System type 2 — Wspólny Język / Polish Club.
    "polish_club": "WJ.bbsa",
    # System type 3 — Precision, in its Meckwell flavour.
    "precision": "Meckwell.bbsa",
}


def base_path():
    """Where `BBA/` lives. Same rule the rest of the server uses."""
    return os.getenv("BEN_HOME") or ".."


def keys():
    """The served keys, sorted, for `/systems` and for tests."""
    return sorted(CARDS)


def path_for(key):
    """The absolute path of a served card, or None if `key` is not one.

    Separate from `resolve/2` so `verify_all()` can ask about a key without
    having a fallback to hand.
    """
    filename = CARDS.get(key)

    if filename is None:
        return None

    return os.path.join(base_path(), "BBA", "CC", filename)


def resolve(key, fallback):
    """Turn a request's card key into a path, or fall back.

    `fallback` is the configured default (`models.bba_our_cc` /
    `models.bba_their_cc`), already a path. An absent, blank or unknown key
    resolves to it — an unknown key is somebody's stale bookmark or a
    probe, and neither deserves an error page that names the filesystem.

    A caller who needs to know whether their key was recognised should ask
    `/systems` first; that is what it is for.
    """
    if not key:
        return fallback

    return path_for(key) or fallback


def verify_all():
    """Assert every served card exists and parses. Raises at boot, not at play.

    Parsing is checked as well as existence because `BBABotBid.load_ccs` reads
    these with ``key, value = line.strip().split(' = ')`` — a file that is
    present but malformed fails exactly as loudly as one that is missing, and
    the point of this check is that neither reaches a request.
    """
    for key in keys():
        path = path_for(key)

        with open(path, "r") as file:
            for i, line in enumerate(file):
                stripped = line.strip()

                if not stripped:
                    continue

                name, _, value = stripped.partition(" = ")

                if not value:
                    raise ValueError(f"convention card {key} ({path}) line {i + 1}: not `key = value`")

                if i == 0 and name == "System type":
                    int(value)
