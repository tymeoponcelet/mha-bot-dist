"""Detection de DERIVE des images : previent AVANT qu'une image ne soit plus reconnue (mise a jour du jeu...).

A chaque detection (score >= seuil), fastmatch appelle note(). Pendant un vrai farm seulement (journal demarre).
  - Fin de session : score median de chaque image -> logs/scores.json (KEEP sessions par image).
  - En direct : si une image est trouvee SUSPECT_N fois avec un score < reference - DROP (reference = mediane des
    sessions precedentes, au moins MIN_SESS), alerte Discord (incident "derive", une fois par image et par session)
    avec la capture : il est temps de refaire cette image.
"""
import os  # noqa: E402
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")   # memoire : sinon numpy reserve ~370 Mo par processus (16/11/2026 : « Insufficient memory » chez un ami)
import atexit
import json
import statistics
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parent
HIST = BASE / "logs" / "scores.json"
DROP = 0.06                     # baisse de score qui declenche l'alerte (ex. 0,97 -> 0,90)
SUSPECT_N = 3                   # detections "basses" dans la session avant d'alerter (evite un cas isole)
MIN_SESS = 3                    # sessions d'historique minimum avant de comparer
MIN_N = 5                       # detections minimum dans une session pour l'ajouter a l'historique
KEEP = 20                       # sessions gardees par image

NEAR = 0.12                    # "presque reconnu" : score entre seuil - NEAR et seuil
NEAR_DIR = BASE / "logs" / "presque"
NEAR_PER_SESSION = 15           # captures maxi par session (une par image)
NEAR_KEEP = 40                  # captures gardees sur le disque

_lock = threading.Lock()
_st = {"hist": None, "sess": {}, "sus": {}, "alerted": set(), "ref": {}, "hooked": False, "near": set()}


def _active():
    try:
        import journal
        return journal._state["started"]
    except Exception:                       # noqa: BLE001
        return False


def _load():
    if _st["hist"] is None:
        try:
            _st["hist"] = json.loads(HIST.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _st["hist"] = {}
    return _st["hist"]


def reference(name):
    """Score habituel d'une image (mediane des sessions precedentes), ou None si pas assez d'historique."""
    if name not in _st["ref"]:
        h = _load().get(name, [])
        _st["ref"][name] = statistics.median(h) if len(h) >= MIN_SESS else None
    return _st["ref"][name]


def note(t, score):
    """Appele par fastmatch.best pour chaque detection. Ne leve jamais d'erreur."""
    name = getattr(t, "name", None)
    if not name or not _active():
        return
    alert = None
    try:
        with _lock:
            if not _st["hooked"]:
                _st["hooked"] = True
                atexit.register(save)
            lst = _st["sess"].setdefault(name, [])
            if len(lst) < 2000:
                lst.append(float(score))
            ref = reference(name)
            if ref is not None and score < ref - DROP:
                n = _st["sus"][name] = _st["sus"].get(name, 0) + 1
                if n >= SUSPECT_N and name not in _st["alerted"]:
                    _st["alerted"].add(name)
                    alert = (name, score, ref, getattr(t, "thr", 0.0))
        if alert:
            import journal
            name, score, ref, thr = alert
            journal.incident(f"Image qui se dégrade : {name} trouvée à {score:.2f} au lieu de {ref:.2f} d'habitude "
                             f"(seuil {thr:.2f}). Le jeu a peut-être changé : refais cette image à partir de capture.png "
                             f"avant qu'elle ne soit plus reconnue.", live=True, kind="derive")
    except Exception:                       # noqa: BLE001 - jamais de gene pour le farm
        pass


def near(t, score):
    """Image PRESQUE reconnue pendant un vrai farm : on garde l'ecran (une fois par image et par session).
    Sert a voir quels boutons ont du mal (decor de la ville, mise a jour du jeu) avant qu'ils ne bloquent le bot."""
    name = getattr(t, "name", None)
    if not name or not _active():
        return
    with _lock:
        if name in _st["near"] or len(_st["near"]) >= NEAR_PER_SESSION:
            return
        _st["near"].add(name)
    try:
        import time
        import cv2
        import journal
        img = journal._last.get("img")
        if img is None:
            return
        NEAR_DIR.mkdir(parents=True, exist_ok=True)
        stem = f"{time.strftime('%Y%m%d-%H%M%S')}_{name.replace('/', '__').removesuffix('.png')}_{score:.2f}"
        cv2.imwrite(str(NEAR_DIR / f"{stem}.png"), img)
        with open(NEAR_DIR / "index.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"fichier": f"{stem}.png", "image": name, "score": round(float(score), 3),
                                "seuil": float(getattr(t, "thr", 0)), "script": journal._state.get("script"),
                                "heure": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False) + "\n")
        import journal as _j
        _j.rotate(NEAR_DIR, "2*.png", NEAR_KEEP)
    except Exception:                       # noqa: BLE001 - jamais de gene pour le farm
        pass


def save():
    """Fin de session : ajoute le score median de chaque image a l'historique."""
    with _lock:
        sess = {k: v for k, v in _st["sess"].items() if len(v) >= MIN_N}
        if not sess:
            return
        _st["hist"] = None                  # relit le fichier (un autre script a pu l'ecrire entre-temps)
        hist = _load()
        for k, v in sess.items():
            hist[k] = (hist.get(k, []) + [round(statistics.median(v), 3)])[-KEEP:]
        try:
            HIST.parent.mkdir(parents=True, exist_ok=True)
            tmp = HIST.with_suffix(f".{os.getpid()}.tmp")         # 2 farms en meme temps : chacun son fichier
            tmp.write_text(json.dumps(hist, indent=1, sort_keys=True), encoding="utf-8")
            tmp.replace(HIST)
        except OSError:
            pass
        _st["sess"] = {}
