"""Interface Discord : boutons, menus, tableau de bord. Branche par discord_bot.py :  discord_ui.setup(module_du_bot)

- Boutons d'action PERSISTANTS (marchent encore apres un redemarrage du bot) : 📸 Capture, 📊 Stats, ⏹ Stop,
  🔁 Relancer pareil, 📋 Statut. Un message n'en montre que certains (Actions(only=[...])).
- Bot partage entre plusieurs PC : chaque PC recoit tous les clics ; seul le PC du proprietaire (ALLOWED) repond,
  les autres se taisent (comme pour les commandes).
"""
import asyncio
import io
import json
import time

import discord

B = None                                                  # le module discord_bot (setup)
LAST_FILE = None


def setup(bot_module):
    global B, LAST_FILE
    B = bot_module
    LAST_FILE = B.BASE / "logs" / "discord_dernier.json"


def owner(interaction):
    return interaction.user.id in B.ALLOWED


def bar(done, total, n=12):
    """Barre de progression : ███████░░░░░ 7/12."""
    if not total:
        return ""
    k = max(0, min(n, round(n * done / max(1, total))))
    return f"`{'█' * k}{'░' * (n - k)}`  **{done}/{total}**"


# ---------------------------------------------------------------- dernier lancement (🔁 Relancer pareil)
def remember(command, label, kind, details=""):
    try:
        LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_FILE.write_text(json.dumps({"command": list(command), "label": label, "kind": kind, "details": details,
                                         "t": time.time()}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def last():
    try:
        return json.loads(LAST_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError):
        return None


# ---------------------------------------------------------------- actions communes (commandes ET boutons)
async def do_stop(interaction):
    label, emo = B.runner.label, B.runner.emoji
    send = interaction.response.send_message
    if B.runner.stop():
        await send(embed=B.embed("⏹ Script arrêté", f"{emo} **{label}**", B.COLORS["bad"],
                                 tip="Le jeu reste où il en est : relance quand tu veux."), ephemeral=B.EPH, view=Actions(["relancer"]))
    elif (ext := await asyncio.to_thread(B.encours.stop)):              # lance depuis l'assistant, le planning...
        await send(embed=B.embed("⏹ Farm arrêté", f"**{ext.get('label', '?')}**\n(lancé depuis "
                                 f"{B.encours.SOURCES.get(ext.get('source'), '?')})", B.COLORS["bad"],
                                 tip="Le jeu reste où il en est : relance quand tu veux."), ephemeral=B.EPH)
    elif await asyncio.to_thread(__import__("planificateur").stop_planned):
        await send(embed=B.embed("⏹ Farm programmé arrêté", "🗓️ lancé par le planning", B.COLORS["bad"],
                                 tip="/planning pour voir les créneaux"), ephemeral=B.EPH)
    else:
        await send(embed=B.embed("Rien à arrêter", "Aucun script ne tourne en ce moment.", B.COLORS["info"],
                                 tip="/farm pour en lancer un"), ephemeral=B.EPH)


def stats_embed(jours=7):
    import stats
    s = stats.summary(jours)
    t = s["total"]
    e = B.embed(f"📊 Statistiques : {jours} dernier{'s' if jours > 1 else ''} jour{'s' if jours > 1 else ''}",
                f"**{t['runs']} runs** en **{t['sessions']} session(s)** · {stats.hms(t['duree'])} de farm · "
                f"**{t['reussite']} %** finies\n🔋 {t['recharges']} recharge(s) · 🛡️ {t['relances']} relance(s) · "
                f"🔄 {t['reprises']} reprise(s)", B.COLORS["info"], tip="Historique complet : bouton 📊 Stats de l'assistant")
    for m, a in list(s["modes"].items())[:10]:
        e.add_field(name=m, value=f"{a['runs']} runs · {a['sessions']} session(s) · {stats.hms(a['duree'])} · {a['reussite']} %",
                    inline=False)
    lst = [stats.describe(x) for x in stats.summary(0)["dernieres"][:5]]
    e.add_field(name="Dernières sessions", value=("\n".join(lst) or "aucune pour l'instant")[:1024], inline=False)
    return e


async def do_capture(interaction):
    await interaction.response.defer(ephemeral=B.EPH, thinking=True)
    png = await asyncio.to_thread(B.screenshot_png)
    if not png:
        await interaction.followup.send(embed=B.embed("📵 Capture impossible", "L'émulateur est éteint, ou ADB ne le trouve pas.",
                                                      B.COLORS["bad"], tip="Ouvre MuMu avec le jeu, puis réessaie."))
        return
    await interaction.followup.send(file=discord.File(io.BytesIO(png), filename="capture.png"))


async def do_relaunch(interaction):
    lt = last()
    if not lt:
        await interaction.response.send_message(embed=B.embed("Rien à relancer", "Aucun farm lancé depuis Discord pour l'instant.",
                                                               B.COLORS["info"], tip="/farm pour en lancer un"), ephemeral=B.EPH)
        return
    await interaction.response.defer(ephemeral=B.EPH, thinking=True)
    await B.start_script(interaction, lt["command"], lt["label"], False, lt.get("kind", "supply"), lt.get("details", ""))


# ---------------------------------------------------------------- boutons d'action persistants
BUTTONS = {   # cle : (texte, style, action)
    "relancer": ("🔁 Relancer pareil", discord.ButtonStyle.success, do_relaunch),
    "capture":  ("📸 Capture", discord.ButtonStyle.secondary, do_capture),
    "statut":   ("📋 Statut", discord.ButtonStyle.secondary, None),
    "stats":    ("📊 Stats", discord.ButtonStyle.secondary, None),
    "stop":     ("⏹ Stop", discord.ButtonStyle.danger, do_stop),
}


class OwnerView(discord.ui.View):
    """Seul le proprietaire de CE PC est servi ; les autres instances du bot partage restent muettes."""

    async def interaction_check(self, interaction):
        return owner(interaction)


class Actions(OwnerView):
    def __init__(self, only=None):
        super().__init__(timeout=None)
        for key in (only or list(BUTTONS)):
            text, style, _fn = BUTTONS[key]
            btn = discord.ui.Button(label=text, style=style, custom_id=f"mha:{key}")
            btn.callback = self._make(key)
            self.add_item(btn)

    @staticmethod
    def _make(key):
        async def cb(interaction):
            if key == "stats":
                await interaction.response.send_message(embed=await asyncio.to_thread(stats_embed, 7), ephemeral=B.EPH)
            elif key == "statut":
                await B.statut_cmd.callback(interaction)
            else:
                await BUTTONS[key][2](interaction)
        return cb


def register(client):
    """Au demarrage : les boutons des anciens messages remarchent (vues persistantes)."""
    client.add_view(Actions())


# ---------------------------------------------------------------- /planning : supprimer d'un clic
class PlanningView(OwnerView):
    def __init__(self, items):
        super().__init__(timeout=900)
        import planificateur as pl
        opts = [discord.SelectOption(label=f"#{it['id']} · {it['heure']} · {pl.JOURS.get(it['jours'], '')}"[:100],
                                     description=pl.describe(it, short=True).split("  ·  ", 1)[-1].strip()[:100], value=str(it["id"]),
                                     emoji="🗑️") for it in items[:25]]
        if opts:
            sel = discord.ui.Select(placeholder="🗑️ Supprimer un créneau…", options=opts)
            sel.callback = self._delete
            self.add_item(sel)
            self.sel = sel

    async def _delete(self, interaction):
        import planificateur as pl
        i = int(self.sel.values[0])
        ok = await asyncio.to_thread(pl.remove, i)
        await interaction.response.send_message(embed=B.embed(f"🗑️ Créneau #{i} supprimé" if ok else f"Créneau #{i} introuvable", "",
                                                              B.COLORS["info"] if ok else B.COLORS["bad"]), ephemeral=B.EPH)


# ---------------------------------------------------------------- /panneau : tableau de bord en direct
PANEL_EVERY = 20                                          # s entre deux mises a jour
_panel = {"next": ("", 0.0)}                              # cache du prochain farm programme (PowerShell : lent)


def panel_file():
    return B.BASE / "logs" / "discord_panneau.json"


def _next_planned():
    txt, t = _panel["next"]
    if time.time() - t < 300:
        return txt
    try:
        import planificateur as pl
        items = pl.load()
        nxt = pl.next_runs() if items else {}
        best = None
        for it in items:
            when = nxt.get(it["id"])
            if when and when != "-":
                d, h = when.split(" ")
                key = (d.split("/")[1], d.split("/")[0], h)
                if best is None or key < best[0]:
                    best = (key, f"{when} · {pl.describe(it, short=True).split('  ·  ', 1)[-1].strip()}")
        txt = best[1] if best else "aucun"
    except Exception:                                     # noqa: BLE001
        txt = "?"
    _panel["next"] = (txt, time.time())
    return txt


def panel_embed():
    """Contenu du tableau de bord (appele dans un fil : lectures disque / ADB)."""
    import socket
    import stats
    rec = B.encours.read()
    dev = B.emulator_state()
    if B.runner.running or rec:
        label = B.runner.label if B.runner.running else rec.get("label", "?")
        p = B.runner.progress() if B.runner.running else ((rec.get("done"), rec.get("total")) if rec.get("total") else None)
        src = "Discord" if B.runner.running else B.encours.SOURCES.get(rec.get("source"), "?")
        el = B.runner.elapsed() if B.runner.running else B.encours.describe(rec).split(" · ")[-2]
        desc = f"⏳ **{label}**\n" + (bar(*p) + "\n" if p and p[1] else "") + \
               (f"🔗 Étape {rec['step']}\n" if rec and rec.get("step") else "") + f"⏱ {el} · lancé depuis {src}"
        last_line = (B.runner.tail(1) if B.runner.running else rec.get("last", ""))[-120:]
        color = B.COLORS["info"]
    else:
        lt = last()
        desc = "⏸️ **Rien ne tourne**" + (f"\nDernier lancé : {lt['label']}" if lt else "")
        last_line, color = "", 0x5865F2
    e = discord.Embed(title=f"🎮 MHA Bot — {socket.gethostname()}", description=desc, color=color)
    e.add_field(name="Émulateur", value=f"{'🟢 ' + dev if dev else '🔴 éteint / introuvable'}", inline=True)
    running = B.runner.running or rec
    args = (getattr(B.runner, "cmd", []) if B.runner.running else (rec or {}).get("args", [])) if running else []
    r, w = effective_options(args)
    e.add_field(name="Options de ce farm" if running else "Réglage général",
                value=f"🔋 {'oui' if r else 'non'} · 🛡️ {'oui' if w else 'non'}", inline=True)
    t = stats.summary(1)["total"]
    e.add_field(name="Dernières 24 h", value=f"{t['runs']} runs · {stats.hms(t['duree'])}", inline=True)
    e.add_field(name="🗓️ Prochain farm programmé", value=_next_planned()[:200], inline=False)
    import version
    e.add_field(name="Version", value=version.label(B.BASE), inline=True)
    if last_line:
        e.add_field(name="Dernière ligne", value=f"`{last_line.replace('`', chr(39))}`", inline=False)
    e.set_footer(text="Mis à jour à " + time.strftime("%H:%M:%S") + " · se rafraîchit tout seul")
    return e


def effective_options(args):
    """(recharge, gardien) REELLEMENT appliques : --refill / --no-refill de la commande, sinon le reglage general."""
    args = list(args or [])
    r = True if "--refill" in args else False if "--no-refill" in args else bool(B.cfg_get("stamina_refill", False))
    w = True if "--watchdog" in args else False if "--no-watchdog" in args else bool(B.cfg_get("watchdog", False))
    return r, w


def panel_view():
    return Actions(["stop", "capture", "panneau", "stats", "relancer"])


async def _panel_message():
    try:
        p = json.loads(panel_file().read_text(encoding="utf-8"))
        ch = B.client.get_channel(p["channel"]) or await B.client.fetch_channel(p["channel"])
        return await ch.fetch_message(p["message"])
    except discord.NotFound:                              # supprime a la main : on l'oublie
        panel_file().unlink(missing_ok=True)
        return None
    except Exception:                                     # noqa: BLE001 - pas de panneau (ou Discord injoignable)
        return None


async def refresh_panel(msg=None, force=False):
    msg = msg or await _panel_message()
    if not msg:
        return False
    e = await asyncio.to_thread(panel_embed)
    key = _key(e)
    if not force and key == _panel.get("key"):
        return True                                       # rien n'a change : pas d'appel a Discord
    _panel["key"] = key
    try:
        await msg.edit(embed=e, view=panel_view())
        return True
    except discord.NotFound:
        panel_file().unlink(missing_ok=True)
        return False


def _key(e):
    """Contenu qui compte (sans l'heure du pied de page) : on ne modifie le message que s'il change."""
    d = e.to_dict()
    return json.dumps(d.get("description")) + json.dumps([f["value"] for f in d.get("fields", [])])


async def panel_loop():
    while True:
        await asyncio.sleep(PANEL_EVERY)
        try:
            await refresh_panel()
        except Exception as ex:                           # noqa: BLE001 - le panneau ne doit jamais faire tomber le bot
            print(f"(!) tableau de bord : {ex!r}", flush=True)


async def open_panel(interaction):
    """/panneau : (re)cree le tableau de bord en message prive (ou dans le salon des resultats)."""
    await interaction.response.defer(ephemeral=B.EPH, thinking=True)
    old = await _panel_message()
    e = await asyncio.to_thread(panel_embed)
    if B.CHANNEL_ID:
        ch = B.client.get_channel(B.CHANNEL_ID) or await B.client.fetch_channel(B.CHANNEL_ID)
        where = f"dans <#{B.CHANNEL_ID}>"
    else:
        ch = await interaction.user.create_dm()
        where = "en message privé"
    msg = await ch.send(embed=e, view=panel_view())
    _panel["key"] = _key(e)
    panel_file().parent.mkdir(parents=True, exist_ok=True)
    panel_file().write_text(json.dumps({"channel": msg.channel.id, "message": msg.id}), encoding="utf-8")
    if old:
        try:
            await old.delete()                            # un seul tableau de bord a la fois
        except Exception:                                 # noqa: BLE001
            pass
    await interaction.followup.send(embed=B.embed("🎮 Tableau de bord ouvert", f"Il est {where} et se met à jour tout seul "
                                                  f"(toutes les {PANEL_EVERY} s).", B.COLORS["info"],
                                                  tip="/panneau à nouveau pour le faire remonter en bas de la conversation"))


async def _panel_button(interaction):
    await interaction.response.defer()
    await refresh_panel(interaction.message, force=True)


BUTTONS["panneau"] = ("🔄 Actualiser", discord.ButtonStyle.primary, _panel_button)


# ---------------------------------------------------------------- /farm : lanceur guide (menus + boutons)
KIND = {"supply": "supply", "nightops": "nightops", "hero": "hero", "whm": "whm", "aa": "aa"}


class PlanModal(discord.ui.Modal, title="🗓️ Programmer ce farm"):
    heure = discord.ui.TextInput(label="Heure (ex. 02:00 ou 7h30)", default="02:00", max_length=5)
    jours = discord.ui.TextInput(label="Quand : tous / semaine / weekend / une_fois", default="tous", max_length=8)

    def __init__(self, st, refill=None, watchdog=None):
        super().__init__()
        self.st, self.refill, self.watchdog = st, refill, watchdog

    async def on_submit(self, interaction):
        import enchainer as ec
        import planificateur as pl
        try:
            it, _m = await asyncio.to_thread(pl.add, self.heure.value, self.jours.value.strip().lower(),
                                             ec.token(ec.validate(dict(self.st))), refill=self.refill, watchdog=self.watchdog)
        except (ValueError, SystemExit) as e:
            await interaction.response.send_message(embed=B.embed("❌ Créneau refusé", str(e)[:1500], B.COLORS["bad"]),
                                                    ephemeral=B.EPH)
            return
        opts = "\n".join(f"{icon} {name} : **{'oui' if it[k] else 'non'}**" for k, icon, name in
                          (("refill", "🔋", "Recharge de stamina"), ("watchdog", "🛡️", "Chien de garde")) if it.get(k) is not None)
        await interaction.response.send_message(embed=B.embed(
            f"🗓️ Farm programmé #{it['id']}", f"**{it['heure']}** · {pl.JOURS[it['jours']].lower()}\n"
            f"{ec.describe_tokens(it['sequence'])}" + (f"\n{opts}" if opts else ""), B.COLORS["info"],
            tip="/planning pour voir ou supprimer"), ephemeral=B.EPH)


class FarmView(OwnerView):
    """Message de /farm : un menu par reglage du mode choisi ; derniere ligne : 🔋 Recharge, 🛡️ Gardien, Lancer, Programmer.
    Discord : 5 lignes maxi, un menu par ligne -> Supply regroupe ressource + niveau dans un seul menu."""
    RUNS = [1, 2, 3, 5, 10, 15, 20, 30, 50, 100]

    def __init__(self):
        super().__init__(timeout=900)
        import enchainer as ec
        self.ec = ec
        self.key = "supply"
        self.st = dict(ec.MODES_UI["supply"][1])
        self.refill = bool(B.cfg_get("stamina_refill", False))       # reglage enregistre ; ne vaut que pour ce lancement
        self.wd = bool(B.cfg_get("watchdog", False))
        self.build()

    def embed(self):
        ec = self.ec
        title = ec.MODES_UI[self.key][0]
        try:
            desc = ec.describe(ec.validate(dict(self.st)))
        except SystemExit as e:
            desc = f"⚠️ {e}"
        aa = self.st["mode"] == "aa"
        opts = ("🔋 Recharge : —  (pas en Agency)" if aa else f"🔋 Recharge de stamina : **{'oui' if self.refill else 'non'}**") + \
            f"\n🛡️ Chien de garde : **{'oui' if self.wd else 'non'}**"
        e = discord.Embed(title=f"🎮 Que veux-tu farmer ?  ·  {title}", description=f"**{desc}**\n\n{opts}",
                          color=B.COLORS.get(KIND[self.st["mode"]], B.COLORS["info"]))
        tips = "Choisis dans les menus, puis ▶ Lancer (ou 🗓️ Programmer). 🔋 / 🛡️ : pour ce lancement seulement."
        if aa:
            tips += " Floor des réservés : réglage de l'assistant (10 par défaut)."
        e.set_footer(text=tips)
        return e

    def _select(self, placeholder, options, cb, row, mini=1, maxi=1):
        options = options[:25]
        s = discord.ui.Select(placeholder=placeholder, options=options, min_values=mini, max_values=max(1, min(maxi, len(options))),
                              row=row)
        s.callback = cb
        self.add_item(s)

    def _button(self, label, style, cb, row, disabled=False):
        b = discord.ui.Button(label=label, style=style, row=row, disabled=disabled)
        b.callback = cb
        self.add_item(b)

    def _runs_select(self, row):
        n = self.st.get("runs", 1)
        vals = sorted(set(self.RUNS + [n]))
        unit = self.runs_unit()
        self._select(f"Nombre de {unit}", [discord.SelectOption(label=f"{v} {unit}", value=str(v), default=(v == n)) for v in vals],
                     self.on_runs, row)

    def runs_unit(self):
        return "combats" if self.st["mode"] == "aa" else "multi-runs"

    def build(self):
        ec, st = self.ec, self.st
        self.clear_items()
        self._select("Mode", [discord.SelectOption(label=n[:100], value=k, default=(k == self.key))
                              for k, (n, _s) in ec.MODES_UI.items()], self.on_mode, 0)
        heroes = B.heroes_list()
        perso = [discord.SelectOption(label="Perso déjà placé", value="-", default=not st.get("hero"), emoji="🦸")] + \
                [discord.SelectOption(label=f"Combat : {B.hz.pretty(h)}", value=h, default=(st.get("hero") == h)) for h in heroes]
        m = st["mode"]
        if m == "supply":
            self._select("Ressource et niveau", [
                discord.SelectOption(label=f"{n} — niveau {lv}", value=f"{k}:{lv}", default=(st["supply"] == k and st["level"] == lv))
                for k, n in ec.SUPPLY_NAMES.items() for lv in range(ec.MAX_LEVEL[k], 0, -1)], self.on_supply_level, 1)
            self._select("Perso qui combat", perso, self.on_hero, 2)
            self._runs_select(3)
        elif m == "whm":
            self._select("Niveau", [discord.SelectOption(label=f"Niveau {i}", value=str(i), default=(st["level"] == i))
                                    for i in (1, 2, 3)], self.on_level, 1)
            self._select("Perso qui combat", perso, self.on_hero, 2)
            self._runs_select(3)
        elif m == "nightops":
            self._select("Perso qui combat", perso, self.on_hero, 1)
            self._runs_select(2)
        elif m == "hero":
            chosen = set(filter(None, st.get("chars", "").split(",")))
            self._select("Persos à farmer", [discord.SelectOption(label=B.hz.pretty(h), value=h, default=(h in chosen))
                                             for h in heroes], self.on_chars, 1, mini=1, maxi=len(heroes))
            self._select("Perso qui combat", perso, self.on_hero, 2)
        elif m == "aa":
            bots = set(filter(None, str(st.get("bots") or "").split(",")))
            self._select("Bots (2 max)", [discord.SelectOption(label=n, value=k, default=(k in bots)) for k, n in ec.AA_BOTS.items()],
                         self.on_bots, 1, mini=0, maxi=2)
            res = set(filter(None, str(st.get("reserve") or "").split(",")))
            self._select("Persos réservés aux hauts floors", [discord.SelectOption(label=ec.aa_name(c), value=c, default=(c in res))
                                                              for c in ec.aa_chars()], self.on_reserve, 2, mini=0, maxi=25)
            self._select("Reset du permit", [discord.SelectOption(label="Jamais de reset du permit", value="0",
                                                                  default=not st.get("reset"))] +
                         [discord.SelectOption(label=f"Reset à partir du floor {i}", value=str(i), default=(st.get("reset") == i))
                          for i in range(2, 21)], self.on_reset, 3)
            self._button("−", discord.ButtonStyle.secondary, self.minus, 4)
            self._button("+", discord.ButtonStyle.secondary, self.plus, 4)
        if m != "aa":
            self._button(f"🔋 Recharge : {'oui' if self.refill else 'non'}",
                         discord.ButtonStyle.success if self.refill else discord.ButtonStyle.secondary, self.toggle_refill, 4)
        self._button(f"🛡️ Gardien : {'oui' if self.wd else 'non'}",
                     discord.ButtonStyle.success if self.wd else discord.ButtonStyle.secondary, self.toggle_wd, 4)
        self._button("▶ Lancer", discord.ButtonStyle.primary, self.launch, 4)
        if m != "aa":
            self._button("🗓️ Programmer", discord.ButtonStyle.secondary, self.program, 4)
        else:
            self._button("🗓️", discord.ButtonStyle.secondary, self.program, 4)

    async def refresh(self, interaction):
        self.build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def on_mode(self, interaction):
        self.key = interaction.data["values"][0]
        hero = self.st.get("hero")
        self.st = dict(self.ec.MODES_UI[self.key][1])
        if self.key == "hero":
            self.st["chars"] = ",".join(B.default_hero_chars()) or (B.heroes_list()[:1] or [""])[0]
        if self.key == "aa":
            self.st["bots"] = ",".join([b for b in (B.cfg_get("aa_bots") or ["deku", "bakugo"]) if b in self.ec.AA_BOTS][:2])
            self.st["reserve"] = ",".join(c for c in (B.cfg_get("aa_reserved") or []) if c in self.ec.aa_chars()) or None
        elif hero:
            self.st["hero"] = hero
        await self.refresh(interaction)

    async def on_supply_level(self, interaction):
        k, lv = interaction.data["values"][0].split(":")
        self.st["supply"], self.st["level"] = k, int(lv)
        await self.refresh(interaction)

    async def on_level(self, interaction):
        self.st["level"] = int(interaction.data["values"][0])
        await self.refresh(interaction)

    async def on_runs(self, interaction):
        self.st["runs"] = int(interaction.data["values"][0])
        await self.refresh(interaction)

    async def on_hero(self, interaction):
        v = interaction.data["values"][0]
        self.st["hero"] = None if v == "-" else v
        await self.refresh(interaction)

    async def on_chars(self, interaction):
        self.st["chars"] = ",".join(interaction.data["values"])
        await self.refresh(interaction)

    async def on_bots(self, interaction):
        self.st["bots"] = ",".join(interaction.data["values"]) or None
        await self.refresh(interaction)

    async def on_reserve(self, interaction):
        self.st["reserve"] = ",".join(interaction.data["values"]) or "aucun"
        await self.refresh(interaction)

    async def on_reset(self, interaction):
        self.st["reset"] = int(interaction.data["values"][0]) or None
        await self.refresh(interaction)

    async def _runs(self, interaction, d):
        self.st["runs"] = max(1, min(500, self.st.get("runs", 1) + d))
        await self.refresh(interaction)

    async def minus(self, interaction):
        await self._runs(interaction, -1)

    async def plus(self, interaction):
        await self._runs(interaction, 1)

    async def toggle_refill(self, interaction):
        self.refill = not self.refill
        await self.refresh(interaction)

    async def toggle_wd(self, interaction):
        self.wd = not self.wd
        await self.refresh(interaction)

    async def launch(self, interaction):
        await interaction.response.defer(ephemeral=B.EPH, thinking=True)
        st = dict(self.st)
        try:
            details = self.ec.describe(self.ec.validate(dict(st)))
        except SystemExit:
            details = ""
        refill = None if st["mode"] == "aa" else self.refill
        await B.run_mode(interaction, st, KIND[st["mode"]], details, False, refill, self.wd)

    async def program(self, interaction):
        refill = None if self.st["mode"] == "aa" else self.refill             # Agency : pas de recharge de stamina
        await interaction.response.send_modal(PlanModal(dict(self.st), refill, self.wd))


async def open_farm(interaction):
    v = FarmView()
    await interaction.response.send_message(embed=v.embed(), view=v, ephemeral=B.EPH)


# ---------------------------------------------------------------- /aide : pages avec ◀ ▶
def help_pages():
    ec = B.ec
    supply = "\n".join(f"**{B.SUPPLY_NAMES[k]}** — {B.SUPPLY_WHAT[k]} *(niveaux 1-{ec.MAX_LEVEL[k]})*" for k in B.SUPPLY_NAMES)
    return [
        ("🚀 Démarrer", 0x5865F2, [
            ("Le plus simple", "**/farm** — tout se choisit avec des menus et des boutons, rien à taper.\n"
                               "**/panneau** — un tableau de bord qui se met à jour tout seul (farm en cours, Stop, Capture)."),
            ("À savoir", "Le PC et MuMu (jeu ouvert, 1600x900) doivent rester allumés. Un seul farm à la fois.\n"
                         "Les réponses sont privées. Les boutons ne marchent que pour toi."),
        ]),
        ("🎮 Lancer un farm", B.COLORS["supply"], [
            ("Avec des menus", "**/farm** — Supply, Night Ops, Hero Trial, Événements, Agency"),
            ("Commandes directes", "**/supply** · **/nightops** · **/whm** · **/aa** · **/herotrial**\n"
                                   "**/enchainer** — plusieurs modes à la suite"),
            ("📦 Les modes Supply", supply),
            ("🦸 Les persos", f"{B.HERO_TEXT}\nL'option **perso** dit **qui combat** : le bot le place tout seul."),
        ]),
        ("👀 Pendant le farm", B.COLORS["nightops"], [
            ("Suivre", "**/panneau** — tableau de bord en direct\n**/statut** — où ça en est\n**/capture** — l'écran en photo"),
            ("Agir", "**/stop** — tout arrêter (même un farm lancé depuis l'assistant ou le planning)\n"
                     "Sur les messages : 🔁 Relancer pareil · 📸 Capture · ⏹ Stop"),
            ("Alertes automatiques", "🚨 blocage ou plantage (capture + 30 dernières captures) · 📵 émulateur / ADB "
                                     "(reprise automatique) · 🔄 écran inconnu · 📉 image qui se dégrade"),
        ]),
        ("🗓️ Planning", B.COLORS["whm"], [
            ("Programmer", "**/planifier** — heure, jours, puis le mode avec des menus\n"
                           "ou depuis **/farm** : bouton 🗓️ Programmer"),
            ("Voir / supprimer", "**/planning** — la liste, et un menu 🗑️ pour supprimer"),
            ("Bon à savoir", "Le PC peut être en veille (il se réveille), pas éteint. Si un farm tourne déjà, le créneau est sauté."),
        ]),
        ("⚙️ Réglages et stats", B.COLORS["aa"], [
            ("Options", "🔋 **Recharge de stamina** — le code cadeau quand il n'y en a plus\n"
                        "🛡️ **Chien de garde** — maintenance ou jeu bloqué : relance le jeu\n**/reglages** pour les changer"),
            ("Statistiques", "**/stats** — runs, temps de farm, réussite, dernières sessions"),
            ("Mises à jour", "**/maj** — installe la dernière version du bot"),
        ]),
    ]


class HelpView(OwnerView):
    def __init__(self, page=0):
        super().__init__(timeout=600)
        self.page = page
        self.pages = help_pages()
        prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, disabled=page == 0)
        nxt = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, disabled=page == len(self.pages) - 1)
        prev.callback, nxt.callback = self._prev, self._next
        self.add_item(prev)
        for i, (title, _c, _f) in enumerate(self.pages):
            b = discord.ui.Button(label=title.split(" ", 1)[1], style=discord.ButtonStyle.primary if i == page
                                  else discord.ButtonStyle.secondary, row=1)
            b.callback = self._goto(i)
            self.add_item(b)
        self.add_item(nxt)

    def embed(self):
        title, color, fields = self.pages[self.page]
        e = discord.Embed(title=f"❓ Aide · {title}", color=color)
        for n, v in fields:
            e.add_field(name=n, value=v[:1024], inline=False)
        e.set_footer(text=f"Page {self.page + 1}/{len(self.pages)} · ◀ ▶ pour naviguer")
        return e

    async def _show(self, interaction, page):
        v = HelpView(page)
        await interaction.response.edit_message(embed=v.embed(), view=v)

    async def _prev(self, interaction):
        await self._show(interaction, max(0, self.page - 1))

    async def _next(self, interaction):
        await self._show(interaction, min(len(self.pages) - 1, self.page + 1))

    def _goto(self, i):
        async def cb(interaction):
            await self._show(interaction, i)
        return cb


