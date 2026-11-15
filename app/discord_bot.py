"""
Bot Discord : pilote les bots MHA depuis ton telephone (le PC et l'emulateur doivent rester allumes).

Une commande PAR MODE : tu ne vois que les options qui servent a ce mode.
  /supply      mode a farmer + niveau + nombre de multi-runs + perso          (options Supply uniquement)
  /nightops    nombre de multi-runs + perso                                   (pas d'option Supply)
  /whm         evenement "Assault on Humarise Branch!" : entree 1/2/3 + multi-runs + perso
  /herotrial   persos a farmer + perso qui combat
  /enchainer   plusieurs modes a la suite (ex. supply:money:4:10 nightops:5)
  /reglages    recharge de stamina et chien de garde : etat + boutons pour activer / desactiver
  /statut      ce qui tourne, depuis quand, dernieres lignes du journal, etat de l'emulateur
  /capture     une capture de l'ecran de l'emulateur
  /stop        arrete le script en cours
  /aide        comment ca marche, de A a Z

Un seul script a la fois. Seuls les identifiants de "allowed_user_ids" peuvent utiliser les commandes.

REPONSES PRIVEES OU PUBLIQUES : par defaut, seul celui qui tape la commande voit la reponse.
  "public_replies": true dans discord_config.json -> tout le serveur voit les reponses (utile entre amis).
  "result_channel_id": <id du salon>              -> le message de fin de script est poste dans ce salon au lieu d'un MP.
  Meme en public, les boutons de /reglages ne repondent qu'a leur proprietaire.

BOT PARTAGE : un seul bot Discord (un seul token) peut tourner sur plusieurs PC (le tien, ceux de tes amis).
Chaque PC recoit toutes les commandes du serveur, mais ne repond qu'a SON proprietaire (allowed_user_ids) et reste
silencieux pour les autres. Les reponses sont privees (visibles seulement par celui qui tape la commande).
Quand un script se termine (ou s'arrete sur une erreur), tu recois un message prive avec les dernieres lignes.
Configuration : discord_config.json (voir discord_config.example.json). Ne partage JAMAIS ce fichier : il contient le token.
"""
import asyncio
import collections
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import discord
from discord import app_commands

import enchainer as ec
import encours                 # farm en cours, partage avec l'assistant et le planning
import discord_ui              # boutons, menus, tableau de bord
import heroes as hz

BASE = Path(__file__).resolve().parent
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
try:
    CFG = json.loads((BASE / "discord_config.json").read_text(encoding="utf-8"))
    TOKEN = CFG["token"]
except (OSError, ValueError, KeyError) as e:
    print(f"discord_config.json absent ou illisible ({e}). Lance lancer_discord.bat : il te pose les questions.")
    sys.exit(2)
ALLOWED = {int(i) for i in CFG.get("allowed_user_ids", [])}
GUILD_ID = int(CFG["guild_id"]) if CFG.get("guild_id") else None
# true chez le createur du bot (son PC enregistre les commandes pour tout le monde), false chez les amis.
# Si la cle est absente : false, car un seul PC doit enregistrer les commandes (le message de demarrage le rappelle).
SYNC = bool(CFG.get("sync_commands", False))
PUBLIC = bool(CFG.get("public_replies", False))     # true : les reponses sont visibles par TOUT LE SERVEUR
CHANNEL_ID = int(CFG["result_channel_id"]) if CFG.get("result_channel_id") else None   # salon ou annoncer la fin (sinon : message prive)
EPH = not PUBLIC
if not ALLOWED:
    print("discord_config.json : renseigne allowed_user_ids (ton identifiant Discord), sinon personne ne peut utiliser le bot.")
    sys.exit(2)

# ---------------------------------------------------------------- noms affiches
SUPPLY_NAMES = {"food": "Cafeteria", "money": "Bounty", "gear": "Data Collection", "card": "Reinforcement", "crystals": "Recovery"}
SUPPLY_WHAT = {"food": "nourriture", "money": "argent", "gear": "matériel", "card": "cartes de renfort", "crystals": "cristaux de soin"}
MODE_NAMES = {"hero": "Hero Trial", "nightops": "Night Ops", "supply": "Supply", "whm": "Événement", "aa": "Agency Assessment"}
MODE_EMOJI = {"hero": "🦸", "nightops": "🌙", "supply": "📦", "whm": "🏛️", "aa": "🏢", "chain": "🔗"}
AA_BOTS = {"deku": "Izuku Midoriya", "bakugo": "Katsuki Bakugo", "shoto": "Shoto Todoroki", "momo": "Momo Yaoyorozu",
           "ochaco": "Ochaco Uraraka", "eijiro": "Eijiro Kirishima", "fumikage": "Fumikage Tokoyami", "denki": "Denki Kaminari"}
AA_CHOICES = [app_commands.Choice(name=v, value=k) for k, v in AA_BOTS.items()]
COLORS = {"hero": 0xE24A4A, "nightops": 0x3B5BA5, "supply": 0xD9A441, "whm": 0x7A4FBF, "aa": 0x2E9C8F, "chain": 0x8E6FD8,
          "info": 0x4CA76B, "bad": 0xC0392B}
WHM_CHOICES = [app_commands.Choice(name=f"Niveau {i}" + (" (le plus rentable)" if i == 3 else ""), value=i) for i in (1, 2, 3)]
WHM_EVENTS = [app_commands.Choice(name="Entrance", value="entrance"), app_commands.Choice(name="Hallway", value="hallway"),
              app_commands.Choice(name="Library", value="library")]
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
REFILL_CODE = CFG.get("refill_code") or "BETASTAMINA"


def heroes_list():
    """Persos utilisables = images presentes dans templates_chars/ (ordre du jeu)."""
    return hz.available(BASE / "templates_chars")


def hero_choices():
    """Menu deroulant des persos, avec leur vrai nom (All Might, Endeavor, Deku...)."""
    out = []
    for n in heroes_list()[:25]:                              # Discord : 25 choix maxi
        label = hz.pretty(n)
        if n == hz.DEFAULT:
            label += " (par défaut)"
        out.append(app_commands.Choice(name=label, value=n))
    return out


HEROES = hero_choices()
HERO_TEXT = ", ".join(f"{hz.pretty(n)} ({n})" for n in heroes_list())
SUPPLY_CHOICES = [app_commands.Choice(name=f"{SUPPLY_NAMES[k]} — {SUPPLY_WHAT[k]} (niveaux 1-{ec.MAX_LEVEL[k]})", value=k)
                  for k in SUPPLY_NAMES]
LEVEL_CHOICES = [app_commands.Choice(name=f"Niveau {i}" + (" (le plus rentable)" if i == 4 else ""), value=i) for i in (1, 2, 3, 4)]
ONOFF = [app_commands.Choice(name="activer", value="on"), app_commands.Choice(name="désactiver", value="off")]


