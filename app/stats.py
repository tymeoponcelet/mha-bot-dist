"""Statistiques des farms : historique des sessions dans logs/runs.jsonl (ecrit par journal.py, aucun mode modifie).

    python stats.py            # resume dans la console

Une session = un lancement d'un mode (une etape d'enchainement compte comme une session).
Lignes du fichier (JSON) : {"id", "ev": "debut" | "progres" | "fin", ...} ; on garde la derniere info de chaque session.
Une session sans ligne "fin" a ete coupee (/stop, fenetre fermee) ou tourne encore : "interrompu".
"""
import json
import os
import re
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
FILE = BASE / "logs" / "runs.jsonl"
MAX_LINES = 20000                          # au-dela, on garde les plus recentes
SKIP_ARGS = ("--test", "--list", "--swipe", "--capture", "--dry")
NOT_STATS = {"enchainement"}               # ses etapes sont comptees une par une
PROGRESS = [re.compile(r"\|\s*(?:multi-)?run (\d+)/(\d+)"), re.compile(r"\bcombat (\d+)/(\d+)")]
COUNTERS = {"recharges": "=== RECHARGE DE STAMINA", "relances": "=== WATCHDOG :", "reprises": "=== ECRAN INCONNU",
            "adb": "=== ADB / EMULATEUR : ne repond", "incidents": "(!) incident enregistre"}
NAMES = {"nightops": "Night Ops", "supply": "Supply", "whm": "Événement WHM", "aa": "Agency Assessment",
         "herotrial": "Hero Trial", "histoire": "Histoire"}
SUPPLY = {"food": "Cafeteria", "money": "Bounty", "gear": "Data Collection", "card": "Reinforcement", "crystals": "Recovery"}
STATUS = {"fini": "✅ fini", "arret": "⚠️ arrêté", "plantage": "❌ plantage", "stop": "⏹️ stoppé", "interrompu": "⏹️ interrompu"}

_cur = {"id": None, "t0": 0.0, "done": None, "total": None, "count": {}}


def _arg(argv, name):
    try:
        return argv[argv.index(name) + 1]
    except (ValueError, IndexError):
        return None


def label(script, argv):
    """Nom lisible du mode : 'Supply Bounty niv. 4', 'Événement WHM Hallway'..."""
    name = NAMES.get(script, script)
    if script == "supply":
        m, lv = _arg(argv, "--mode"), _arg(argv, "--level")
        name += f" {SUPPLY.get(m, m or '')}" + (f" niv. {lv}" if lv else "")
    elif script == "whm":
        ev = _arg(argv, "--event")
        name += f" {ev.capitalize()}" if ev else ""
    return name.strip()


def _write(rec):
    try:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _trim():
    try:
        lines = FILE.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_LINES:
            FILE.write_text("\n".join(lines[-MAX_LINES // 2:]) + "\n", encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------- ecriture (appelee par journal.py)
def begin(script, argv):
    if script in NOT_STATS or any(a in argv for a in SKIP_ARGS):
        return
    _trim()
    _cur.update(id=f"{os.getpid()}-{int(time.time() * 1000)}", t0=time.time(), done=None, total=None,
                count={k: 0 for k in COUNTERS})
    _write({"id": _cur["id"], "ev": "debut", "t": round(_cur["t0"]), "script": script, "mode": label(script, argv),
            "args": " ".join(argv)})


def line(text):
    """Chaque ligne affichee par le script : progression et compteurs."""
    if not _cur["id"]:
        return
    for k, pat in COUNTERS.items():
        if pat in text:
            _cur["count"][k] += 1
    for rx in PROGRESS:
        m = rx.search(text)
        if m:
            done, total = int(m.group(1)), int(m.group(2))
            if (done, total) != (_cur["done"], _cur["total"]):
                _cur["done"], _cur["total"] = done, total
                _write({"id": _cur["id"], "ev": "progres", "t": round(time.time()), "done": done, "total": total,
                        **_cur["count"]})
            break


def end(status, message=""):
    if not _cur["id"]:
        return
    _write({"id": _cur["id"], "ev": "fin", "t": round(time.time()), "status": status, "message": str(message)[:200],
            "done": _cur["done"], "total": _cur["total"], **_cur["count"]})
    _cur["id"] = None


# ---------------------------------------------------------------- lecture
def sessions(path=None):
    """-> liste de sessions (du plus ancien au plus recent), chacune : dict fusionne + duree + status."""
    out = {}
    try:
        lines = Path(path or FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for l in lines:
        try:
            r = json.loads(l)
        except ValueError:
            continue
        s = out.setdefault(r.get("id"), {"status": "interrompu"})
        if r.get("ev") == "debut":
            s.update(start=r["t"], script=r.get("script"), mode=r.get("mode"), args=r.get("args", ""))
        s.update({k: v for k, v in r.items() if k not in ("ev", "t", "id")})
        s["last"] = r.get("t", s.get("last"))
    res = [s for s in out.values() if "start" in s]
    for s in res:
        s["duration"] = max(0, (s.get("last") or s["start"]) - s["start"])
    return sorted(res, key=lambda s: s["start"])


def summary(days=7, now=None, path=None):
    """Totaux sur les `days` derniers jours (0 = tout) : globaux et par mode."""
    now = now or time.time()
    ss = [s for s in sessions(path) if not days or s["start"] >= now - days * 86400]

    def agg(group):
        n = len(group)
        return {"sessions": n, "runs": sum(s.get("done") or 0 for s in group),
                "duree": sum(s["duration"] for s in group),
                "reussite": round(100 * sum(s["status"] == "fini" for s in group) / n) if n else 0,
                **{k: sum(s.get(k) or 0 for s in group) for k in COUNTERS}}
    modes = {}
    for s in ss:
        modes.setdefault(s.get("mode") or s.get("script"), []).append(s)
    return {"total": agg(ss), "modes": {m: agg(g) for m, g in sorted(modes.items(), key=lambda kv: -len(kv[1]))},
            "dernieres": ss[-15:][::-1]}


def hms(sec):
    sec = int(sec)
    return f"{sec // 3600}h{sec % 3600 // 60:02d}" if sec >= 3600 else f"{sec // 60} min"


def describe(s):
    """Une session en une ligne lisible."""
    d = time.strftime("%d/%m %H:%M", time.localtime(s["start"]))
    runs = f"{s.get('done')}/{s.get('total')}" if s.get("total") else "-"
    extra = ", ".join(f"{s[k]} {k}" for k in ("recharges", "relances", "reprises") if s.get(k))
    return f"{d}  {s.get('mode', '?')}  ·  {hms(s['duration'])}  ·  runs {runs}  ·  {STATUS.get(s['status'], s['status'])}" + (
        f"  ·  {extra}" if extra else "")


if __name__ == "__main__":
    for days, title in ((1, "Dernières 24 h"), (7, "7 derniers jours"), (0, "Depuis le début")):
        t = summary(days)["total"]
        print(f"{title:<17} {t['sessions']} session(s), {t['runs']} runs, {hms(t['duree'])} de farm, {t['reussite']} % finies")
    print("\nDernières sessions :")
    for s in summary(0)["dernieres"]:
        print("  " + describe(s))
