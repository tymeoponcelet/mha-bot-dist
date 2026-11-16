"""Le farm EN COURS, partage entre l'assistant, le bot Discord et le planning : logs/en_cours.json.

C'est le script lui-meme qui s'annonce (journal.start), quel que soit celui qui l'a lance :
  source = "assistant" | "discord" | "planning" | "manuel"  (variable d'environnement MHA_SOURCE)
Il met a jour sa progression (run X/Y, etape d'enchainement, derniere ligne) et s'efface a la fin.
read() ne renvoie le farm que si son processus tourne encore (pid + heure de creation verifies).
stop() arrete le farm (et les scripts lances par l'enchaineur).
"""
import os  # noqa: E402
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")   # memoire : sinon numpy reserve ~370 Mo par processus (16/11/2026 : « Insufficient memory » chez un ami)
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
def file_for(device=None):
    """Un fichier par instance (2 comptes) ; sans instance imposee : l'ancien fichier unique."""
    if not device:
        return FILE
    safe = "".join(c if c.isalnum() else "-" for c in str(device))
    return FILE.with_name(f"en_cours_{safe}.json")


SCRIPT_FILES = {"supply": "auto_supply.py", "nightops": "auto_nightops.py", "herotrial": "auto_hero_trial.py",
                "whm": "auto_whm.py", "aa": "auto_aa.py", "enchainement": "enchainer.py", "histoire": "auto_story_mha.py"}
STEMS = {Path(v).stem: k for k, v in SCRIPT_FILES.items()}


def child_of(args):
    """Relance : le farm rejoue apres « -- » -> (script, args) ou (None, [])."""
    args = list(args)
    if "--" not in args:
        return None, []
    rest = args[args.index("--") + 1:]
    for i, a in enumerate(rest):
        stem = Path(str(a)).stem
        if stem in STEMS:
            return STEMS[stem], [str(x) for x in rest[i + 1:]]
    return None, []


def command_of(rec):
    """Commande exacte pour relancer le farm `rec` (memes options), ou None."""
    script, args = rec.get("script"), list(rec.get("args") or [])
    if script == "relance":
        script, args = child_of(args)
    if script not in SCRIPT_FILES:
        return None
    return [sys.executable, "-u", str(BASE / SCRIPT_FILES[script])] + args


def modes_of(script, args):
    """Modes que ce farm occupe : {'supply'} ; un enchainement -> les modes de ses etapes ; une relance -> son farm."""
    if script == "relance":
        child, cargs = child_of(args)
        return modes_of(child, cargs) if child else ["relance"]
    if script != "enchainement":
        return [script]
    out = []
    try:
        import enchainer as ec
        for t in args:
            if not t.startswith("--"):
                m = ec.parse_token(t)["mode"]
                m = {"hero": "herotrial"}.get(m, m)
                if m not in out:
                    out.append(m)
    except BaseException:                                # noqa: BLE001
        pass
    return out or [script]


def _write(rec):
    f = file_for(rec.get("device"))
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
        _me["t"] = time.time()
    except OSError:
        pass


class Occupied(Exception):
    """Un autre farm VIVANT est deja inscrit sur cette instance : on ne l'ecrase pas (deux farms sur un meme jeu)."""

    def __init__(self, rec):
        super().__init__(rec.get("label", "?"))
        self.rec = rec


def declare(script, label, log=None):
    pid = os.getpid()
    other = _read_file(file_for(os.environ.get("MHA_DEVICE") or None))
    if other and other.get("pid") != pid:
        raise Occupied(other)
    rec = {"pid": pid, "pstart": _creation_time(pid) or time.time(), "script": script, "label": label,
           "source": os.environ.get("MHA_SOURCE") or "manuel", "t0": time.time(), "done": None, "total": None,
           "step": None, "last": "", "log": str(log or ""), "args": sys.argv[1:],     # options reelles (--refill...)
           "device": os.environ.get("MHA_DEVICE") or None, "modes": modes_of(script, sys.argv[1:])}
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
    if not rec:
        return
    f = file_for(rec.get("device"))
    try:
        cur = json.loads(f.read_text(encoding="utf-8"))
        if cur.get("pid") == rec["pid"]:
            f.unlink()
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------- lecture / arret (assistant, Discord)
def _read_file(f):
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not alive(rec):
        try:
            f.unlink()                                   # reste d'un script tue sans pouvoir s'effacer
        except OSError:
            pass
        return None
    rec["_file"] = str(f)
    return rec


def read_all():
    """-> tous les farms en cours (toutes instances), du plus ancien au plus recent."""
    files = [FILE] + sorted(FILE.parent.glob("en_cours_*.json")) if FILE.parent.exists() else []
    recs = [r for r in (_read_file(f) for f in files) if r]
    return sorted(recs, key=lambda r: r.get("t0", 0))


def read(device=None):
    """-> le farm en cours sur cette instance (device) ; sans device : le premier farm en cours. None sinon."""
    if device:
        return _read_file(file_for(device))
    recs = read_all()
    return recs[0] if recs else None


def conflict(script, device=None, args=()):
    """Ce qui empeche de lancer `script` sur `device` : (farm, raison) ou None.
    Une instance = un farm a la fois ; et jamais le MEME script sur deux instances en meme temps."""
    want = set(modes_of(script, list(args)))
    for rec in read_all():
        if (rec.get("device") or None) == (device or None):
            return rec, "instance occupée"
        common = want & set(rec.get("modes") or [rec.get("script")])
        if common:
            return rec, f"{', '.join(sorted(common))} tourne déjà sur l'autre instance"
    return None


def stop(device=None, rec=None):
    """Arrete le farm en cours (avec ses scripts enfants) : celui de `device`, ou `rec`, ou le premier.
    -> le farm arrete (dict) ou None."""
    rec = rec or read(device)
    if not rec:
        return None
    subprocess.run(["taskkill", "/PID", str(rec["pid"]), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
    for _ in range(20):
        if not alive(rec):
            break
        time.sleep(0.25)
    try:
        Path(rec.get("_file") or file_for(rec.get("device"))).unlink()
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
    if rec.get("device"):
        try:
            import instances
            bits.append("📱 " + instances.label(rec["device"]))
        except Exception:                               # noqa: BLE001
            bits.append("📱 " + rec["device"])
    return " · ".join(bits)