async def open_help(interaction):
    v = HelpView(0)
    await interaction.response.send_message(embed=v.embed(), view=v, ephemeral=B.EPH)


# ---------------------------------------------------------------- résumé quotidien (/resume)
def resume_embed():
    """Bilan des dernieres 24 h + ce qui est programme dans les 24 h qui viennent."""
    import stats
    s = stats.summary(1)
    t = s["total"]
    e = discord.Embed(title=f"🌙 Résumé du {time.strftime('%d/%m')}", color=0x5865F2,
                      description=f"**{t['runs']} runs** en **{t['sessions']} session(s)** · {stats.hms(t['duree'])} de farm · "
                                  f"**{t['reussite']} %** finies\n🔋 {t['recharges']} recharge(s) · 🛡️ {t['relances']} relance(s) · "
                                  f"🔄 {t['reprises']} reprise(s)")
    modes = "\n".join(f"**{m}** — {a['runs']} runs · {stats.hms(a['duree'])} · {a['reussite']} %" for m, a in list(s["modes"].items())[:8])
    e.add_field(name="Par mode", value=modes or "Aucun farm aujourd'hui.", inline=False)
    today = time.strftime("%Y%m%d")
    try:
        inc = [d for d in (B.BASE / "logs" / "incidents").glob(f"{today}-*") if d.is_dir()]
    except OSError:
        inc = []
    kinds = {}
    for d in inc:
        try:
            k = json.loads((d / "incident.json").read_text(encoding="utf-8")).get("kind", "incident")
        except (OSError, ValueError):
            k = "incident"
        kinds[k] = kinds.get(k, 0) + 1
    names = {"arret": "arrêt", "plantage": "plantage", "incident": "blocage", "adb": "ADB / émulateur", "reprise": "écran inconnu",
             "derive": "image qui se dégrade", "planning": "farm programmé"}
    e.add_field(name="Incidents du jour", value=", ".join(f"{n} × {names.get(k, k)}" for k, n in kinds.items()) or "aucun 👍",
                inline=False)
    rec = B.encours.read()
    if B.runner.running or rec:
        e.add_field(name="En cours", value=(B.runner.label if B.runner.running else B.encours.describe(rec))[:200], inline=False)
    try:
        import planificateur as pl
        items = {it["id"]: it for it in pl.load()}
        nxt = pl.next_runs() if items else {}
        soon = []
        for i, when in nxt.items():
            if i in items and when and when != "-":
                soon.append(f"{when} · {pl.describe(items[i], short=True).split('  ·  ', 1)[-1]}")
        e.add_field(name="🗓️ Programmé", value="\n".join(sorted(soon)[:6]) or "rien de programmé", inline=False)
    except Exception:                                     # noqa: BLE001
        pass
    e.set_footer(text="Résumé quotidien · /resume pour changer l'heure ou le couper")
    return e


