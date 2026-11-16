"""
Reconnaissance d'images RAPIDE, partagee par tous les scripts (auto_*.py, nav.py, refill.py, watchdog.py).

Pourquoi : chercher une image en couleur sur toute la capture coute ~250 ms. Avec 13 images a chercher, un tour de
boucle prenait ~3 s -> le bot reagissait lentement (Continue Challenge, fin de combat...).

Comment, en 3 temps :
  1. l'image deja trouvee au tour d'avant est cherchee d'abord AUTOUR de son dernier emplacement (quasi gratuit) ;
  2. sinon, reperage rapide sur toute la capture en noir et blanc et en demi-echelle (~15 ms au lieu de 250) ;
  3. des que le reperage approche du seuil, on VERIFIE en couleur dans une petite fenetre : le score final est
     exactement celui de l'ancienne methode (mesure : 0 difference sur 19 images x 8 ecrans, ~13x plus vite).
Si le reperage etait sur de lui mais que la verification echoue (cas rare : deux elements identiques a l'ecran),
on repasse automatiquement en couleur sur toute la capture : jamais de perte de fiabilite.

Pour revenir a l'ancienne methode (tout en couleur, lent) : lancer le bot avec  --slow-match
"""
import os  # noqa: E402
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")   # memoire : sinon numpy reserve ~370 Mo par processus (16/11/2026 : « Insufficient memory » chez un ami)
import sys
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

import derive

FAST = "--slow-match" not in sys.argv
WORKERS = 4                    # recherches en parallele (OpenCV libere le GIL) : ~3x plus vite, resultats identiques
_POOL = None
COARSE_SCALE = 0.5             # echelle du reperage rapide
COARSE_MARGIN = 0.15           # on verifie en couleur des que le reperage depasse (seuil - cette marge)
TRACK_MARGIN = 24              # px cherches autour du dernier emplacement connu
MIN_COARSE = 14                # px : en dessous, l'image est trop petite pour la demi-echelle
_CACHE = {"key": None}


def screen_cache(screen):
    """Versions noir et blanc / demi-echelle de la capture : calculees UNE fois par tour, partagees par toutes les images."""
    key = (screen.__array_interface__["data"][0], screen.shape, int(screen[::97, ::151, 0].sum()))
    if _CACHE["key"] != key:
        g = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY) if screen.ndim == 3 else screen
        _CACHE.update(key=key, gray=g,
                      small=cv2.resize(g, None, fx=COARSE_SCALE, fy=COARSE_SCALE, interpolation=cv2.INTER_AREA))
    return _CACHE["gray"], _CACHE["small"]


def peak(scr, tpl, mask=None):
    """-> (score, (x, y)) du meilleur endroit, ou None si l'image est plus grande que la zone.
    mask : ne compare que les pixels de l'icone (fond de l'image de reference ignore, voir background_mask)."""
    if scr is None or tpl is None or scr.shape[0] < tpl.shape[0] or scr.shape[1] < tpl.shape[1]:
        return None
    if mask is None:
        _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(scr, tpl, cv2.TM_CCOEFF_NORMED))
        return (0.0 if np.isnan(score) else float(score)), loc
    res = np.nan_to_num(cv2.matchTemplate(scr, tpl, cv2.TM_CCOEFF_NORMED, mask=mask), nan=-1.0, posinf=-1.0, neginf=-1.0)
    _, score, _, loc = cv2.minMaxLoc(res)
    return float(score), loc


def background_mask(img, tol=60):
    """Masque des pixels de l'ICONE : tout ce qui differe de la couleur du bord (le fond uni de la decoupe).
    Pour les boutons poses sur la ville : le decor change a chaque deplacement, on ne le compare plus."""
    border = np.concatenate([img[0], img[-1], img[:, 0], img[:, -1]]).astype(np.int32)
    bg = np.median(border, axis=0)
    m = (np.abs(img.astype(np.int32) - bg).sum(axis=2) > tol).astype(np.uint8) * 255
    return cv2.dilate(m, np.ones((3, 3), np.uint8))


