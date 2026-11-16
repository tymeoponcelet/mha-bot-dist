"""Instances MuMu (plusieurs comptes) : liste, nom lisible, adresse ADB propre a chaque instance.

    python instances.py        # instances demarrees

- Avec MuMuManager : chaque instance demarree a SON port ADB (127.0.0.1:<port>) -> aucune confusion entre comptes.
- Sans MuMuManager (autre emulateur) : les appareils vus par `adb devices`.
- Un farm vise une instance par la variable d'environnement MHA_DEVICE (posee par l'assistant / Discord) : le script
  utilise CETTE instance et ne se rabat jamais sur une autre (sinon il jouerait sur le mauvais compte).
"""
import os  # noqa: E402
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")   # memoire : sinon numpy reserve ~370 Mo par processus (16/11/2026 : « Insufficient memory » chez un ami)
import json
import os
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ENV = "MHA_DEVICE"


def _run(args, timeout=15):
    try:
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=timeout,
                           creationflags=NO_WINDOW)
        return r.returncode, r.stdout
    except Exception as e:                    # noqa: BLE001
        return -1, str(e)


def adb_path():
    try:
        c = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        c = {}
    for p in (c.get("adb"), str(BASE / "platform-tools" / "adb.exe"), r"C:\platform-tools\adb.exe"):
        if p and Path(p).exists():
            return p
    return "adb"


def devices(adb=None):
    code, out = _run([adb or adb_path(), "devices"])
    return [l.split()[0] for l in out.splitlines()[1:] if l.strip().endswith("device")] if code == 0 else []


_cache = {"t": 0.0, "items": None}


def listing(adb=None, connect=True):
    """-> [{'serial', 'name', 'index'}] des instances demarrees (index None hors MuMu).
    connect=False : resultat garde 60 s (affichage frequent, MuMuManager est lent a interroger)."""
    import time
    if not connect and _cache["items"] is not None and time.time() - _cache["t"] < 60:
        return _cache["items"]
    items = _listing(adb, connect)
    _cache.update(t=time.time(), items=items)
    return items


def _listing(adb=None, connect=True):
    import emulateur
    adb = adb or adb_path()
    out = []
    mgr = emulateur.manager()
    if mgr:
        for k, v in sorted(emulateur.instances(mgr).items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 99):
            if v.get("is_android_started") and v.get("adb_port"):
                serial = f"{v.get('adb_host_ip') or '127.0.0.1'}:{v['adb_port']}"
                out.append({"serial": serial, "name": (v.get("name") or f"MuMu {k}").strip(), "index": str(k)})
        if out and connect:
            seen = devices(adb)
            for i in out:
                if i["serial"] not in seen:
                    _run([adb, "connect", i["serial"]], timeout=8)
        if out:
            return out
    return [{"serial": d, "name": d, "index": None} for d in devices(adb)]


def label(serial, items=None):
    """'MuMu 1 · Android Device-1' (ou l'adresse si inconnue)."""
    for i in items if items is not None else listing(connect=False):
        if i["serial"] == serial:
            return f"MuMu {i['index']} · {i['name']}" if i["index"] is not None else i["name"]
    return serial or "émulateur"


def wanted():
    """Instance demandee pour CE script (MHA_DEVICE), ou None = comportement habituel (config.json)."""
    return os.environ.get(ENV) or None


if __name__ == "__main__":
    items = listing()
    for i in items:
        print(f"{label(i['serial'], items):<40} {i['serial']}")
    if not items:
        print("Aucune instance demarree.")
