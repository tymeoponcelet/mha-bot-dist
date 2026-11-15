"""Commande Discord /maj : met le bot a jour depuis le depot GitHub, puis le redemarre si du code a change.

Branchement dans discord_bot.py : 2 lignes, juste AVANT  if __name__ == "__main__":
    import maj_cmd
    maj_cmd.setup(cmd, embed, runner, BASE, EPH, COLORS, NO_WINDOW, ENV, discord, app_commands)

Seul le proprietaire (allowed_user_ids) peut l'utiliser : c'est deja verifie par Tree.interaction_check.
La fenetre MHA (hub.py) relance le bot tout de suite quand il quitte avec le code 3.
"""
import asyncio
import json
import os
import subprocess
import sys

RESTART_CODE = 3


def _run_updater(base, env, no_window, check):
    args = [sys.executable, "-u", str(base / "updater.py"), "--check" if check else "--update", "--root", str(base.parent)]
    r = subprocess.run(args, cwd=base, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env, creationflags=no_window, timeout=900)
    result = None
    for line in (r.stdout or "").splitlines():
        if line.startswith("RESULT "):
            try:
                result = json.loads(line[7:])
            except ValueError:
                pass
    return result, ((r.stdout or "") + (r.stderr or ""))[-600:]


def setup(cmd, embed, runner, BASE, EPH, COLORS, NO_WINDOW, ENV, discord, app_commands):
    @cmd(name="maj", description="⬇️ Met à jour le bot (scripts et images) depuis le dépôt du créateur")
    @app_commands.describe(verifier="Oui = seulement regarder s'il y a du nouveau, sans rien installer",
                           forcer="Oui = mettre à jour même si un script de farm tourne (il sera arrêté si le bot redémarre)")
    async def maj_command(interaction: discord.Interaction, verifier: bool = False, forcer: bool = False):
        if not (BASE / "updater.py").exists():
            await interaction.response.send_message(embed=embed(
                "⬇️ Mise à jour indisponible", "Ce bot n'a pas été installé avec le lanceur MHA (updater.py absent).",
                COLORS["bad"]), ephemeral=EPH)
            return
        if runner.running and not verifier and not forcer:
            await interaction.response.send_message(embed=embed(
                "⬇️ Un script tourne", f"{runner.emoji} **{runner.label}** est en cours.\n"
                "Fais **/stop** d'abord, ou relance avec `forcer: Oui`.", COLORS["bad"]), ephemeral=EPH)
            return
        await interaction.response.defer(ephemeral=EPH)
        result, tail = await asyncio.to_thread(_run_updater, BASE, ENV, NO_WINDOW, verifier)
        if not result or result.get("error"):
            err = (result or {}).get("error") or tail or "réponse illisible"
            await interaction.followup.send(embed=embed("❌ Mise à jour impossible", f"```{err[-900:]}```",
                                                        COLORS["bad"], tip="Réessaie plus tard ou préviens le créateur."))
            return
        names = ", ".join(f"`{f}`" for f in result.get("files", [])[:8])
        more = f" (+{result['updated'] + result['removed'] - 8})" if result["updated"] + result["removed"] > 8 else ""
        if verifier:
            if result.get("available"):
                desc = (f"Une nouvelle version est disponible : **{result['version']}**"
                        f" (installée : {result.get('previous') or '?'}).\n{result.get('message') or ''}\n"
                        f"{result['updated']} fichier(s) à mettre à jour. Lance **/maj** pour l'installer.")
            else:
                desc = f"Tu as déjà la dernière version : **{result['version']}**."
            await interaction.followup.send(embed=embed("⬇️ Vérification", desc, COLORS["info"]))
            return
        if not result["updated"] and not result["removed"]:
            await interaction.followup.send(embed=embed(
                "✅ Déjà à jour", f"Version **{result['version']}**.", COLORS["info"]))
            return
        desc = (f"Version **{result['version']}** installée : {result['updated']} fichier(s) mis à jour, "
                f"{result['removed']} supprimé(s).\n{result.get('message') or ''}\n{names}{more}")
        if result.get("restart_needed"):
            await interaction.followup.send(embed=embed(
                "✅ Mis à jour : je redémarre", desc, COLORS["info"],
                tip="Je reviens dans quelques secondes (la fenêtre MHA me relance toute seule)."))
            await asyncio.sleep(1)
            runner.stop()                            # un farm en cours (forcer) est arrete proprement avant le redemarrage
            os._exit(RESTART_CODE)
        await interaction.followup.send(embed=embed("✅ Mis à jour", desc, COLORS["info"],
                                                    tip="Rien à redémarrer : seules des images ont changé."))
