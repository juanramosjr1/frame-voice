<p align="center"><img src="docs/intro.png" width="520" alt="fuelCell"></p>

# fuelCell Voice Typing

Speak into any text box on your **Steam Frame**: the Steam store search, a browser, Discord. It also gives you easy **Copy, Paste and Select all** using your controllers.

- 🎙️ **Hold Grip + B, talk, let go.** Your words get typed wherever your cursor is.
- 📋 **Grip + Y** copies, **Grip + A** pastes, **Grip + X** selects all.
- 🔒 **Free and private.** Speech recognition runs on the headset itself, with no account and nothing sent online.

<p align="center">
  <img src="docs/app.png" width="300" alt="Main window">
  <img src="docs/listening.png" width="300" alt="Listening">
</p>

---

## Install (about 5 minutes)

You only do this once.

**1. Open the desktop on your Frame.** Switch the Frame to its Linux desktop (KDE) and open **Konsole**, the terminal app.

**2. Copy and paste this line into Konsole, then press Enter:**

```bash
curl -fsSL https://raw.githubusercontent.com/juanramosjr1/frame-voice/main/install.sh | bash
```

The installer shows 5 steps and tells you when it's done.

> **If it asks for a password:** voice typing needs permission to act like a keyboard. If you've never set a password, press `Ctrl+C`, type `passwd`, press Enter, choose a password, then run the line from step 2 again.

**3. Turn on one SteamVR setting.** This lets controller shortcuts work while other apps are open.
In SteamVR, go to **Settings > Developer** and turn on **Experimental overlay input overrides**.

**4. Restart the headset.** Then open **fuelCell Voice Typing** from your apps. After the first time, it should start on its own with SteamVR.

To make sure everything is set up, run this in Konsole:

```bash
frame-voice --check
```

---

## How to use it

| What you want | Controllers (default) | Or in the window |
| --- | --- | --- |
| Type with your voice | Click a text box, **hold Grip + B**, talk, let go | Tap the big mic, talk, tap again |
| Copy | **Grip + Y** | Copy |
| Paste | **Grip + A** | Paste |
| Select all | **Grip + X** | Select all |
| Undo, Delete, Space, Enter | — | Buttons in the window |

While you talk, a small **"Listening..."** badge floats below your view, so you don't need the window open.

**Tips**
- Closing the window keeps the app running so the shortcuts keep working. Open the app again to bring the window back. Use **Settings > Quit app** to stop it.
- **Last typed** keeps your last sentence. Use **Type again** to retype it, or **Copy text** to put it on the clipboard.

## Change the shortcuts

Open **Settings** (the gear icon) and go to **Controller shortcuts**:

<p align="center"><img src="docs/settings.png" width="380" alt="Settings"></p>

| Layout | How it works |
| --- | --- |
| **Grip combos** (default) | Hold grip, then press B to talk, A to paste, Y to copy, X to select all |
| **Trigger combos** | The same, but hold the trigger instead of grip |
| **Simple** | Hold B to talk, hold A to paste |

If you want something else, tap **Customize buttons in SteamVR**. That opens SteamVR's own controller bindings screen for this app, where you can map anything to any button. If the button doesn't open it, go to **SteamVR Settings > Controllers > Manage Controller Bindings** and pick **fuelCell Voice Typing**.

Settings also has: speech model (**Fast / Balanced / Most accurate**), add a space after each dictation, press Enter after each dictation, show the intro, and start automatically.

---

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Nothing gets typed | Run `frame-voice --check`. If "Can type into apps" says FIX, run the installer again and restart. |
| Controller shortcuts do nothing | Turn on **Experimental overlay input overrides** (install step 3), and make sure SteamVR is running. The dot at the bottom of the window turns green when shortcuts are on. |
| "Didn't catch that" | Speak a little closer or longer, or try **Most accurate** in Settings. |
| Typing is slow to start | Use **Fast** in Settings. |
| Wrong characters | The app types as a US keyboard layout. |

## Uninstall

```bash
bash ~/.local/share/frame-voice/uninstall.sh
```

---

## How it works (for the curious)

- **Speech to text:** [faster-whisper](https://github.com/SYSTRAN/faster-whisper), running locally on the headset's ARM CPU.
- **Typing:** a virtual USB keyboard made through Linux `uinput`, so anything that accepts a real keyboard accepts it, including the Steam client. Written in pure Python with nothing to compile.
- **Controller shortcuts:** SteamVR Input with default bindings for the Steam Frame controllers (`frame_controller`), plus Quest/Touch and Index fallbacks. The app uses Valve's official Linux ARM64 OpenVR library.
- **Window:** Qt (PySide6). The window never takes keyboard focus, so pressing its buttons leaves your cursor in the text box, the same way an on-screen keyboard works.

### Testing

Every push runs the full test suite on GitHub on both **x86 and ARM64** Linux. It runs the real installer, types through a real virtual keyboard and checks the key presses arrive, and has Whisper transcribe a synthesized sentence.

Not yet verified on a real Steam Frame:
- typing into the Steam store inside the SteamVR dashboard
- the exact Frame controller button names in the default bindings
- the overlay input override setting

If something doesn't work, run `frame-voice --check` and share the output.

### Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'
QT_QPA_PLATFORM=offscreen pytest
```

MIT licensed. Made by fuelCell.
