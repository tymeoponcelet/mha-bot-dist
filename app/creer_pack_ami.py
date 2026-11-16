"""
Cree "mha script.zip" : le pack a donner a un ami (bot Discord + assistant + images).
  - SANS ton token Discord (discord_config.json), SANS ton config.json (chemin ADB / appareil de TON PC), sans debug/, __pycache__...
  - Verifie ensuite le contenu du zip : si un secret (fichier ou token Discord) s'y trouve, le zip est supprime.
Utilisation : double-clic sur creer_pack_ami.bat  (ou : python creer_pack_ami.py)
"""
import os  # noqa: E402
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")   # memoire : sinon numpy reserve ~370 Mo par processus (16/11/2026 : « Insufficient memory » chez un ami)
import re
import sys
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE.parent / "mha script.zip"
EXCLUDE_FILES = {"config.json", "discord_config.json", "sequence.json", "mha script.zip", "mha-bot-ami.zip"}
EXCLUDE_DIRS = {"__pycache__", "debug", "platform-tools", ".git", ".idea", ".vscode"}
EXCLUDE_EXT = {".pyc", ".zip", ".log"}
TOKEN_RE = re.compile(rb"[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{27,}")   # forme d'un token Discord


def wanted(p):
    rel = p.relative_to(BASE)
    if any(part in EXCLUDE_DIRS for part in rel.parts[:-1]):
        return False
    return p.name not in EXCLUDE_FILES and p.suffix.lower() not in EXCLUDE_EXT


def main():
    files = sorted(p for p in BASE.rglob("*") if p.is_file() and wanted(p))
    if OUT.exists():
        OUT.unlink()
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, Path(BASE.name) / p.relative_to(BASE))
    # verification : aucun secret dans le zip
    bad = []
    with zipfile.ZipFile(OUT) as z:
        for n in z.namelist():
            if Path(n).name in ("config.json", "discord_config.json"):
                bad.append(n)
            elif n.endswith((".py", ".json", ".txt", ".bat")) and TOKEN_RE.search(z.read(n)):
                bad.append(n + " (ressemble a un token Discord)")
    if bad:
        OUT.unlink()
        print("ERREUR : secret detecte, zip SUPPRIME :", ", ".join(bad))
        sys.exit(1)
    print(f"OK : {OUT}  ({len(files)} fichiers, {OUT.stat().st_size // 1024} Ko)")
    print("Verifie : ni token, ni config.json dans le zip.")
    print("Envoie ce zip a ton ami, et le TOKEN du bot en MESSAGE PRIVE (jamais dans un salon). Il suit GUIDE_AMI.txt.")


if __name__ == "__main__":
    main()
