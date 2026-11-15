"""Refaire une image de bouton a partir d'une capture (outil « 🖼️ Images » de l'assistant).

    python refaire.py --check templates_stamina/cadeau.png capture.png    # verification seule (JSON)

- captures() : ecrans disponibles (boutons presque reconnus, incidents, debug/, tests/captures/), les plus recents d'abord.
- save_crop() : decoupe le rectangle et remplace l'image ; l'ancienne est gardee dans logs/sauvegardes_images/<date>/.
- check() : avec les MEMES zones et seuils que le bot -> score sur la capture d'origine + pire confusion sur les autres
  captures (une autre image trop proche = faux positif possible).
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SKIP = {"templates_aa/floors"}


def templates():
    """Toutes les images de boutons : 'templates_supply/confirm.png', ..."""
    out = []
    for d in sorted(BASE.glob("templates*")):
        if d.is_dir():
            for p in sorted(d.rglob("*.png")):
                rel = p.relative_to(BASE).as_posix()
                if not any(rel.startswith(s) for s in SKIP):
                    out.append(rel)
    return out


def captures():
    """-> [(chemin, libelle, image suggeree ou None)] du plus recent au plus ancien."""
    items = []
    idx = {}
    try:
        for l in (BASE / "logs" / "presque" / "index.jsonl").read_text(encoding="utf-8").splitlines():
            e = json.loads(l)
            idx[e["fichier"]] = e
    except (OSError, ValueError):
        pass
    for p in (BASE / "logs" / "presque").glob("2*.png"):
        e = idx.get(p.name, {})
        items.append((p, f"presque reconnu : {e.get('image', '?')} {e.get('score', '')}", e.get("image")))
    for p in (BASE / "logs" / "incidents").glob("*/capture.png"):
        items.append((p, f"incident : {p.parent.name}", None))
    for p in list((BASE / "debug").glob("*.png")) + list((BASE / "tests" / "captures").glob("*.png")):
        items.append((p, f"{p.parent.name} : {p.name}", None))
    items = [i for i in items if i[0].exists()]
    return sorted(items, key=lambda i: i[0].stat().st_mtime, reverse=True)


def save_crop(capture, box, target):
    """Decoupe box=(x0, y0, x1, y1) (pixels de la capture) et ecrit BASE/target. -> chemin de la sauvegarde (ou None)."""
    import cv2
    img = cv2.imread(str(capture), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"capture illisible : {capture}")
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0, x1 = sorted((max(0, x0), min(img.shape[1], x1)))
    y0, y1 = sorted((max(0, y0), min(img.shape[0], y1)))
    if x1 - x0 < 8 or y1 - y0 < 8:
        raise ValueError("Rectangle trop petit : trace-le autour du bouton (8 px minimum).")
    dst = BASE / target
    if not str(dst.resolve()).startswith(str(BASE.resolve())) or not target.startswith("templates") or not target.endswith(".png"):
        raise ValueError(f"cible refusee : {target}")
    backup = None
    if dst.exists():
        backup = BASE / "logs" / "sauvegardes_images" / time.strftime("%Y%m%d-%H%M%S") / target
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dst, backup)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(dst), img[y0:y1, x0:x1]):
        raise ValueError(f"ecriture impossible : {dst}")
    return backup


def _objects():
    """{'templates_supply/confirm.png': objet image du bot (memes crop / seuil)}."""
    sys.argv = sys.argv[:1]
    import auto_nightops as nt  # noqa: F401
    import nav
    import refill
    import watchdog
    noop = lambda *a, **k: None               # noqa: E731
    objs = {}
    for k, t in nav.Home(noop, noop, noop).t.items():
        objs[getattr(t, "name", k)] = t
    for k, t in refill.Refill(noop, noop, noop, noop, Exception).t.items():
        objs[getattr(t, "name", k)] = t
    for k, t in watchdog.Watchdog(noop, noop, noop, noop, Exception).t.items():
        objs[getattr(t, "name", k)] = t
    for mod in ("auto_nightops", "auto_supply", "auto_whm", "auto_aa", "auto_hero_trial"):
        try:
            m = __import__(mod)
            m.load_templates(strict=False)
            for t in m.T.values():
                n = getattr(t, "name", None) or getattr(getattr(getattr(t, "picker", None), "target", None), "name", None)
                if n:
                    objs.setdefault(n, t)
        except BaseException:                 # noqa: BLE001 - un mode qui ne charge pas n'empeche pas la verification
            pass
    return objs


def check(target, capture):
    """-> dict : score sur la capture, seuil, pire confusion ailleurs (fichier, score)."""
    import cv2
    import fastmatch
    fastmatch.FAST = False
    t = _objects().get(target)
    if t is None:                             # image pas (encore) utilisee par un mode : comparaison simple
        class _Raw:
            pass
        t = _Raw()
        t.img, t.thr, t.prep = cv2.imread(str(BASE / target)), 0.85, (lambda im: im)
        t.best = lambda s, t=t: fastmatch.full(t, s)
    res = {"image": target, "seuil": float(t.thr)}
    s = cv2.imread(str(capture), cv2.IMREAD_COLOR)
    res["score"] = round(float(t.best(s)[0]), 3) if s is not None else None
    worst = (0.0, None)
    for p, _lbl, _sug in captures():
        if Path(p).resolve() == Path(capture).resolve():
            continue
        o = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if o is None or o.shape != s.shape:
            continue
        if hasattr(t, "last"):
            t.last = None
        v = float(t.best(o)[0])
        if v > worst[0]:
            worst = (v, Path(p).name)
    res["ailleurs_max"], res["ailleurs_fichier"] = round(worst[0], 3), worst[1]
    res["ok"] = bool(res["score"] is not None and res["score"] >= t.thr)
    return res


def check_subprocess(target, capture):
    """Verification dans un processus a part (l'assistant ne charge pas tous les modes)."""
    r = subprocess.run([sys.executable, str(BASE / "refaire.py"), "--check", target, str(capture)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=180, creationflags=NO_WINDOW, cwd=str(BASE))
    for l in reversed(r.stdout.splitlines()):
        if l.startswith("{"):
            return json.loads(l)
    raise RuntimeError((r.stdout + r.stderr)[-400:] or "verification impossible")


if __name__ == "__main__":
    if "--check" in sys.argv:
        i = sys.argv.index("--check")
        print(json.dumps(check(sys.argv[i + 1], sys.argv[i + 2]), ensure_ascii=False))