def _resume_mark():
    return B.BASE / "logs" / "discord_resume_envoye"


async def resume_tick(now=None):
    """Un passage : a l'heure reglee (config 'resume_heure'), envoie le resume, une seule fois par jour. -> True si envoye."""
    now = now or time.localtime()
    h = B.cfg_get("resume_heure")
    hm = time.strftime("%H:%M", now)
    if not h or hm < h:
        return False
    today = time.strftime("%Y-%m-%d", now)
    mark = _resume_mark()
    if mark.exists() and mark.read_text(encoding="utf-8").strip() == today:
        return False
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.write_text(today, encoding="utf-8")
    if hm > _plus(h, 60):                                 # bot demarre bien apres l'heure : pas de resume en retard
        return False
    await B.send_owner(await asyncio.to_thread(resume_embed), view=Actions(["stats", "capture"]))
    return True


async def resume_loop():
    while True:
        await asyncio.sleep(30)
        try:
            await resume_tick()
        except Exception as ex:                           # noqa: BLE001
            print(f"(!) resume quotidien : {ex!r}", flush=True)


def _plus(hhmm, minutes):
    h, m = (int(x) for x in hhmm.split(":"))
    t = min(23 * 60 + 59, h * 60 + m + minutes)
    return f"{t // 60:02d}:{t % 60:02d}"


