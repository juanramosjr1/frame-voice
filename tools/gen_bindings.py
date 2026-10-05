"""Regenerate frame_voice/steamvr/*.json (SteamVR action manifest + default
bindings). Run: python3 tools/gen_bindings.py
"""

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "frame_voice" / "steamvr"

ACTIONS = ["talk", "copy", "paste", "select_all", "enter"]
LABELS = {"talk": "Hold to talk", "copy": "Copy", "paste": "Paste",
          "select_all": "Select all", "enter": "Enter"}
PRESETS = {"ab": "Hold A + B", "trigger": "Trigger + B", "hold_b": "Hold B"}

# Physical buttons per controller type: name -> (hand, input path component).
# Steam Frame: A/B/X/Y and the trigger, as on a gamepad.
CONTROLLERS = {
    "frame_controller": {
        "A": ("right", "a"), "B": ("right", "b"), "X": ("left", "x"), "Y": ("left", "y"),
    },
    # Quest / Touch: A/B on the right, X/Y on the left.
    "oculus_touch": {
        "A": ("right", "a"), "B": ("right", "b"), "X": ("left", "x"), "Y": ("left", "y"),
    },
    # Index: A/B on both hands; use the left hand's for the X/Y roles.
    "knuckles": {
        "A": ("right", "a"), "B": ("right", "b"), "X": ("left", "a"), "Y": ("left", "b"),
    },
}


def path(hand, comp):
    return f"/user/hand/{hand}/input/{comp}"


def chord(preset, action, *inputs):
    return {"output": f"/actions/{preset}/in/{action}",
            "inputs": [[path(h, c), "click"] for h, c in inputs]}


def button(preset, action, hand, comp):
    return {"path": path(hand, comp), "mode": "button",
            "inputs": {"click": {"output": f"/actions/{preset}/in/{action}"}}}


def bindings(ctype, face):
    def trig(btn):
        hand, comp = face[btn]
        return ((hand, "trigger"), (hand, comp))

    out = {}
    for preset in PRESETS:
        chords = [
            chord(preset, "paste", *trig("A")),
            chord(preset, "copy", *trig("Y")),
            chord(preset, "select_all", *trig("X")),
        ]
        sources = []
        if preset == "ab":
            chords.append(chord(preset, "talk", face["A"], face["B"]))
        elif preset == "trigger":
            chords.append(chord(preset, "talk", *trig("B")))
        else:
            sources.append(button(preset, "talk", *face["B"]))
        body = {"chords": chords}
        if sources:
            body["sources"] = sources
        out[f"/actions/{preset}"] = body
    return {"controller_type": ctype, "name": f"fuelCell defaults ({ctype})",
            "description": "Talk: A+B / Trigger+B / B. Trigger + A paste, Y copy, X select all.",
            "bindings": out}


def main():
    manifest = {
        "default_bindings": [{"controller_type": c, "binding_url": f"bindings_{c}.json"}
                             for c in CONTROLLERS],
        "action_sets": [{"name": f"/actions/{p}", "usage": "single"} for p in PRESETS],
        "actions": [{"name": f"/actions/{p}/in/{a}", "type": "boolean"}
                    for p in PRESETS for a in ACTIONS],
        "localization": [dict(
            {"language_tag": "en_US"},
            **{f"/actions/{p}": label for p, label in PRESETS.items()},
            **{f"/actions/{p}/in/{a}": LABELS[a] for p in PRESETS for a in ACTIONS},
        )],
    }
    for old in OUT.glob("*.json"):
        old.unlink()
    (OUT / "actions.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for ctype, face in CONTROLLERS.items():
        (OUT / f"bindings_{ctype}.json").write_text(json.dumps(bindings(ctype, face), indent=2) + "\n")


if __name__ == "__main__":
    main()
