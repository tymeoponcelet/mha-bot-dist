"""Reprise quand ADB ou l'emulateur ne repond plus (appele par screenshot() apres 5 captures ratees).

Ordre, du plus doux au plus fort :
  1. alerte Discord tout de suite (incident "live" : voir journal.py) ;
  2. serveur ADB relance (kill-server / start-server / connect) ;
  3. si l'appareil n'est toujours pas la : MuMu relance par MuMuManager.exe (instance memorisee au demarrage) ;
  4. le jeu est relance (paquet "game_package" de config.json). Ensuite le garde-chien (s'il est actif) ou la reprise
     "ecran inconnu" ramene le bot au menu principal.
Au plus MAX_RECOVER reprises par WINDOW secondes : au-dela, le script s'arrete comme avant (incident final).
config.json n'est jamais modifie ici.
"""
import json
import os
import subprocess
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_RECOVER = 3
WINDOW = 1800
BOOT_TIMEOUT = 240                         # s max pour que MuMu redemarre et qu'ADB revoie l'appareil
GAME_WAIT = 25                             # s laisses au jeu pour s'ouvrir avant de reprendre
MANAGER_CANDIDATES = [
    r"C:\Program Files\Netease\MuMuPlayer\nx_main\MuMuManager.exe",
    r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\MuMuManager.exe",
    r"C:\Program Files\Netease\MuMu Player 12\shell\MuMuManager.exe",
    r"D:\Program Files\Netease\MuMuPlayer\nx_main\MuMuManager.exe",
    r"D:\Program Files\Netease\MuMuPlayer-12.0\shell\MuMuManager.exe",
]
_state = {"index": None, "times": []}


def _run(args, timeout=30):
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        return r.returncode, r.stdout.decode("utf-8", "ignore")
    except Exception as e:                 # noqa: BLE001
        return -1, str(e)


def decode_raw(raw):
    """Capture BRUTE d'adb (exec-out screencap, sans PNG : ~2x plus rapide) -> image BGR, ou None si format inattendu."""
    import struct
    import cv2
    import numpy as np
    if not raw or len(raw) < 16:
        return None
    w, h, fmt = struct.unpack("<III", raw[:12])
    n = w * h * 4
    if fmt != 1 or not (0 < w <= 8192 and 0 < h <= 8192):
        return None
    for hdr in (12, 16):                       # l'en-tete fait 12 octets (Android <= 9) ou 16 (Android 10+)
        if len(raw) - hdr == n:
            img = np.frombuffer(raw, np.uint8, count=n, offset=hdr).reshape(h, w, 4)
            return cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    return None


def manager():
    """Chemin de MuMuManager.exe, ou None (autre emulateur : on se contente de relancer ADB)."""
    env = os.environ.get("MHA_MUMU_MANAGER")
    for c in ([env] if env else []) + MANAGER_CANDIDATES:
        if c and Path(c).exists():
            return c
    return None


def instances(mgr=None):
    mgr = mgr or manager()
    if not mgr:
        return {}
    code, out = _run([mgr, "info", "-v", "all"], timeout=20)
    try:
        data = json.loads(out[out.index("{"):]) if code == 0 else {}
    except ValueError:
        return {}
    if "index" in data:                    # une seule instance : MuMuManager renvoie directement l'objet
        data = {str(data["index"]): data}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def remember():
    """Au demarrage du bot : memorise l'instance MuMu demarree (celle qu'on relancera si besoin)."""
    try:
        started = [k for k, v in instances().items() if v.get("is_android_started")]
        if len(started) == 1:
            _state["index"] = started[0]
    except Exception:                      # noqa: BLE001
        pass
    return _state["index"]


def _game_package():
    try:
        return str(json.loads((BASE / "config.json").read_text(encoding="utf-8")).get("game_package") or "")
    except (OSError, ValueError):
        return ""


def _device_ok(adb, find_device):
    """-> nom de l'appareil si ADB le voit ET qu'une capture passe, sinon None."""
    dev = find_device()
    if not dev:
        return None
    code, _ = _run([adb, "-s", dev, "shell", "echo", "ok"], timeout=15)
    return dev if code == 0 else None


def recover(adb, find_device, log, why=""):
    """Tente de recuperer ADB / l'emulateur. find_device() -> nom de l'appareil ou None (connect compris).
    -> nom de l'appareil (reprise reussie) ou None (abandon)."""
    import journal
    now = time.time()
    _state["times"] = [t for t in _state["times"] if now - t < WINDOW]
    if len(_state["times"]) >= MAX_RECOVER:
        log(f"(!) ADB / emulateur : deja {MAX_RECOVER} reprises en {WINDOW // 60} min, j'abandonne.")
        return None
    _state["times"].append(now)
    journal.incident(f"ADB / émulateur ne répond plus ({why[:200]}) : tentative de reprise automatique",
                     live=True, kind="adb")
    log("=== ADB / EMULATEUR : ne repond plus -> relance du serveur ADB ===")
    _run([adb, "kill-server"], timeout=20)
    _run([adb, "start-server"], timeout=30)
    dev = _device_ok(adb, find_device)
    if not dev:
        mgr = manager()
        idx = _state["index"]
        if not mgr or idx is None:
            log("(!) emulateur introuvable par ADB, et MuMuManager / instance inconnus : relance MuMu a la main.")
            return None
        info = instances(mgr).get(str(idx), {})
        action = "restart" if info.get("is_process_started") else "launch"
        log(f"=== MuMu (instance {idx}) : {action} ===")
        _run([mgr, "control", "-v", str(idx), action], timeout=60)
        t0 = time.time()
        while time.time() - t0 < BOOT_TIMEOUT:
            time.sleep(10)
            if instances(mgr).get(str(idx), {}).get("is_android_started"):
                dev = _device_ok(adb, find_device)
                if dev:
                    break
        if not dev:
            log(f"(!) MuMu n'a pas redemarre en {BOOT_TIMEOUT // 60} min.")
            return None
    pkg = _game_package()
    if pkg:
        log(f"relance du jeu ({pkg})")
        _run([adb, "-s", dev, "shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"], timeout=30)
        time.sleep(GAME_WAIT)
    log(f"=== ADB / emulateur : repris ({dev}) ===")
    return dev
