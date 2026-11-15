"""Journal de session (logs/) pour tous les scripts.

    import journal
    journal.start("nightops")          # au tout debut du script (dans if __name__ == "__main__")

- Tout ce qui s'affiche (print, log, erreurs) est AUSSI ecrit dans logs/AAAAMMJJ-HHMMSS_<script>.log.
  L'affichage ne change pas (le bot Discord et l'assistant lisent toujours la sortie du script).
- Un seul fichier par session : un script lance par enchainer.py ecrit dans le journal de l'enchainement
  (l'enchaineur recopie deja ses lignes ; variable d'environnement MHA_LOG).
- Rotation : on garde les KEEP derniers journaux.
- Les dernieres lignes restent aussi en memoire (tail()) pour les rapports d'incident.
"""
import collections
import os
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOG_DIR = BASE / "logs"
KEEP = 10                                  # journaux de session conserves
ENV_LOG = "MHA_LOG"                        # chemin du journal de la session en cours (transmis aux scripts enfants)

_lines = collections.deque(maxlen=200)     # dernieres lignes affichees (memoire)
_state = {"started": False, "file": None, "path": None, "script": None, "stats": None, "encours": None}
_lock = threading.Lock()


class _Tee:
    """Remplace sys.stdout / sys.stderr : affiche comme avant, et recopie dans le journal + en memoire."""

    def __init__(self, stream):
        self._s = stream
        self._buf = ""

    def write(self, s):
        n = self._s.write(s) if self._s else len(s)
        with _lock:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                _record(line)
        return n

    def flush(self):
        if self._s:
            self._s.flush()
        f = _state["file"]
        if f:
            try:
                f.flush()
            except (OSError, ValueError):
                pass

    def __getattr__(self, name):           # encoding, isatty, reconfigure... : ceux du vrai flux
        return getattr(self._s, name)


def _record(line):
    line = line.rstrip("\r")
    _lines.append(line)
    if _state["stats"]:
        try:
            _state["stats"].line(line)          # statistiques (stats.py) : progression, recharges, relances...
        except Exception:                       # noqa: BLE001
            pass
    if _state["encours"]:
        try:
            _state["encours"].line(line)        # farm en cours, vu par l'assistant et Discord (encours.py)
        except Exception:                       # noqa: BLE001
            pass
    f = _state["file"]
    if f:
        try:
            f.write(line + "\n")
        except (OSError, ValueError):
            pass


def rotate(folder, pattern, keep):
    """Supprime les plus anciens elements de folder (noms qui commencent par la date : tri alphabetique = chronologique)."""
    try:
        items = sorted(folder.glob(pattern))
    except OSError:
        return
    for old in items[:-keep] if keep > 0 else items:
        try:
            if old.is_dir():
                for p in sorted(old.rglob("*"), reverse=True):
                    p.unlink() if p.is_file() else p.rmdir()
                old.rmdir()
            else:
                old.unlink()
        except OSError:
            pass


def start(script):
    """Demarre le journal de session. Sans effet si deja fait. Ne leve jamais d'erreur (le farm passe avant)."""
    if _state["started"]:
        return _state["path"]
    _state.update(started=True, script=script)
    parent = os.environ.get(ENV_LOG)
    try:
        if parent:                         # lance par enchainer.py : il recopie deja nos lignes dans SON journal
            _state["path"] = Path(parent)
        else:
            LOG_DIR.mkdir(exist_ok=True)
            path = LOG_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}_{script}.log"
            _state["file"] = open(path, "a", encoding="utf-8", buffering=1)
            _state["path"] = path
            os.environ[ENV_LOG] = str(path)
            rotate(LOG_DIR, "*.log", KEEP)
            _state["file"].write(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} | {script} | {' '.join(sys.argv[1:])}\n")
    except OSError as e:
        print(f"(!) journal impossible dans logs/ ({e}) : le bot continue sans.", flush=True)
    try:
        import stats                            # historique des sessions (logs/runs.jsonl)
        stats.begin(script, sys.argv[1:])
        _state["stats"] = stats
    except Exception:                           # noqa: BLE001
        pass
    if not parent and not any(a in sys.argv[1:] for a in ("--test", "--list", "--swipe", "--capture", "--dry")):
        try:
            import encours                      # farm en cours partage (assistant / Discord / planning)
            encours.declare(script, _label(script), _state["path"])
            _state["encours"] = encours
        except Exception:                       # noqa: BLE001
            pass
    sys.stdout = _Tee(sys.stdout)
    sys.stderr = _Tee(sys.stderr)
    return _state["path"]


def _label(script):
    """Nom lisible du farm qui demarre (pour l'assistant et Discord)."""
    import stats
    args = sys.argv[1:]
    if script != "enchainement":
        return stats.label(script, args)
    try:
        import enchainer as ec
        steps = [ec.label(ec.validate(ec.parse_token(t))) for t in args if not t.startswith("--")]
        return "Enchaînement : " + " → ".join(steps) if steps else "Enchaînement (sequence.json)"
    except BaseException:                       # noqa: BLE001 - SystemExit compris
        return "Enchaînement"


def _end(status, message=""):
    if _state["stats"]:
        try:
            _state["stats"].end(status, message)
        except Exception:                       # noqa: BLE001
            pass


def tail(n=10):
    """Les n dernieres lignes affichees par ce script."""
    with _lock:
        return [l for l in _lines if l.strip()][-n:]


def path():
    return _state["path"]


