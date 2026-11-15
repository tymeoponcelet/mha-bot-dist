"""
Cree discord_config.json en te posant quelques questions (appele automatiquement par lancer_discord.bat la 1re fois).
  1. le token du bot       (donne par la personne qui a cree le bot, en message prive : ne le poste jamais dans un salon)
  2. TON identifiant Discord   (Parametres > Avance > Mode developpeur, puis clic droit sur ton pseudo > Copier l'identifiant)
  3. l'identifiant du serveur  (clic droit sur le nom du serveur > Copier l'identifiant du serveur)
  4. reponses privees ou visibles par tout le serveur
"""
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE / "discord_config.json"
EXAMPLE = BASE / "discord_config.example.json"
PLACEHOLDER = 123456789012345678


def ask_id(question, default=None):
    while True:
        hint = f" [Entree = {default}]" if default else ""
        raw = input(f"{question}{hint} : ").strip()
        if not raw and default:
            return default
        if re.fullmatch(r"\d{15,21}", raw):
            return int(raw)
        print("   -> un identifiant, ce sont uniquement des chiffres (17 a 20). Exemple : 123456789012345678")


def ask_token():
    while True:
        tok = input("Token du bot : ").strip().strip('"')
        if tok.count(".") == 2 and len(tok) >= 50 and " " not in tok:
            return tok
        print("   -> ca ne ressemble pas a un token (3 morceaux separes par des points, ~70 caracteres). Recolle-le en entier.")


def ask_yes(question, default=False):
    d = "o/N" if not default else "O/n"
    raw = input(f"{question} ({d}) : ").strip().lower()
    if not raw:
        return default
    return raw in ("o", "oui", "y", "yes")


def main():
    ex = {}
    try:
        ex = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    except Exception:
        pass
    if OUT.exists() and input("discord_config.json existe deja. Le remplacer ? (o/n) ").strip().lower() not in ("o", "oui", "y"):
        return
    guild_default = ex.get("guild_id") if ex.get("guild_id") not in (None, PLACEHOLDER) else None
    print("\n=== Configuration du bot Discord ===\n")
    cfg = {
        "token": ask_token(),
        "allowed_user_ids": [ask_id("TON identifiant Discord")],
        "guild_id": ask_id("Identifiant du serveur", guild_default),
        "sync_commands": bool(ex.get("sync_commands", True)),
    }
    print("\nPar defaut, seul celui qui tape une commande voit la reponse.")
    cfg["public_replies"] = ask_yes("Afficher les reponses a TOUT le serveur ?")
    if cfg["public_replies"] and ask_yes("Annoncer aussi la FIN d'un script dans un salon (au lieu d'un message prive) ?"):
        cfg["result_channel_id"] = ask_id("Identifiant du salon (clic droit sur le salon > Copier l'identifiant)")
    OUT.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print("\ndiscord_config.json cree. Ne le partage jamais (il contient le token).")
    print("ATTENTION : cette etape ne met PAS le bot en ligne. Il faut ensuite lancer lancer_discord.bat (ou utiliser\n"
          "configurer_discord.bat, qui le fait tout seul) et laisser la fenetre ouverte : tu dois y lire \"Connecte\".\n")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nAnnule.")
        sys.exit(1)
