"""Le farm EN COURS, partage entre l'assistant, le bot Discord et le planning : logs/en_cours.json.

C'est le script lui-meme qui s'annonce (journal.start), quel que soit celui qui l'a lance :
  source = "assistant" | "discord" | "planning" | "manuel"  (variable d'environnement MHA_SOURCE)
Il met a jour sa progression (run X/Y, etape d'enchainement, derniere ligne) et s'efface a la fin.
read() ne renvoie le farm que si son processus tourne encore (pid + heure de creation verifies).
stop() arrete le farm (et les scripts lances par l'enchaineur).
"""
import json
import os
import sys
import re
import subprocess
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
FILE = BASE / "logs" / "en_cours.json"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SOURCES = {"assistant": "l'assistant", "discord": "Discord", "planning": "le planning", "manuel": "un lancement manuel"}
STEP = re.compile(r"===== Etape (\d+)/(\d+) : (.+?) =====")
PROGRESS = [re.compile(r"\|\s*(?:multi-)?run (\d+)/(\d+)"), re.compile(r"\bcombat (\d+)/(\d+)")]
WRITE_EVERY = 3.0                             # s mini entre deux ecritures (sauf progression / etape : tout de suite)
_me = {"rec": None, "t": 0.0}


# ---------------------------------------------------------------- processus vivant ?
def _creation_time(pid):
    """Heure de creation du processus (secondes epoch), None s'il n'existe pas / plus."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return 0.0
        except OSError:
            return None
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, int(pid))          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        code = wintypes.DWORD()
        if not k.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:   # 259 = STILL_ACTIVE
            return None
        c, e, kt, u = (wintypes.FILETIME() for _ in range(4))
        if not k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(u)):
            return 0.0
        ft = (c.dwHighDateTime << 32) | c.dwLowDateTime
        return ft / 1e7 - 11644473600                    # FILETIME (1601) -> epoch
    finally:
        k.CloseHandle(h)


def alive(rec):
    ct = _creation_time(rec.get("pid", 0)) if rec else None
    if ct is None:
        return False
    return ct == 0.0 or abs(ct - rec.get("pstart", ct)) < 5    # meme processus (pas un numero reutilise)


# ---------------------------------------------------------------- ecriture (le script qui tourne)
def _write(rec):
    try:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        tmp.replace(FILE)
        _me["t"] = time.time()
    except OSError:
        pass


def declare(script, label, log=None):
    pid = os.getpid()
    rec = {"pid": pid, "pstart": _creation_time(pid) or time.time(), "script": script, "label": label,
           "source": os.environ.get("MHA_SOURCE") or "manuel", "t0": time.time(), "done": None, "total": None,
           "step": None, "last": "", "log": str(log or ""), "args": sys.argv[1:]}      # options reelles (--refill...)
    _me["rec"] = rec
    _write(rec)
    import atexit
    atexit.register(clear)


def line(text):
    rec = _me["rec"]
    if not rec or not text.strip():
        return
    rec["last"] = text.strip()[-160:]
    urgent = False
    m = STEP.search(text)
    if m:
        rec.update(step=f"{m.group(1)}/{m.group(2)} : {m.group(3)}", done=None, total=None)
        urgent = True
    for rx in PROGRESS:
        m = rx.search(text)
        if m:
            d, t = int(m.group(1)), int(m.group(2))
            urgent = (d, t) != (rec["done"], rec["total"])
            rec["done"], rec["total"] = d, t
            break
    if urgent or time.time() - _me["t"] >= WRITE_EVERY:
        _write(rec)


def clear():
    """Fin du script : on s'efface (seulement si c'est bien nous qui sommes inscrits)."""
    rec = _me["rec"]
    _me["rec"] = None
    try:
        cur = json.loads(FILE.read_text(encoding="utf-8"))
        if rec and cur.get("pid") == rec["pid"]:
            FILE.unlink()
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------- lecture / arret (assistant, Discord)
def read():
    """-> le farm en cours (dict) ou None."""
    try:
        rec = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not alive(rec):
        try:
            FILE.unlink()                                # reste d'un script tue sans pouvoir s'effacer
        except OSError:
            pass
        return None
    return rec


def stop():
    """Arrete le farm en cours (avec ses scripts enfants). -> le farm arrete (dict) ou None."""
    rec = read()
    if not rec:
        return None
    subprocess.run(["taskkill", "/PID", str(rec["pid"]), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
    for _ in range(20):
        if not alive(rec):
            break
        time.sleep(0.25)
    try:
        FILE.unlink()
    except OSError:
        pass
    return rec


def tail(rec, n=8):
    """Les n dernieres lignes du journal du farm en cours."""
    try:
        lines = Path(rec.get("log", "")).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [rec.get("last", "")] if rec.get("last") else []
    return [l for l in lines if l.strip() and not l.startswith("=====  ")][-n:]


def describe(rec):
    """Une ligne lisible : 'Supply Bounty niv. 4 · run 3/10 · 12 min · lancé depuis Discord'."""
    el = int(time.time() - rec.get("t0", time.time()))
    bits = [rec.get("label") or rec.get("script", "?")]
    if rec.get("step"):
        bits.append(f"étape {rec['step']}")
    if rec.get("total"):
        bits.append(f"run {rec['done']}/{rec['total']}")
    bits.append(f"{el // 3600}h{el % 3600 // 60:02d}" if el >= 3600 else f"{el // 60} min")
    bits.append("lancé depuis " + SOURCES.get(rec.get("source"), rec.get("source", "?")))
    return " · ".join(bits)
