"""Petites fonctions identiques partagees par plusieurs modes (sans etat, sans effet de bord)."""
import os  # noqa: E402
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")   # memoire : sinon numpy reserve ~370 Mo par processus (16/11/2026 : « Insufficient memory » chez un ami)
import sys

import cv2
import numpy as np


def arg(flag, cast=int):
    """Valeur qui suit `flag` sur la ligne de commande (convertie par cast), ou None."""
    if flag in sys.argv:
        try:
            return cast(sys.argv[sys.argv.index(flag) + 1])
        except (IndexError, ValueError):
            pass
    return None


def card_sig(screen):
    """Signature de la zone des cartes (pour savoir si la liste a bouge apres un glissement)."""
    h = screen.shape[0]
    band = cv2.cvtColor(screen[int(0.15 * h):int(0.85 * h)], cv2.COLOR_BGR2GRAY)
    return cv2.resize(band, (96, 48), interpolation=cv2.INTER_AREA).astype(np.float32)