def default_hero_chars():
    """Persos Hero Trial choisis dans l'assistant (config.json). Vide si le fichier n'existe pas encore."""
    return cfg_get("hero_chars", [])


def cfg_get(key, default=None):
    try:
        return json.loads((BASE / "config.json").read_text(encoding="utf-8")).get(key, default)
    except Exception:
        return default


def cfg_set(key, value):
    f = BASE / "config.json"
    cfg = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    cfg[key] = value
    f.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- emulateur (ADB)
def _adb():
    """-> (chemin adb, appareil) ou (None, None) si l'emulateur n'est pas la."""
    import auto_nightops as nt
    try:
        adb = nt._resolve_adb(nt.ADB)
    except SystemExit:
        return None, None
    nt.ADB = adb
    devs = nt._devices()
    if not devs:
        for p in nt.PORTS:
            try:
                subprocess.run([adb, "connect", p], capture_output=True, timeout=6, creationflags=NO_WINDOW)
            except Exception:
                pass
        devs = nt._devices()
    if not devs:
        return adb, None
    dev = nt.DEVICE if nt.DEVICE in devs else devs[0]
    return adb, dev


def emulator_state():
    adb, dev = _adb()
    return dev


def screenshot_png():
    adb, dev = _adb()
    if not dev:
        return None
    r = subprocess.run([adb, "-s", dev, "exec-out", "screencap", "-p"], capture_output=True, timeout=30, creationflags=NO_WINDOW)
    return r.stdout if r.returncode == 0 and r.stdout.startswith(b"\x89PNG") else None


# ---------------------------------------------------------------- gestion du script en cours
class Runner:
    def __init__(self):
        self.proc, self.label, self.emoji, self.t0, self.stopped = None, "", "🎮", 0.0, False
        self.lines = collections.deque(maxlen=60)
        self.lock = threading.Lock()

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, cmd, label, on_end, emoji="🎮"):
        with self.lock:
            self.lines.clear()
            self.label, self.emoji, self.t0, self.stopped = label, emoji, time.time(), False
            self.proc = subprocess.Popen(cmd, cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                         encoding="utf-8", errors="replace", env=dict(ENV, MHA_DISCORD="1", MHA_SOURCE="discord"),
                                         creationflags=NO_WINDOW)
            threading.Thread(target=self._read, args=(self.proc, on_end), daemon=True).start()

    def _read(self, proc, on_end):
        for line in proc.stdout:
            if line.strip():
                self.lines.append(line.rstrip())
        code = proc.wait()
        on_end(code, self.stopped)

    def stop(self):
        if not self.running:
            return False
        self.stopped = True
        if os.name == "nt":                                   # tue aussi les bots lances par l'enchaineur
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
        else:
            self.proc.terminate()
        return True

    def tail(self, n=8):
        txt = "\n".join(list(self.lines)[-n:])
        return txt[-1500:] or "(rien d'affiché pour l'instant)"

    def elapsed(self):
        s = int(time.time() - self.t0)
        return f"{s // 3600}h{s % 3600 // 60:02d}" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"

    def progress(self):
        """Derniere progression lue dans le journal : '| run 3/10' -> (3, 10)."""
        for line in reversed(self.lines):
            if "| run " in line:
                try:
                    a, b = line.rsplit("| run ", 1)[1].split()[0].split("/")
                    return int(a), int(b)
                except (ValueError, IndexError):
                    pass
        return None

    def activity(self):
        """Ce que le bot est en train de faire, en clair."""
        for line in reversed(self.lines):
            if "RECHARGE DE STAMINA" in line:
                return "🔋 recharge de la stamina avec le code"
            if "MAINTENANCE" in line:
                return "🛡️ serveur en maintenance : j'attends"
            if "WATCHDOG" in line:
                return "🛡️ relance du jeu en cours"
            if "challagain" in line or "continuechall" in line:
                return "⚔️ en plein multi-run"
            if "defilement" in line or "liste" in line:
                return "🔎 je cherche le perso dans la liste"
        return "⚙️ en route"


runner = Runner()


# ---------------------------------------------------------------- bot Discord
class Tree(app_commands.CommandTree):
    async def interaction_check(self, interaction):
        # Bot partage : toutes les instances (tous les PC) recoivent toutes les commandes du serveur.
        # Si ce n'est pas MON proprietaire, je ne reponds RIEN : c'est le PC de l'autre personne qui repond.
        return interaction.user.id in ALLOWED

    async def on_error(self, interaction, error):
        if isinstance(error, app_commands.CheckFailure):
            return
        print(f"Erreur sur une commande : {error!r}", flush=True)


intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = Tree(client)
GUILD = discord.Object(id=GUILD_ID) if GUILD_ID else None
LOOP = None


def cmd(**kw):
    return tree.command(guild=GUILD, **kw) if GUILD else tree.command(**kw)


def embed(title, desc="", color=COLORS["info"], tip=None):
    e = discord.Embed(title=title, description=desc, color=color)
    if tip:
        e.set_footer(text=(f"{tip} · 🖥️ {socket.gethostname()}" if PUBLIC and tip else
                           (f"🖥️ {socket.gethostname()}" if PUBLIC else tip)))
    return e


async def send_owner(e, files=(), view=None):
    """Envoie un message dans le salon des resultats (s'il est regle), sinon en prive au proprietaire."""
    def fl():
        return [discord.File(str(f), filename=f.name) for f in files]
    if CHANNEL_ID:                                       # annonce dans un salon : tout le monde la voit
        try:
            ch = client.get_channel(CHANNEL_ID) or await client.fetch_channel(CHANNEL_ID)
            await ch.send(embed=e, files=fl(), **({"view": view} if view else {}))
            return
        except Exception as ex:
            print(f"(!) Salon {CHANNEL_ID} inaccessible ({ex}) : j'envoie un message privé à la place.")
    for uid in sorted(ALLOWED)[:1]:
        try:
            user = await client.fetch_user(uid)
            await user.send(embed=e, files=fl(), **({"view": view} if view else {}))
        except Exception as ex:
            print("Message privé impossible :", ex)


# ---------------------------------------------------------------- incidents (journal.py : logs/incidents/)
INC_DIR = BASE / "logs" / "incidents"
INC_POLL = 15                                            # s entre deux verifications du dossier
BOT_T0 = time.time()


