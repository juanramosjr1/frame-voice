"""Regenerate frame_voice/steamvr/*.json (SteamVR action manifest + default
bindings). Run: python3 tools/gen_bindings.py

Each button the app uses is its own action set with one "press" action, bound
as a plain button. Combos (hold A + B, trigger + A, ...) are worked out in
frame_voice/vr.py, so SteamVR chords aren't needed. One set per button lets the
app take only the buttons it needs at that moment (see vr.py).
"""

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "frame_voice" / "steamvr"
APP_KEY = "fuelcell.voicetyping"

BUTTONS = {
    "a": ("Button A", "A: talk (with B), paste (with the trigger)"),
    "b": ("Button B", "B: talk"),
    "x": ("Button X", "X: select all (with the trigger)"),
    "y": ("Button Y", "Y: copy (with the trigger)"),
    "trigger": ("Trigger", "Trigger: used together with A, B, X and Y"),
}

# Where each button is, per controller type: name -> (hand, input).
CONTROLLERS = {
    # Steam Frame: A, B, X and Y are all on the right controller (Valve's
    # "Steam Frame Input" docs). The left one has a d-pad instead.
    "frame_controller": {"a": ("right", "a"), "b": ("right", "b"), "x": ("right", "x"),
                         "y": ("right", "y"), "trigger": ("right", "trigger")},
    # Quest / Touch: A/B on the right, X/Y on the left.
    "oculus_touch": {"a": ("right", "a"), "b": ("right", "b"), "x": ("left", "x"),
                     "y": ("left", "y"), "trigger": ("right", "trigger")},
    # Index: A/B on both hands; the left hand's stand in for X/Y.
    "knuckles": {"a": ("right", "a"), "b": ("right", "b"), "x": ("left", "a"),
                 "y": ("left", "b"), "trigger": ("right", "trigger")},
}


def bindings(ctype, where):
    out = {}
    for button, (hand, comp) in where.items():
        out[f"/actions/{button}"] = {"sources": [{
            "path": f"/user/hand/{hand}/input/{comp}",
            "mode": "trigger" if comp == "trigger" else "button",
            "parameters": {},
            "inputs": {"click": {"output": f"/actions/{button}/in/press"}},
        }]}
    return {
        "action_manifest_version": 0,
        "alias_info": {},
        "app_key": APP_KEY,
        "bindings": out,
        "category": "steamvr_input",
        "controller_type": ctype,
        "description": "Hold A + B to talk. Trigger + A pastes, Y copies, X selects all.",
        "name": f"fuelCell defaults ({ctype})",
        "options": {},
        "simulated_actions": [],
    }


def main():
    manifest = {
        "default_bindings": [{"controller_type": c, "binding_url": f"bindings_{c}.json"}
                             for c in CONTROLLERS],
        "action_sets": [{"name": f"/actions/{b}", "usage": "single"} for b in BUTTONS],
        "actions": [{"name": f"/actions/{b}/in/press", "type": "boolean"} for b in BUTTONS],
        "localization": [dict(
            {"language_tag": "en_US"},
            **{f"/actions/{b}": name for b, (name, _) in BUTTONS.items()},
            **{f"/actions/{b}/in/press": what for b, (_, what) in BUTTONS.items()},
        )],
    }
    for old in OUT.glob("*.json"):
        old.unlink()
    (OUT / "actions.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for ctype, where in CONTROLLERS.items():
        (OUT / f"bindings_{ctype}.json").write_text(json.dumps(bindings(ctype, where), indent=2) + "\n")


if __name__ == "__main__":
    main()
