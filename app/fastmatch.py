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
import sys

import cv2
import numpy as np

FAST = "--slow-match" not in sys.argv
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


def peak(scr, tpl):
    """-> (score, (x, y)) du meilleur endroit, ou None si l'image est plus grande que la zone."""
    if scr is None or tpl is None or scr.shape[0] < tpl.shape[0] or scr.shape[1] < tpl.shape[1]:
        return None
    _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(scr, tpl, cv2.TM_CCOEFF_NORMED))
    return (0.0 if np.isnan(score) else float(score)), loc


def prepare(t):
    """A appeler apres avoir rempli t.img : prepare les versions noir et blanc / demi-echelle de l'image cherchee."""
    t.last = None
    t.gimg = t.simg = None
    if t.img is None or t.img.ndim != 3:       # image comparee en masque blanc (bouton AUTO) : deja en 1 canal
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
    r = peak(t.prep(screen), t.img)
    return (0.0, (0, 0)) if r is None else r


def best(t, screen):
    """Meilleur score de l'image t dans la capture. t doit avoir : img, gimg, simg, thr, last, prep()."""
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