def incidents(pending_only=True):
    """-> [(dossier, infos)] des incidents pas encore envoyes, du plus ancien au plus recent."""
    out = []
    try:
        for d in sorted(INC_DIR.glob("2*")):
            if pending_only and (d / "envoye").exists():
                continue
            try:
                out.append((d, json.loads((d / "incident.json").read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                pass
    except OSError:
        pass
    return out


def mark_sent(d):
    try:
        (d / "envoye").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    except OSError:
        pass


def incident_files(d):
    return [f for f in (d / "capture.png", d / "boite_noire.jpg") if f.exists()]


def incident_tail(d, n=10):
    try:
        txt = "\n".join((d / "fin_journal.txt").read_text(encoding="utf-8").splitlines()[-n:])
    except OSError:
        txt = ""
    return (txt[-1000:] or "(vide)").replace("`", "'")


INCIDENT_TITLES = {"planning": "🗓️ Farm programmé", "derive": "📉 Image qui se dégrade", "reprise": "🔄 Écran inconnu : reprise", "adb": "📵 ADB / émulateur",
                   "incident": "🚨 Script bloqué"}


async def send_incident(d, info):
    live = info.get("live")
    title = INCIDENT_TITLES.get(info.get("kind"), "🚨 Script bloqué") if live else "🚨 Incident"
    e = embed(f"{title} : {info.get('script') or 'script'}",
              f"**{info.get('reason', '?')[:900]}**\n🕒 {info.get('time', '')}", COLORS["bad"],
              tip="Pièces jointes : dernière capture + boîte noire (30 dernières captures) · /capture pour l'écran actuel")
    e.add_field(name="10 dernières lignes du journal", value=f"```\n{incident_tail(d)}\n```", inline=False)
    files = incident_files(d)
    if any(f.name == "capture.png" for f in files):
        e.set_image(url="attachment://capture.png")
    await send_owner(e, files, discord_ui.Actions(["capture", "statut", "stop"] if live else ["capture", "relancer"]))
    mark_sent(d)


async def incident_check():
    """Un passage : envoie les incidents des scripts lances hors Discord (assistant...) et les blocages en direct.
    Ceux des scripts lances par Discord partent avec le message de fin (notify)."""
    for d, info in incidents():
        mtime = d.stat().st_mtime
        if mtime < BOT_T0 - 3600:                       # vieux incidents d'avant le demarrage du bot : on ne spamme pas
            mark_sent(d)
            continue
        if info.get("discord_session") and not info.get("live") and time.time() - mtime < 120:
            continue                                    # notify() s'en charge a la fin du script
        await send_incident(d, info)


ALIVE = BASE / "logs" / "discord_bot.vivant"            # l'assistant y voit que le bot est en ligne (verif.py)


def alive():
    try:
        ALIVE.parent.mkdir(parents=True, exist_ok=True)
        ALIVE.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    except OSError:
        pass


async def incident_watch():
    while True:
        alive()
        await asyncio.sleep(INC_POLL)
        try:
            await incident_check()
        except Exception as ex:                         # noqa: BLE001 - la surveillance ne doit jamais s'arreter
            print(f"(!) envoi d'incident impossible : {ex!r}", flush=True)


async def notify(code, stopped):
    """Message prive a la fin d'un script (pas quand tu l'as arrete toi-meme avec /stop)."""
    if stopped:
        return
    text = runner.tail(14)
    ok = any("Fini !" in l for l in list(runner.lines)[-14:]) and code == 0
    if runner.label.startswith("Enchainement"):
        ok = ok or (any(l.strip().startswith("OK") for l in runner.lines) and not any("ECHEC" in l for l in runner.lines))
    e = embed(f"{'✅ Terminé' if ok else '⚠️ Arrêté avant la fin'} : {runner.label}",
              f"Durée : **{runner.elapsed()}**", COLORS["info"] if ok else COLORS["bad"],
              tip="/statut pour les détails · /capture pour voir l'écran")
    e.add_field(name="Dernières lignes", value=f"```\n{text}\n```", inline=False)
    files = []
    mine = [(d, i) for d, i in incidents() if i.get("discord_session") and not i.get("live")
            and d.stat().st_mtime >= runner.t0 - 5]
    if mine:                                             # incident(s) de ce lancement : raison + capture + boite noire
        d, info = mine[-1]
        e.add_field(name="🚨 Incident", value=info.get("reason", "?")[:1000], inline=False)
        files = incident_files(d)
        if any(f.name == "capture.png" for f in files):
            e.set_image(url="attachment://capture.png")
    await send_owner(e, files, discord_ui.Actions(["relancer", "capture", "stats"]))
    for d, _i in mine:
        mark_sent(d)


def on_end(code, stopped):
    if LOOP:
        asyncio.run_coroutine_threadsafe(notify(code, stopped), LOOP)


def options_line(refill, watchdog):
    """Ce qui sera applique a ce lancement (None = reglage enregistre)."""
    r = refill if refill is not None else bool(cfg_get("stamina_refill", False))
    w = watchdog if watchdog is not None else bool(cfg_get("watchdog", False))
    return f"🔋 Recharge stamina : **{'oui' if r else 'non'}**\n🛡️ Chien de garde : **{'oui' if w else 'non'}**"


async def start_script(interaction, command, label, replace, kind="supply", details="", refill=None, watchdog=None):
    if runner.running:
        if not replace:
            e = embed("⏳ Un script tourne déjà", f"{runner.emoji} **{runner.label}**\nDepuis {runner.elapsed()}.",
                      COLORS["bad"], tip="Relance avec remplacer: Oui pour prendre sa place, ou /stop d'abord.")
            await interaction.followup.send(embed=e)
            return
        runner.stop()
        await asyncio.sleep(2)
    elif (ext := await asyncio.to_thread(encours.read)):
        if not replace:
            e = embed("⏳ Un farm tourne déjà", encours.describe(ext), COLORS["bad"],
                      tip="Relance avec remplacer: Oui pour l'arrêter et lancer celui-ci, ou /stop d'abord.")
            await interaction.followup.send(embed=e)
            return
        await asyncio.to_thread(encours.stop)
        await asyncio.sleep(2)
    dev = await asyncio.to_thread(emulator_state)
    if not dev:
        e = embed("📵 L'émulateur ne répond pas",
                  "Ouvre MuMu avec le jeu lancé sur le PC, puis réessaie.", COLORS["bad"],
                  tip="Si MuMu est bien ouvert : vérifie l'ADB dans l'assistant (bouton Tester).")
        await interaction.followup.send(embed=e)
        return
    runner.start(command, label, on_end, MODE_EMOJI.get(kind, "🎮"))
    discord_ui.remember(command, label, kind, details)                # pour 🔁 Relancer pareil
    e = embed(f"{MODE_EMOJI.get(kind, '🎮')} C'est parti : {label}", details, COLORS.get(kind, COLORS["info"]),
              tip="Je t'écris en privé quand c'est fini · /statut · /capture · /stop")
    e.add_field(name="Options", value=options_line(refill, watchdog), inline=False)
    e.add_field(name="Et maintenant ?", value="Le script revient d'abord au **menu principal** tout seul, "
                "puis il place le perso et lance le farm. Tu peux ranger ton téléphone 🙂", inline=False)
    await interaction.followup.send(embed=e, view=discord_ui.Actions(["statut", "capture", "stop"]))


@client.event
async def on_ready():
    global LOOP
    first = LOOP is None
    LOOP = asyncio.get_running_loop()
    if first:
        LOOP.create_task(incident_watch())                # incidents (logs/incidents) -> Discord
        discord_ui.register(client)                       # boutons des anciens messages : toujours actifs
        LOOP.create_task(discord_ui.panel_loop())         # tableau de bord (/panneau) mis a jour tout seul
    if SYNC:
        try:
            await (tree.sync(guild=GUILD) if GUILD else tree.sync())
        except Exception as e:
            print(f"(!) Enregistrement des commandes impossible : {e}\n    Le bot est-il bien invité sur le serveur (scope applications.commands) ?", flush=True)
    print(f"Connecté : {client.user} | utilisateurs autorisés : {sorted(ALLOWED)} | "
          f"commandes {'enregistrées' if SYNC else 'non enregistrées (sync_commands = false)'}", flush=True)


# ---------------------------------------------------------------- preparation d'un lancement
async def run_mode(interaction, st, kind, details, remplacer, refill, watchdog):
    try:
        st = ec.validate(st)
    except SystemExit as e:
        await interaction.followup.send(embed=embed("❌ Impossible de lancer", str(e), COLORS["bad"]))
        return
    if st.get("hero") and st["hero"] not in heroes_list():
        await interaction.followup.send(embed=embed(
            "❌ Perso inconnu", f"**{st['hero']}** n'a pas d'image dans `templates_chars/`.\n\nPersos utilisables : {HERO_TEXT}", COLORS["bad"]))
        return
    ec.REFILL, ec.WATCHDOG = refill, watchdog                 # None = on garde le reglage enregistre (/reglages)
    command = ec.command(st)
    ec.REFILL = ec.WATCHDOG = None
    await start_script(interaction, command, ec.label(st), remplacer, kind, details, refill, watchdog)


# ---------------------------------------------------------------- /supply
@cmd(name="supply", description="📦 Supply : quoi farmer, quel niveau, combien de multi-runs")
@app_commands.describe(
    farmer="Ce que tu veux farmer : la liste t'indique les niveaux possibles pour chacun",
    niveau="Niveau de la mission : 1 à 4, sauf Data Collection qui s'arrête au niveau 2",
    multiruns="Nombre de multi-runs à enchaîner. 1 multi-run = la série de combats + Challenge Again",
    perso="Qui combat : choisis dans la liste. Le bot le place tout seul dans l'équipe",
    stamina="Recharger la stamina avec le code quand il n'y en a plus. Vide = réglage de /reglages",
    gardien="Relancer le jeu si maintenance ou blocage. Vide = réglage de /reglages",
    remplacer="Oui = arrête le script déjà en cours pour lancer celui-ci")
@app_commands.choices(farmer=SUPPLY_CHOICES, niveau=LEVEL_CHOICES, perso=HEROES)
async def supply_cmd(interaction: discord.Interaction, farmer: app_commands.Choice[str],
                     niveau: app_commands.Choice[int] = None, multiruns: app_commands.Range[int, 1, 999] = 1,
                     perso: app_commands.Choice[str] = None, stamina: bool = None, gardien: bool = None,
                     remplacer: bool = False):
    await interaction.response.defer(ephemeral=EPH)
    lv = niveau.value if niveau else 1
    mx = ec.MAX_LEVEL[farmer.value]
    if lv > mx:
        await interaction.followup.send(embed=embed(
            "❌ Ce niveau n'existe pas", f"**{SUPPLY_NAMES[farmer.value]}** s'arrête au **niveau {mx}** (tu as demandé {lv}).",
            COLORS["bad"], tip="Relance la commande en choisissant un niveau plus bas."))
        return
    st = {"mode": "supply", "supply": farmer.value, "level": lv, "runs": multiruns,
          "hero": perso.value if perso else None}
    details = (f"**{SUPPLY_NAMES[farmer.value]}** ({SUPPLY_WHAT[farmer.value]}) · niveau **{lv}** · "
               f"**{multiruns}** multi-run{'s' if multiruns > 1 else ''}\n"
               f"🦸 Perso : **{hz.pretty(perso.value) if perso else 'celui de l’assistant'}**")
    await run_mode(interaction, st, "supply", details, remplacer, stamina, gardien)


# ---------------------------------------------------------------- /nightops
@cmd(name="nightops", description="🌙 Night Ops : combien de multi-runs, avec quel perso")
@app_commands.describe(
    multiruns="Nombre de multi-runs à enchaîner. 1 multi-run = la série de combats + Challenge Again",
    perso="Qui combat : choisis dans la liste. Le bot le place tout seul dans l'équipe",
    stamina="Recharger la stamina avec le code quand il n'y en a plus. Vide = réglage de /reglages",
    gardien="Relancer le jeu si maintenance ou blocage. Vide = réglage de /reglages",
    remplacer="Oui = arrête le script déjà en cours pour lancer celui-ci")
@app_commands.choices(perso=HEROES)
async def nightops_cmd(interaction: discord.Interaction, multiruns: app_commands.Range[int, 1, 999] = 1,
                       perso: app_commands.Choice[str] = None, stamina: bool = None, gardien: bool = None,
                       remplacer: bool = False):
    await interaction.response.defer(ephemeral=EPH)
    st = {"mode": "nightops", "runs": multiruns, "hero": perso.value if perso else None}
    details = (f"**{multiruns}** multi-run{'s' if multiruns > 1 else ''}\n"
               f"🦸 Perso : **{hz.pretty(perso.value) if perso else 'celui de l’assistant'}**")
    await run_mode(interaction, st, "nightops", details, remplacer, stamina, gardien)


# ---------------------------------------------------------------- /herotrial
@cmd(name="herotrial", description="🦸 Hero Trial : les persos à farmer, un par un dans l'ordre")
@app_commands.describe(
    persos="Persos à FARMER, séparés par des virgules. Commence à taper : je te propose la suite",
    perso="Qui COMBAT (pas forcément celui que tu farmes) : choisis dans la liste",
    stamina="Recharger la stamina avec le code quand il n'y en a plus. Vide = réglage de /reglages",
    gardien="Relancer le jeu si maintenance ou blocage. Vide = réglage de /reglages",
    remplacer="Oui = arrête le script déjà en cours pour lancer celui-ci")
@app_commands.choices(perso=HEROES)
async def herotrial_cmd(interaction: discord.Interaction, persos: str = None, perso: app_commands.Choice[str] = None,
                        stamina: bool = None, gardien: bool = None, remplacer: bool = False):
    await interaction.response.defer(ephemeral=EPH)
    chars = (persos or ",".join(default_hero_chars())).replace(" ", "")
    if not chars:
        await interaction.followup.send(embed=embed(
            "❌ Aucun perso à farmer", f"Indique-les dans `persos` (ex. `deku,denki`), ou choisis-les une fois dans l'assistant.\n\n"
            f"Persos utilisables : {HERO_TEXT}", COLORS["bad"]))
        return
    inconnus = [c for c in chars.split(",") if c and c not in heroes_list()]
    if inconnus:
        await interaction.followup.send(embed=embed(
            "❌ Perso inconnu", f"Je ne connais pas : **{', '.join(inconnus)}**.\n\nPersos utilisables : {HERO_TEXT}", COLORS["bad"]))
        return
    st = {"mode": "hero", "chars": chars, "hero": perso.value if perso else None}
    details = (f"À farmer : **{', '.join(hz.pretty(c) for c in chars.split(',') if c)}**\n"
               f"🦸 Perso qui combat : **{hz.pretty(perso.value) if perso else 'celui de l’assistant'}**")
    await run_mode(interaction, st, "hero", details, remplacer, stamina, gardien)


@herotrial_cmd.autocomplete("persos")
async def persos_auto(interaction: discord.Interaction, current: str):
    """Complete le dernier nom tape : 'deku,de' -> 'deku,denki'."""
    head, _, last = current.rpartition(",")
    prefix = f"{head}," if head else ""
    deja = {c.strip() for c in head.split(",") if c.strip()}
    out = []
    if not current.strip() and default_hero_chars():
        out.append(app_commands.Choice(name=f"⭐ ceux de l'assistant : {','.join(default_hero_chars())}",
                                       value=",".join(default_hero_chars())))
    for n in heroes_list():
        if n in deja or not n.startswith(last.strip().lower()):
            continue
        val = f"{prefix}{n}"
        if len(val) <= 100:
            out.append(app_commands.Choice(name=f"{val}   ({hz.pretty(n)})", value=val))
    return out[:25]


# ---------------------------------------------------------------- /whm (evenement)
@cmd(name="whm", description="🏛️ Événements WHM (Entrance, Hallway, Library) : lequel, quel niveau, combien de multi-runs")
@app_commands.describe(
    evenement="Quel événement : Entrance, Hallway ou Library (vide = celui réglé dans l'assistant)",
    entree="Niveau 1, 2 ou 3 : plus c'est haut, plus ça rapporte (et plus ça coûte)",
    multiruns="Nombre de multi-runs. 1 multi-run = case cochée + Start = le maximum de runs d'un coup",
    perso="Qui combat : choisis dans la liste. Le bot le place tout seul dans l'équipe",
    stamina="Recharger la stamina avec le code quand il n'y en a plus. Vide = réglage de /reglages",
    gardien="Relancer le jeu si maintenance ou blocage. Vide = réglage de /reglages",
    remplacer="Oui = arrête le script déjà en cours pour lancer celui-ci")
@app_commands.choices(evenement=WHM_EVENTS, entree=WHM_CHOICES, perso=HEROES)
async def whm_cmd(interaction: discord.Interaction, evenement: app_commands.Choice[str] = None,
                  entree: app_commands.Choice[int] = None,
                  multiruns: app_commands.Range[int, 1, 999] = 1, perso: app_commands.Choice[str] = None,
                  stamina: bool = None, gardien: bool = None, remplacer: bool = False):
    await interaction.response.defer(ephemeral=EPH)
    lv = entree.value if entree else int(cfg_get("whm_level", 3) or 3)
    ev = evenement.value if evenement else (cfg_get("whm_event", "entrance") or "entrance")
    st = {"mode": "whm", "event": ev, "level": lv, "runs": multiruns, "hero": perso.value if perso else None}
    details = (f"**{str(ev).capitalize()} {lv}** · **{multiruns}** multi-run{'s' if multiruns > 1 else ''}\n"
               f"🦸 Perso : **{hz.pretty(perso.value) if perso else 'celui de l’assistant'}**\n"
               "Le bot coche « Consecutive Challenges » (maximum de runs), active AUTO et enchaîne avec « Continue Challenge ».")
    await run_mode(interaction, st, "whm", details, remplacer, stamina, gardien)


# ---------------------------------------------------------------- /aa (Agency Assessment)
@cmd(name="aa", description="🏢 Agency Assessment : combien de combats, quels bots, à quel floor remettre le permit à zéro")
@app_commands.describe(
    combats="Nombre de combats à faire (1 combat = 1 floor). Vide = réglage de l'assistant",
    bot1="1er bot qui t'aide au combat (vide = ceux réglés dans l'assistant)",
    bot2="2e bot, différent du premier",
    reset_floor="Reset du permit dès qu'il est à ce floor OU plus haut. 0 = jamais. Vide = réglage de l'assistant",
    floor_actuel="Floor actuel du permit, seulement si le bot n'arrive pas à le lire tout seul",
    reserves="Persos gardés pour les hauts floors, ex. am,endy,hawks (aucun = pas de réserve)",
    floor_reserve="Les persos réservés ne sont joués qu'à partir de ce floor (10 par défaut)",
    gardien="Relancer le jeu si maintenance ou blocage. Vide = réglage de /reglages",
    remplacer="Oui = arrête le script déjà en cours pour lancer celui-ci")
@app_commands.choices(bot1=AA_CHOICES, bot2=AA_CHOICES)
async def aa_cmd(interaction: discord.Interaction, combats: app_commands.Range[int, 1, 999] = None,
                 bot1: app_commands.Choice[str] = None, bot2: app_commands.Choice[str] = None,
                 reset_floor: app_commands.Range[int, 0, 99] = None, floor_actuel: app_commands.Range[int, 1, 99] = None,
                 reserves: str = None, floor_reserve: app_commands.Range[int, 1, 99] = None,
                 gardien: bool = None, remplacer: bool = False):
    await interaction.response.defer(ephemeral=EPH)
    bots = [b.value for b in (bot1, bot2) if b]
    if len(bots) == 2 and bots[0] == bots[1]:
        await interaction.followup.send(embed=embed("❌ Deux fois le même bot", "Choisis deux bots différents.", COLORS["bad"]))
        return
    if not bots:
        bots = [b for b in (cfg_get("aa_bots") or ["deku", "bakugo"]) if b in AA_BOTS][:2]
    n = combats or int(cfg_get("aa_runs", 10) or 10)
    rf = reset_floor if reset_floor is not None else int(cfg_get("aa_reset_floor", 0) or 0)
    command = [sys.executable, "-u", str(BASE / "auto_aa.py"), "--runs", str(n), "--bots", ",".join(bots), "--reset-floor", str(rf)]
    if floor_actuel:
        command += ["--floor", str(floor_actuel)]
    res = [n.strip().lower() for n in reserves.split(",") if n.strip()] if reserves is not None else list(cfg_get("aa_reserved") or [])
    res = [n for n in res if n != "aucun"]
    absent = [n for n in res if not (BASE / "templates_aa" / "chars" / f"{n}.png").exists()]
    if absent:
        await interaction.followup.send(embed=embed("❌ Perso réservé inconnu", "Pas d'icône `templates_aa/chars/<nom>.png` pour : **"
                                                    + ", ".join(absent) + "**.\nNoms possibles : "
                                                    + ", ".join(sorted(f.stem for f in (BASE / "templates_aa" / "chars").glob("*.png")))
                                                    + ".", COLORS["bad"]))
        return
    rfl = floor_reserve or int(cfg_get("aa_reserve_floor", 10) or 10)
    command += ["--reserve", ",".join(res) or "aucun", "--reserve-floor", str(rfl)]
    if gardien is not None:
        command.append("--watchdog" if gardien else "--no-watchdog")
    details = (f"**{n}** combat{'s' if n > 1 else ''}\n🤖 Bots : **{' + '.join(AA_BOTS[b] for b in bots) or 'aucun'}**\n"
               f"♻️ Reset du permit : **{'à partir du floor ' + str(rf) if rf else 'jamais'}**"
               + (f"\n🔒 Réservés pour les floors {rfl}+ : **{', '.join(n.capitalize() for n in res)}**" if res else ""))
    await start_script(interaction, command, f"Agency Assessment x{n}", remplacer, "aa", details, False, gardien)


# ---------------------------------------------------------------- /enchainer
@cmd(name="enchainer", description="🔗 Plusieurs modes à la suite, sans rien toucher entre deux")
@app_commands.describe(
    plan="Étapes séparées par un espace, ex. supply:money:4:10 nightops:5 — je te propose la suite",
    stamina="Recharger la stamina avec le code quand il n'y en a plus. Vide = réglage de /reglages",
    gardien="Relancer le jeu si maintenance ou blocage. Vide = réglage de /reglages",
    remplacer="Oui = arrête le script déjà en cours pour lancer celui-ci")
async def enchainer_cmd(interaction: discord.Interaction, plan: str, stamina: bool = None, gardien: bool = None,
                        remplacer: bool = False):
    await interaction.response.defer(ephemeral=EPH)
    tokens = plan.replace(";", " ").split()
    try:
        steps = [ec.validate(ec.parse_token(t)) for t in tokens]
    except SystemExit as e:
        await interaction.followup.send(embed=embed(
            "❌ Plan illisible", f"{e}\n\n**Exemples qui marchent :**\n"
            "`supply:money:4:10` → 10 multi-runs Bounty niveau 4\n"
            "`supply:money:4:10 nightops:5` → puis 5 multi-runs Night Ops\n"
            "`nightops:5:deku` → 5 multi-runs avec Deku qui combat\n"
            "`hero:deku,denki` → Hero Trial sur Deku puis Denki\n"
            "`whm:3:2` → 2 multi-runs de l'événement Entrance, niveau 3\n"
            "`hallway:3:2` / `library:3:2` → pareil pour Hallway / Library", COLORS["bad"]))
        return
    if not steps:
        await interaction.followup.send(embed=embed("❌ Plan vide", "Écris au moins une étape, ex. `supply:money:4:10`.", COLORS["bad"]))
        return
    flags = []
    if stamina is not None:
        flags.append("--refill" if stamina else "--no-refill")
    if gardien is not None:
        flags.append("--watchdog" if gardien else "--no-watchdog")
    details = "\n".join(f"**{i}.** {ec.label(s)}" for i, s in enumerate(steps, 1))
    await start_script(interaction, [sys.executable, "-u", str(BASE / "enchainer.py"), *tokens, *flags],
                       f"Enchainement ({len(steps)} étapes)", remplacer, "chain", details, stamina, gardien)


@enchainer_cmd.autocomplete("plan")
async def plan_auto(interaction: discord.Interaction, current: str):
    """Complete l'etape en cours de frappe, ou propose l'etape suivante quand on vient de taper un espace."""
    parts = current.split(" ")
    last = "" if current.endswith(" ") else parts[-1]          # etape en cours de frappe
    done = parts[:-1] if last else [p for p in parts if p]
    base = (" ".join(done) + " ") if done else ""
    props = ["supply:money:4:10", "supply:food:4:10", "supply:gear:2:5", "supply:card:4:10", "supply:crystals:4:10",
             "nightops:5", "whm:3:2", "hallway:3:2", "library:3:2", "aa:10", "hero:deku,denki"]
    out = []
    for prop in props:
        if last and not prop.startswith(last.lower()):
            continue
        if done and prop == done[-1]:
            continue                                            # inutile de reproposer l'etape qu'on vient d'ecrire
        val = base + prop
        if len(val) <= 100:
            out.append(app_commands.Choice(name=val, value=val))
    return out[:25]


# ---------------------------------------------------------------- /reglages (avec boutons)
def settings_embed():
    r, w = bool(cfg_get("stamina_refill", False)), bool(cfg_get("watchdog", False))
    e = embed("🎛️ Réglages du bot", "Ils sont enregistrés et servent à **tous** les lancements.\n"
              "Pour un seul lancement, utilise plutôt les options `stamina:` et `gardien:` de la commande.",
              COLORS["info"], tip="Un script déjà en route garde ses réglages : ils s'appliquent au prochain lancement.")
    e.add_field(name=f"🔋 Recharge de stamina — {'ACTIVÉE' if r else 'désactivée'}",
                value=f"Quand il n'y a plus de stamina, le bot va chercher le cadeau, entre le code **{REFILL_CODE}** "
                      "15 fois (+1000 chacune, 15 000 max) et reprend le farm.", inline=False)
    e.add_field(name=f"🛡️ Chien de garde — {'ACTIVÉ' if w else 'désactivé'}",
                value="Serveur en maintenance (pastille orange) → il attend qu'elle redevienne verte. "
                      "Rien ne bouge pendant 5 min → il ferme le jeu, le relance et repart.", inline=False)
    return e


class Settings(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.refresh_labels()

    def refresh_labels(self):
        r, w = bool(cfg_get("stamina_refill", False)), bool(cfg_get("watchdog", False))
        self.stam.label = ("Désactiver" if r else "Activer") + " la recharge de stamina"
        self.stam.style = discord.ButtonStyle.danger if r else discord.ButtonStyle.success
        self.stam.emoji = "🔋"
        self.gard.label = ("Désactiver" if w else "Activer") + " le chien de garde"
        self.gard.style = discord.ButtonStyle.danger if w else discord.ButtonStyle.success
        self.gard.emoji = "🛡️"

    async def toggle(self, interaction, key):
        if interaction.user.id not in ALLOWED:            # message public : seul le proprietaire peut changer SES reglages
            await interaction.response.send_message("⛔ Ces réglages sont ceux d'un autre PC : tu ne peux pas les changer.",
                                                    ephemeral=True)
            return
        try:
            cfg_set(key, not bool(cfg_get(key, False)))
        except Exception as e:
            await interaction.response.send_message(f"❌ Impossible d'écrire config.json : {e}", ephemeral=EPH)
            return
        self.refresh_labels()
        await interaction.response.edit_message(embed=settings_embed(), view=self)

    @discord.ui.button(label="…", style=discord.ButtonStyle.secondary)
    async def stam(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle(interaction, "stamina_refill")

    @discord.ui.button(label="…", style=discord.ButtonStyle.secondary)
    async def gard(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle(interaction, "watchdog")


@cmd(name="reglages", description="🎛️ Recharge de stamina et chien de garde : voir et changer d'un clic")
async def reglages_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(embed=settings_embed(), view=Settings(), ephemeral=EPH)


# ---------------------------------------------------------------- /stop /statut /capture
@cmd(name="farm", description="🎮 Lancer un farm avec des menus : mode, ressource, niveau, perso, nombre de runs")
async def farm_cmd(interaction: discord.Interaction):
    await discord_ui.open_farm(interaction)


@cmd(name="panneau", description="🎮 Tableau de bord en direct : farm en cours, progression, boutons Stop / Capture")
async def panneau_cmd(interaction: discord.Interaction):
    await discord_ui.open_panel(interaction)


@cmd(name="stop", description="⏹ Arrête le script en cours")
async def stop_cmd(interaction: discord.Interaction):
    await discord_ui.do_stop(interaction)



@cmd(name="statut", description="📊 Ce qui tourne, depuis quand, et les derniers messages")
async def statut_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=EPH)
    dev = await asyncio.to_thread(emulator_state)
    emu = f"{'🟢' if dev else '🔴'} Émulateur : **{dev or 'éteint / introuvable'}**\n🖥️ PC : **{socket.gethostname()}**"
    if runner.running:
        p = runner.progress()
        bar = ""
        if p:
            done, total = p
            n = max(0, min(10, round(10 * done / max(1, total))))
            bar = f"\n`{'█' * n}{'░' * (10 - n)}`  **{done}/{total}** multi-runs"
        e = embed(f"{runner.emoji} En cours : {runner.label}",
                  f"{runner.activity()} · depuis **{runner.elapsed()}**{bar}", COLORS["info"],
                  tip="/capture pour voir l'écran · /stop pour arrêter")
    elif (ext := await asyncio.to_thread(encours.read)):
        bar = ""
        if ext.get("total"):
            n = max(0, min(10, round(10 * ext["done"] / max(1, ext["total"]))))
            bar = f"\n`{'█' * n}{'░' * (10 - n)}`  **{ext['done']}/{ext['total']}**"
        step = f"\n🔗 Étape {ext['step']}" if ext.get("step") else ""
        e = embed(f"⏳ En cours : {ext.get('label', '?')}", encours.describe(ext) + step + bar, COLORS["info"],
                  tip="/capture pour voir l'écran · /stop pour arrêter")
        lines = "\n".join(encours.tail(ext))[-1000:].replace("`", "'")
        if lines:
            e.add_field(name="Journal", value=f"```\n{lines}\n```", inline=False)
    elif runner.lines:
        e = embed("⏸️ Rien ne tourne", f"Dernier script : {runner.emoji} **{runner.label}**", COLORS["info"],
                  tip="/supply · /nightops · /herotrial · /enchainer")
    else:
        e = embed("⏸️ Rien ne tourne", "Aucun script lancé depuis le démarrage du bot.", COLORS["info"],
                  tip="/aide si tu ne sais pas par où commencer")
    e.add_field(name="Machine", value=emu, inline=False)
    if runner.lines and runner.running:
        e.add_field(name="Journal", value=f"```\n{runner.tail()}\n```", inline=False)
    await interaction.followup.send(embed=e)


@cmd(name="capture", description="📸 Une photo de l'écran de l'émulateur, tout de suite")
async def capture_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=EPH)
    png = await asyncio.to_thread(screenshot_png)
    if not png:
        await interaction.followup.send(embed=embed("📵 Capture impossible", "L'émulateur est éteint, ou ADB ne le trouve pas.",
                                                    COLORS["bad"], tip="Ouvre MuMu avec le jeu, puis réessaie."))
        return
    await interaction.followup.send(file=discord.File(io.BytesIO(png), filename="capture.png"))


# ---------------------------------------------------------------- /planifier /planning
PLAN_JOURS = [app_commands.Choice(name="Tous les jours", value="tous"),
              app_commands.Choice(name="Du lundi au vendredi", value="semaine"),
              app_commands.Choice(name="Le week-end", value="weekend"),
              app_commands.Choice(name="Une seule fois (prochaine fois que cette heure arrive)", value="une_fois")]


PLAN_MODES = [app_commands.Choice(name=n, value=k) for k, (n, _st) in ec.MODES_UI.items()] + [
    app_commands.Choice(name="🔗 L'enchaînement préparé dans l'assistant", value="enchainement")]


def plan_step(mode, ressource, niveau, runs, perso, persos, bot1, bot2, reset, reserves=None, floor_reserve=None):
    """Les menus de /planifier -> une etape validee (SystemExit / ValueError si impossible)."""
    st = dict(ec.MODES_UI[mode][1])
    if runs:
        st["runs"] = runs
    if mode == "supply":
        st["supply"] = ressource or st["supply"]
        st["level"] = niveau or min(4, ec.MAX_LEVEL[st["supply"]])
    elif st["mode"] == "whm" and niveau:
        st["level"] = niveau
    if mode == "hero":
        chars = [c.strip().lower() for c in (persos or ",".join(cfg_get("hero_chars") or [])).split(",") if c.strip()]
        if not chars:
            raise ValueError("Hero Trial : indique les persos à farmer dans « persos » (ex. deku,denki).")
        st["chars"] = ",".join(chars)
    if mode == "aa":
        bots = [b for b in (bot1, bot2) if b]
        if len(set(bots)) < len(bots):
            raise ValueError("Agency : choisis deux bots différents.")
        st["bots"] = ",".join(bots) or None
        st["reset"] = reset or None
        if reserves is not None:                               # sinon : reglage de l'assistant au moment du farm
            st["reserve"] = reserves.replace(" ", "") or "aucun"
        st["resfloor"] = floor_reserve or None
    elif perso:
        st["hero"] = perso
    return ec.validate(st)


@cmd(name="planifier", description="🗓️ Programme un farm à une heure fixe (le PC doit rester allumé)")
@app_commands.describe(
    heure="Heure, ex. 02:00 ou 7h30", mode="Quoi farmer (ou l'enchaînement préparé dans l'assistant)",
    jours="Quand le répéter (défaut : tous les jours)", ressource="Supply : quoi farmer (défaut : Bounty)",
    niveau="Supply 1-4 / Événement 1-3 (défaut : le plus haut)", runs="Nombre de multi-runs (ou de combats pour Agency)",
    perso="Qui combat (sinon : celui déjà placé)", persos="Hero Trial : persos à farmer, ex. deku,denki (défaut : assistant)",
    bot1="Agency : 1er bot", bot2="Agency : 2e bot", reset="Agency : remettre le permit au floor 1 à partir de ce floor",
    reserves="Agency : persos réservés aux hauts floors, ex. mirio,hawks (aucun = personne ; vide = assistant)",
    floor_reserve="Agency : floor à partir duquel jouer les réservés (défaut : 10)",
    sequence="Avancé : étapes écrites comme /enchainer (remplace les menus)",
    stamina="Recharger la stamina avec le code quand il n'y en a plus. Vide = réglage général au moment du farm",
    gardien="Relancer le jeu si maintenance ou blocage. Vide = réglage général au moment du farm")
@app_commands.choices(mode=PLAN_MODES, jours=PLAN_JOURS, ressource=SUPPLY_CHOICES, perso=HEROES, bot1=AA_CHOICES,
                      bot2=AA_CHOICES)
async def planifier_cmd(interaction: discord.Interaction, heure: str, mode: str = "supply", jours: str = "tous",
                        ressource: str = None, niveau: app_commands.Range[int, 1, 4] = None,
                        runs: app_commands.Range[int, 1, 500] = None, perso: str = None, persos: str = None,
                        bot1: str = None, bot2: str = None, reset: app_commands.Range[int, 2, 30] = None,
                        reserves: str = None, floor_reserve: app_commands.Range[int, 1, 99] = None,
                        sequence: str = None, stamina: bool = None, gardien: bool = None):
    await interaction.response.defer(ephemeral=EPH)
    import planificateur as pl
    try:
        if sequence:
            seq = sequence
        elif mode == "enchainement":
            steps = [ec.validate(dict(s)) for s in (cfg_get("chain_steps") or [])]
            if not steps:
                raise ValueError("L'enchaînement de l'assistant est vide : prépare-le (mode Enchaîner) ou choisis un mode.")
            seq = " ".join(ec.token(s) for s in steps)
        else:
            seq = ec.token(plan_step(mode, ressource, niveau, runs, perso, persos, bot1, bot2, reset, reserves, floor_reserve))
        it, _m = await asyncio.to_thread(pl.add, heure, jours, seq, refill=stamina, watchdog=gardien)
    except (ValueError, SystemExit) as e:
        await interaction.followup.send(embed=embed("❌ Créneau refusé", str(e)[:1500], COLORS["bad"],
                                                    tip="Choisis le mode dans le menu ; les autres options sont facultatives"))
        return
    nxt = (await asyncio.to_thread(pl.next_runs)).get(it["id"], "?")
    await interaction.followup.send(embed=embed(
        f"🗓️ Farm programmé #{it['id']}", f"**{it['heure']}** · {pl.JOURS[it['jours']].lower()}\n"
        f"{ec.describe_tokens(it['sequence'])}\nProchaine exécution : **{nxt}**"
        + "".join(f"\n{icon} {name} : **{'oui' if it[k] else 'non'}**" for k, icon, name in
                  (("refill", "🔋", "Recharge de stamina"), ("watchdog", "🛡️", "Chien de garde")) if it.get(k) is not None),
        COLORS["info"],
        tip="Le PC peut être en veille (il se réveille), pas éteint · /planning pour voir ou supprimer"))


@cmd(name="planning", description="🗓️ Les farms programmés (et supprimer un créneau)")
@app_commands.describe(supprimer="Numéro du créneau à supprimer (voir la liste)")
async def planning_cmd(interaction: discord.Interaction, supprimer: int = 0):
    await interaction.response.defer(ephemeral=EPH)
    import planificateur as pl
    if supprimer:
        ok = await asyncio.to_thread(pl.remove, supprimer)
        await interaction.followup.send(embed=embed(f"🗑️ Créneau #{supprimer} supprimé" if ok else f"Créneau #{supprimer} introuvable",
                                                    "", COLORS["info"] if ok else COLORS["bad"]))
        return
    items = pl.load()
    nxt = await asyncio.to_thread(pl.next_runs)
    lines = [pl.describe(it) + f"  ·  prochaine : {nxt.get(it['id'], '⚠️ tâche Windows absente')}" for it in items]
    await interaction.followup.send(embed=embed("🗓️ Farms programmés", ("\n".join(lines) or "Aucun pour l'instant.")[:3900],
                                                COLORS["info"], tip="/planifier pour en ajouter · menu ci-dessous pour supprimer"),
                                    **({"view": discord_ui.PlanningView(items)} if items else {}))


# ---------------------------------------------------------------- /stats
@cmd(name="stats", description="📊 Statistiques des farms : runs, temps, réussite, dernières sessions")
@app_commands.describe(jours="Sur combien de jours (1 à 90, défaut 7)")
async def stats_cmd(interaction: discord.Interaction, jours: app_commands.Range[int, 1, 90] = 7):
    await interaction.response.send_message(embed=await asyncio.to_thread(discord_ui.stats_embed, jours), ephemeral=EPH,
                                            view=discord_ui.Actions(["statut", "capture"]))



# ---------------------------------------------------------------- /aide
@cmd(name="aide", description="❓ Comment ça marche : les commandes, les persos, les options")
async def aide_cmd(interaction: discord.Interaction):
    await discord_ui.open_help(interaction)              # aide en pages (◀ ▶)


discord_ui.setup(sys.modules[__name__])                # l'interface recoit CE module (pas une 2e copie)

import maj_cmd
maj_cmd.setup(cmd, embed, runner, BASE, EPH, COLORS, NO_WINDOW, ENV, discord, app_commands)

if __name__ == "__main__":
    try:
        client.run(TOKEN)
    except discord.LoginFailure:
        print("Token refusé par Discord : vérifie le token dans discord_config.json (il a peut-être été réinitialisé).")
        sys.exit(2)