def prepare(t):
    """A appeler apres avoir rempli t.img : prepare les versions noir et blanc / demi-echelle de l'image cherchee."""
    t.last = None
    t.gimg = t.simg = None
    if t.img is None or t.img.ndim != 3:       # image comparee en masque blanc (bouton AUTO) : deja en 1 canal
        return
    if getattr(t, "mask", None) is not None:   # fond masque : comparaison couleur complete (pas de reperage rapide)
        return
    t.gimg = cv2.cvtColor(t.img, cv2.COLOR_BGR2GRAY)
    h, w = (max(1, int(round(v * COARSE_SCALE))) for v in t.gimg.shape[:2])
    t.simg = cv2.resize(t.gimg, (w, h), interpolation=cv2.INTER_AREA) if min(h, w) >= MIN_COARSE else None


def exact(t, screen, x, y):
    """Score COULEUR exact dans une petite fenetre autour de (x, y)."""
    h, w = t.img.shape[:2]
    m = TRACK_MARGIN
    ya, xa = max(0, y - m), max(0, x - m)
    win = t.prep(screen[ya:min(screen.shape[0], y + h + m), xa:min(screen.shape[1], x + w + m)])
    r = peak(win, t.img)
    return (0.0, (x, y)) if r is None else (r[0], (r[1][0] + xa, r[1][1] + ya))


def full(t, screen):
    """Ancienne methode : comparaison couleur sur TOUTE la capture."""
    r = peak(t.prep(screen), t.img, getattr(t, "mask", None))
    return (0.0, (0, 0)) if r is None else r


def best(t, screen):
    """Meilleur score de l'image t dans la capture. t doit avoir : img, gimg, simg, thr, last, prep().
    Chaque detection (score >= seuil) est notee pour la detection de derive (derive.py)."""
    score, loc = _best(t, screen)
    if score >= t.thr:
        derive.note(t, score)
    elif score >= t.thr - derive.NEAR:
        derive.near(t, score)                             # presque reconnu : capture pour comprendre (logs/presque/)
    return score, loc


def _best(t, screen):
    if not FAST or getattr(t, "gimg", None) is None:
        return full(t, screen)
    if t.last is not None:                               # 1) deja vue : on regarde autour de la derniere fois
        score, loc = exact(t, screen, *t.last)
        if score >= t.thr:
            t.last = loc
            return score, loc
    gray, small = screen_cache(screen)                   # 2) reperage rapide sur toute la capture
    if t.simg is not None:
        r = peak(small, t.simg)
        guess = None if r is None else (int(r[1][0] / COARSE_SCALE), int(r[1][1] / COARSE_SCALE))
    else:
        r = peak(gray, t.gimg)
        guess = None if r is None else r[1]
    if r is None:
        return 0.0, (0, 0)
    if r[0] < t.thr - COARSE_MARGIN:                     # tres loin du seuil : inutile de verifier en couleur
        t.last = None
        return r[0], guess
    score, loc = exact(t, screen, *guess)                # 3) verification en couleur : score exact
    if score < t.thr <= r[0]:                            # le reperage etait sur de lui : on verifie partout, en couleur
        t.last = None
        return full(t, screen)
    t.last = loc if score >= t.thr else None
    return score, loc


def find_many(items, screen):
    """items = [(nom, image), ...] -> {nom: image.find(screen)} dans le MEME ordre, recherches en parallele.
    Meme resultat qu'une boucle (chaque image n'a que son propre etat, t.last). Une meme image presente deux fois,
    ou --slow-match / WORKERS = 1 : on reste en sequentiel."""
    global _POOL
    items = list(items)
    if WORKERS <= 1 or len(items) < 2 or len({id(t) for _, t in items}) != len(items):
        return {k: t.find(screen) for k, t in items}
    if screen is not None and screen.ndim == 3:
        screen_cache(screen)                              # prepare UNE fois avant les fils (sinon chacun la recalcule)
    if _POOL is None:
        _POOL = ThreadPoolExecutor(WORKERS, thread_name_prefix="fastmatch")
    return dict(zip((k for k, _ in items), _POOL.map(lambda kt: kt[1].find(screen), items)))