async def resume_command(interaction, heure=None, desactiver=False, maintenant=False):
    import planificateur as pl
    if maintenant:
        await interaction.response.send_message(embed=await asyncio.to_thread(resume_embed), ephemeral=B.EPH,
                                                view=Actions(["stats", "capture"]))
        return
    if desactiver:
        B.cfg_set("resume_heure", None)
        await interaction.response.send_message(embed=B.embed("🌙 Résumé quotidien coupé", "", B.COLORS["info"],
                                                              tip="/resume heure:21:00 pour le remettre"), ephemeral=B.EPH)
        return
    if heure:
        try:
            h, _j, _t = pl.validate(heure, "tous", "nightops:1")
        except ValueError as e:
            await interaction.response.send_message(embed=B.embed("❌ Heure invalide", str(e), B.COLORS["bad"]), ephemeral=B.EPH)
            return
        B.cfg_set("resume_heure", h)
        await interaction.response.send_message(embed=B.embed("🌙 Résumé quotidien réglé", f"Chaque jour à **{h}** "
                                                              f"({'dans le salon des résultats' if B.CHANNEL_ID else 'en message privé'}).",
                                                              B.COLORS["info"], tip="/resume maintenant:Oui pour le voir tout de suite"),
                                                ephemeral=B.EPH)
        return
    cur = B.cfg_get("resume_heure")
    await interaction.response.send_message(embed=B.embed("🌙 Résumé quotidien", f"Actuellement : **{cur or 'coupé'}**.\n"
                                                          "`/resume heure:21:00` · `/resume desactiver:Oui` · `/resume maintenant:Oui`",
                                                          B.COLORS["info"]), ephemeral=B.EPH)


# ---------------------------------------------------------------- version installee (lanceur) + « quoi de neuf »
async def announce_version():
    """Au demarrage du bot : si la version installee a change depuis la derniere fois -> message « Nouvelle version »."""
    import version
    i = version.info(B.BASE)
    if i["dev"] or not i["version"]:
        return False
    mark = B.BASE / "logs" / "discord_version_vue"
    try:
        seen = mark.read_text(encoding="utf-8").strip()
    except OSError:
        seen = None
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.write_text(i["version"], encoding="utf-8")
    if seen is None or seen == i["version"]:              # 1er demarrage : on note seulement
        return False
    e = discord.Embed(title=f"🆕 Nouvelle version installée : {i['version']}",
                      description=f"**Quoi de neuf :** {i['message'] or '(pas de message)'}", color=0x2ECC71)
    older = [x for x in version.news(B.BASE, 4) if x["version"] != i["version"]][:3]
    if older:
        e.add_field(name="Avant", value="\n".join(f"`{x['version']}` — {x['message']}"[:200] for x in older), inline=False)
    e.set_footer(text="/aide pour voir ce que le bot sait faire")
    await B.send_owner(e)
    return True