# ======================= BOITE NOIRE + INCIDENTS =======================
# Les FRAMES dernieres captures sont gardees en miniature, avec la ligne de journal du moment.
# Incident (plantage, arret anormal, blocage, ADB) -> logs/incidents/<date>_<script>/ :
#   capture.png (derniere capture en taille reelle), boite_noire.jpg (planche des dernieres captures),
#   fin_journal.txt (10 dernieres lignes), incident.json (raison...). Le bot Discord les envoie (voir discord_bot.py).
INC_DIR = LOG_DIR / "incidents"
FRAMES = 30
THUMB = (320, 180)
HANG_S = 180                               # s sans aucune capture pendant que le script tourne -> "bloque"
_box = collections.deque(maxlen=FRAMES)    # (heure, miniature, ligne de journal)
_last = {"img": None, "t": 0.0, "hang": False, "thread": None}


def frame(img):
    """A appeler a chaque capture d'ecran (auto_nightops.screenshot / auto_hero_trial.screenshot). ~1 ms."""
    try:
        import cv2
        _last["img"], _last["t"] = img, time.time()
        if _last["hang"]:
            _last["hang"] = False
            print("(i) les captures ont repris.", flush=True)
        small = cv2.resize(img, THUMB, interpolation=cv2.INTER_AREA)
        with _lock:
            note = next((l for l in reversed(_lines) if l.strip()), "")
        _box.append((time.time(), small, note))
        if _state["started"] and _last["thread"] is None:
            _last["thread"] = threading.Thread(target=_watch_hang, daemon=True, name="journal-blocage")
            _last["thread"].start()
    except Exception:                      # noqa: BLE001 - la boite noire ne doit jamais gener le farm
        pass


def _watch_hang():
    while True:
        time.sleep(10)
        if not _last["hang"] and _last["t"] and time.time() - _last["t"] > HANG_S:
            _last["hang"] = True
            incident(f"Bloqué : aucune capture d'écran depuis {HANG_S // 60} min (le script ne répond plus ?)", live=True)


def _sheet():
    """Planche des dernieres captures (6 colonnes), heure et ligne de journal sous chaque image."""
    import cv2
    import numpy as np
    items = list(_box)
    if not items:
        return None
    cols = 6
    w, h = THUMB
    cap = 34
    rows = (len(items) + cols - 1) // cols
    sheet = np.full((rows * (h + cap), cols * w, 3), 24, np.uint8)
    for i, (t, img, note) in enumerate(items):
        x, y = (i % cols) * w, (i // cols) * (h + cap)
        sheet[y:y + h, x:x + w] = img
        cv2.putText(sheet, time.strftime("%H:%M:%S", time.localtime(t)), (x + 4, y + h + 13),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (120, 230, 255), 1, cv2.LINE_AA)
        txt = note.encode("ascii", "replace").decode()[-48:]
        cv2.putText(sheet, txt, (x + 4, y + h + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (220, 220, 220), 1, cv2.LINE_AA)
    return sheet


def incident(reason, live=False, kind="incident"):
    """Enregistre un incident (voir plus haut). live=True : le script tourne encore (blocage). -> dossier ou None."""
    import json
    try:
        import cv2
        INC_DIR.mkdir(parents=True, exist_ok=True)
        d = INC_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}_{_state['script'] or 'script'}"
        n = 2
        while d.exists():
            d = d.with_name(f"{d.name.split('~')[0]}~{n}")
            n += 1
        d.mkdir()
        if _last["img"] is not None:
            cv2.imwrite(str(d / "capture.png"), _last["img"])
        sheet = _sheet()
        if sheet is not None:
            cv2.imwrite(str(d / "boite_noire.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 80])
        lines = tail(10)
        (d / "fin_journal.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        info = {"reason": str(reason)[:900], "script": _state["script"], "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "live": live, "kind": kind, "discord_session": os.environ.get("MHA_DISCORD") == "1",
                "log": str(_state["path"] or ""), "pid": os.getpid()}
        (d / "incident.json").write_text(json.dumps(info, indent=1, ensure_ascii=False), encoding="utf-8")
        rotate(INC_DIR, "2*", KEEP)
        print(f"(!) incident enregistre : logs/incidents/{d.name} ({str(reason)[:80]})", flush=True)
        return d
    except Exception as e:                 # noqa: BLE001
        print(f"(!) incident non enregistre ({e!r})", flush=True)
        return None


def run(main, Done=None, stop_msg="Arrete."):
    """Point d'entree des scripts : meme affichage et meme code de sortie qu'avant, + incident si arret anormal.
    Done "...Fini !" = succes ; tout autre Done, plantage ou SystemExit avec message = incident."""
    try:
        main()
        _end("fini" if any("Fini" in l for l in tail(3)) else "arret", (tail(1) or [""])[0])
    except KeyboardInterrupt:
        print(stop_msg)
        _end("stop")
    except SystemExit as e:
        if isinstance(e.code, str) and e.code:
            incident(e.code, kind="arret")
            _end("arret", e.code)
        else:
            _end("fini" if any("Fini" in l for l in tail(3)) else "arret", (tail(1) or [""])[0])
        raise
    except Exception as e:                 # noqa: BLE001 - re-levee juste apres
        if Done is not None and isinstance(e, Done):
            print(e)
            if "Fini" not in str(e):
                incident(str(e), kind="arret")
            _end("fini" if "Fini" in str(e) else "arret", str(e))
            return
        import traceback
        traceback.print_exc()
        incident(f"Plantage : {e!r}", kind="plantage")
        _end("plantage", repr(e))
        sys.exit(1)
