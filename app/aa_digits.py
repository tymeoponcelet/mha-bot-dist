# -*- coding: utf-8 -*-
"""Lecture du numero de floor sur le permit : decoupe le masque jaune en chiffres, puis reconnait chaque chiffre
(1) par comparaison aux chiffres connus de templates_aa/digits/<chiffre>*.png, sinon (2) par sa forme "7 segments"."""
import os  # noqa: E402
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")   # memoire : sinon numpy reserve ~370 Mo par processus (16/11/2026 : « Insufficient memory » chez un ami)
from pathlib import Path
import cv2
import numpy as np

DIR = Path(__file__).resolve().parent / "templates_aa" / "digits"
NORM = (20, 32)                                  # taille normalisee d'un chiffre (l, h)
SEG = {0: "abcdef", 1: "bc", 2: "abged", 3: "abgcd", 4: "fgbc", 5: "afgcd", 6: "afgedc", 7: "abc", 8: "abcdefg", 9: "abcdfg"}
ZONES = {"a": (0, 5, 5, 15), "g": (13, 19, 5, 15), "d": (27, 32, 5, 15),          # (y0, y1, x0, x1)
         "f": (5, 13, 0, 6), "b": (5, 13, 14, 20), "e": (19, 27, 0, 6), "c": (19, 27, 14, 20)}
_known = []


def glyphs(mask):
    """Masque binaire -> liste des chiffres (images binaires recadrees), de gauche a droite."""
    m = (mask > 127).astype(np.uint8)
    rows = np.where(m.sum(1) > 0)[0]
    if len(rows) == 0:
        return []
    cols = m.sum(0) > 0
    out, x = [], 0
    W = m.shape[1]
    while x < W:
        if cols[x]:
            x1 = x
            while x1 < W and cols[x1]:
                x1 += 1
            g = m[:, x:x1]
            r = np.where(g.sum(1) > 0)[0]
            g = g[r[0]:r[-1] + 1]
            if g.shape[0] >= 10 and g.sum() >= 12:              # ignore les miettes
                out.append(g * 255)
            x = x1
        else:
            x += 1
    hmax = max((g.shape[0] for g in out), default=0)
    return [g for g in out if g.shape[0] >= 0.7 * hmax]


def norm(g):
    return (cv2.resize(g, NORM, interpolation=cv2.INTER_AREA) > 100).astype(np.uint8)


def holes(n):
    """Trous fermes du chiffre -> liste des hauteurs relatives (0 = haut, 1 = bas) de leurs centres."""
    inv = cv2.copyMakeBorder((1 - n).astype(np.uint8), 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=1)
    cnt, lab, stats, cent = cv2.connectedComponentsWithStats(inv, connectivity=4)
    out = []
    for i in range(1, cnt):
        if i != lab[0, 0] and stats[i, cv2.CC_STAT_AREA] >= 4:
            out.append((cent[i][1] - 1) / n.shape[0])
    return out


def by_shape(g):
    """Chiffre d'apres sa forme (trous + coins occupes). -> (chiffre, ecart : 0 = forme nette)"""
    h, w = g.shape[:2]
    if w / h < 0.45:
        return 1, 0
    n = norm(g)
    hs = holes(n)
    if len(hs) >= 2:
        return 8, 0
    if len(hs) == 1:
        return (9 if hs[0] < 0.42 else 6 if hs[0] > 0.58 else 0), 0
    on = lambda y0, y1, x0, x1: n[y0:y1, x0:x1].mean() > 0.45
    # les chiffres de cette police ont des "bras" aux extremites (le 3 revient a gauche en haut et en bas) :
    # on regarde donc les cotes pres du milieu, juste au-dessus et juste en dessous de la barre centrale
    f, b, e, c = on(10, 13, 0, 5), on(10, 13, 15, 20), on(19, 22, 0, 5), on(19, 22, 15, 20)
    top = n[0:4, 7:13].mean() > 0.45
    bottom = n[28:32].max(0).mean()                       # largeur occupee par le bas du chiffre
    if f and b and c and not e and not top:
        return 4, 0
    if top and bottom < 0.62 and not f:
        return 7, 0
    if f and not b:
        return 5, 0
    if not f and e and not c:
        return 2, 0
    if not f and not e:
        return 3, 0
    return 0, 3                                           # forme inconnue


def known():
    if not _known and DIR.is_dir():
        for f in sorted(DIR.glob("*.png")):
            if f.stem[:1].isdigit():
                im = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
                if im is not None:
                    _known.append((int(f.stem[0]), norm(im), im.shape[1] / im.shape[0]))
    return _known


def read_digit(g):
    """-> (chiffre, methode, confiance 0-1)"""
    n, ar = norm(g), g.shape[1] / g.shape[0]
    best = (None, 0.0)
    for d, k, kar in known():
        if abs(kar - ar) > 0.22:
            continue
        iou = float((n & k).sum()) / max(1, int((n | k).sum()))
        if iou > best[1]:
            best = (d, iou)
    if best[1] >= 0.80:
        return best[0], "connu", best[1]
    d, miss = by_shape(g)
    return d, "forme", max(0.0, 1 - miss / 3)


def read_number(mask):
    """-> (nombre ou None, details [(chiffre, methode, confiance, image)])"""
    if float((mask > 127).mean()) > 0.38:                 # zone presque toute jaune : flash / animation, pas un nombre
        return None, []
    gs = glyphs(mask)
    if not gs or len(gs) > 2:
        return None, []
    if any(g.shape[1] > 0.85 * g.shape[0] or g.shape[0] < 18 for g in gs):
        return None, []                                   # trop large ou trop petit pour etre un chiffre
    det = [read_digit(g) + (g,) for g in gs]
    if any(c < 0.6 for _d, _m, c, _g in det):
        return None, det
    return int("".join(str(d) for d, _m, _c, _g in det)), det
