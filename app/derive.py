"""Detection de DERIVE des images : previent AVANT qu'une image ne soit plus reconnue (mise a jour du jeu...).

A chaque detection (score >= seuil), fastmatch appelle note(). Pendant un vrai farm seulement (journal demarre).
  - Fin de session : score median de chaque image -> logs/scores.json (KEEP sessions par image).
  - En direct : si une image est trouvee SUSPECT_N fois avec un score < reference - DROP (reference = mediane des
    sessions precedentes, au moins MIN_SESS), alerte Discord (incident "derive", une fois par image et par session)
    avec la capture : il est temps de refaire cette image.
"""
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

_lock = threading.Lock()
_st = {"hist": None, "sess": {}, "sus": {}, "alerted": set(), "ref": {}, "hooked": False}


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
            tmp = HIST.with_suffix(".tmp")
            tmp.write_text(json.dumps(hist, indent=1, sort_keys=True), encoding="utf-8")
            tmp.replace(HIST)
        except OSError:
            pass
        _st["sess"] = {}
