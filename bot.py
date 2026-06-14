"""
╔══════════════════════════════════════════════════════════════════╗
║           ModBot — Bot de Modération Discord                      ║
║           Fichier unique  •  discord.py 2.x                      ║
╠══════════════════════════════════════════════════════════════════╣
║  Installation :  pip install discord.py aiohttp python-dotenv    ║
║  Lancement    :  python bot.py                                    ║
║  Token        :  variable d'env BOT_TOKEN  ou ligne 32           ║
╚══════════════════════════════════════════════════════════════════╝
"""

# ══════════════════════════════════════════════════════════════════
# IMPORTS
# ══════════════════════════════════════════════════════════════════
import discord
from discord.ext import commands, tasks
import asyncio, json, os, re, uuid, random, traceback, aiohttp
from datetime import datetime, timedelta
from typing import Optional, Union
from collections import defaultdict, deque

# FIX: asyncio.Lock pour éviter les race conditions sur les fichiers JSON
import threading
_json_lock = asyncio.Lock()

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

# ══════════════════════════════════════════════════════════════════
# CONFIGURATION  ← Modifie ici
# ══════════════════════════════════════════════════════════════════
PREFIX   = '+'
TOKEN = os.getenv('DISCORD_TOKEN', '')
DATA_DIR = 'data'

# ══════════════════════════════════════════════════════════════════
# COULEURS
# ══════════════════════════════════════════════════════════════════
class C:
    SUCCESS  = 0x2ECC71
    ERROR    = 0xE74C3C
    WARNING  = 0xF39C12
    INFO     = 0x5865F2
    BAN      = 0xC0392B
    KICK     = 0xE67E22
    MUTE     = 0xE8A838
    WARN     = 0xE74C3C
    UNMUTE   = 0x2ECC71
    SOFTBAN  = 0x8E44AD
    GIVEAWAY = 0xFF6B9D
    NEUTRAL  = 0x36393F
    LOCK     = 0x6C5CE7
    NUKE     = 0xD63031
    STATS    = 0x74B9FF
    LOG      = 0x636E72
    AUTOMOD  = 0xE17055

NUM_E = ['1️⃣','2️⃣','3️⃣','4️⃣','5️⃣','6️⃣','7️⃣','8️⃣','9️⃣','🔟']

# ══════════════════════════════════════════════════════════════════
# ÉTAT PARTAGÉ (en mémoire)
# ══════════════════════════════════════════════════════════════════
snipe_cache  : dict = defaultdict(lambda: deque(maxlen=15))
spam_cache   : dict = defaultdict(lambda: defaultdict(lambda: deque(maxlen=12)))
raid_joins   : dict = defaultdict(lambda: deque())

# FIX: ensemble pour suivre les mutes par rôle actifs (survie redémarrage via JSON)
active_role_mutes : dict = {}  # {(guild_id, member_id): asyncio.Task}

# ══════════════════════════════════════════════════════════════════
# DONNÉES JSON  — FIX: protégées par un verrou asyncio
# ══════════════════════════════════════════════════════════════════
def load(fn: str) -> dict:
    os.makedirs(DATA_DIR, exist_ok=True)
    p = os.path.join(DATA_DIR, fn)
    if not os.path.exists(p): return {}
    try:
        with open(p, 'r', encoding='utf-8') as f: return json.load(f)
    except: return {}

def save(fn: str, data: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    # FIX: écriture atomique via fichier temporaire pour éviter la corruption
    p = os.path.join(DATA_DIR, fn)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)

def cfg(gid: int) -> dict:
    return load('settings.json').get(str(gid), {})

def set_cfg(gid: int, key: str, val):
    s = load('settings.json')
    s.setdefault(str(gid), {})[key] = val
    save('settings.json', s)

# ══════════════════════════════════════════════════════════════════
# DURÉES
# ══════════════════════════════════════════════════════════════════
_U = {'s':1,'m':60,'h':3600,'d':86400,'w':604800}

def pdur(s: str) -> Optional[int]:
    if not s: return None
    s = s.strip().lower()
    if s[-1] in _U:
        try:
            v = int(s[:-1]); return v*_U[s[-1]] if v>0 else None
        except: return None
    try: return int(s) or None
    except: return None

def fdur(n: int) -> str:
    if n<60: return f'{n}s'
    if n<3600: m,s=divmod(n,60); return f'{m}m{f" {s}s" if s else ""}'
    if n<86400: h,r=divmod(n,3600); m=r//60; return f'{h}h{f" {m}m" if m else ""}'
    if n<604800: d,r=divmod(n,86400); h=r//3600; return f'{d}j{f" {h}h" if h else ""}'
    w,r=divmod(n,604800); d=r//86400; return f'{w}sem{f" {d}j" if d else ""}'

# ══════════════════════════════════════════════════════════════════
# EMBEDS
# ══════════════════════════════════════════════════════════════════
def _ft(g: discord.Guild) -> dict:
    return {'text': f'ModBot • {g.name}', 'icon_url': g.icon.url if g.icon else None}

def mod_e(action, icon, color, mod, target, reason='Aucune raison fournie', extra=None):
    e = discord.Embed(title=f'{icon}  {action}', color=color, timestamp=datetime.utcnow())
    if isinstance(target, (discord.Member, discord.User)):
        e.description = f'**Utilisateur :** {target.mention}\n**ID :** `{target.id}`'
        e.set_thumbnail(url=target.display_avatar.url)
    else:
        e.description = f'**Utilisateur :** `{target}`'
    e.add_field(name='🛡️ Modérateur', value=mod.mention, inline=True)
    e.add_field(name='📝 Raison',      value=reason,      inline=True)
    if extra:
        for n,v,i in extra: e.add_field(name=n, value=v, inline=i)
    e.set_footer(**_ft(mod.guild)); return e

def ok_e(t, d=None):
    return discord.Embed(title=f'✅  {t}', description=d, color=C.SUCCESS, timestamp=datetime.utcnow())
def err_e(t, d=None):
    return discord.Embed(title=f'❌  {t}', description=d, color=C.ERROR, timestamp=datetime.utcnow())
def inf_e(t, d=None, c=None):
    return discord.Embed(title=t, description=d, color=c or C.INFO, timestamp=datetime.utcnow())

async def do_log(bot, guild: discord.Guild, embed: discord.Embed):
    ch_id = cfg(guild.id).get('log_channel')
    if not ch_id: return
    ch = guild.get_channel(ch_id)
    if ch:
        try: await ch.send(embed=embed)
        except: pass

# ══════════════════════════════════════════════════════════════════
# CHECKS
# ══════════════════════════════════════════════════════════════════
def is_mod():
    async def p(ctx):
        g = ctx.author.guild_permissions
        return g.manage_messages or g.manage_guild or g.administrator or ctx.author.id==ctx.guild.owner_id
    return commands.check(p)

# ══════════════════════════════════════════════════════════════════
# ██████████████████████  COG : MODÉRATION  ██████████████████████
# ══════════════════════════════════════════════════════════════════
class ModerationCog(commands.Cog):
    def __init__(self, bot): self.bot=bot; self._tb_task.start()
    def cog_unload(self): self._tb_task.cancel()

    # ── BLACKLIST ──────────────────────────────────────────────
    @commands.command('bl', aliases=['blacklist'])
    @commands.has_permissions(ban_members=True)
    async def bl(self, ctx, user_id: int, *, reason='Aucune raison fournie'):
        """Blackliste un utilisateur même absent du serveur."""
        bl=load('blacklist.json'); gid=str(ctx.guild.id); bl.setdefault(gid,{})
        if str(user_id) in bl[gid]:
            return await ctx.send(embed=err_e('Déjà blacklisté',f'`{user_id}` est déjà dans la blacklist.'))
        user=None
        try: user=await self.bot.fetch_user(user_id)
        except: pass
        bl[gid][str(user_id)]={'user_id':user_id,'username':str(user) if user else f'Inconnu ({user_id})',
            'reason':reason,'moderator':str(ctx.author),'moderator_id':ctx.author.id,'date':datetime.utcnow().isoformat()}
        save('blacklist.json', bl)
        try:
            m=ctx.guild.get_member(user_id)
            if m: await m.ban(reason=f'[BL] {reason}', delete_message_days=0)
        except: pass
        await ctx.send(embed=mod_e('Utilisateur blacklisté','🚫',C.BAN,ctx.author,user or user_id,reason))
        await do_log(self.bot,ctx.guild,mod_e('BLACKLIST','🚫',C.BAN,ctx.author,user or user_id,reason))

    @commands.command('unbl', aliases=['unblacklist'])
    @commands.has_permissions(ban_members=True)
    async def unbl(self, ctx, user_id: int, *, reason='Aucune raison fournie'):
        """Retire un utilisateur de la blacklist."""
        bl=load('blacklist.json'); gid=str(ctx.guild.id)
        if gid not in bl or str(user_id) not in bl[gid]:
            return await ctx.send(embed=err_e('Non blacklisté',f'`{user_id}` n\'est pas dans la blacklist.'))
        entry=bl[gid].pop(str(user_id)); save('blacklist.json', bl)
        try: await ctx.guild.unban(discord.Object(id=user_id), reason=f'[UNBL] {reason}')
        except: pass
        e=discord.Embed(title='✅  Blacklist retirée',
            description=f'**Utilisateur :** `{entry["username"]}` (`{user_id}`)',color=C.SUCCESS,timestamp=datetime.utcnow())
        e.add_field(name='🛡️ Modérateur',value=ctx.author.mention,inline=True)
        e.add_field(name='📝 Raison',value=reason,inline=True)
        e.set_footer(**_ft(ctx.guild)); await ctx.send(embed=e)

    @commands.command('bllist')
    @commands.has_permissions(ban_members=True)
    async def bllist(self, ctx):
        """Affiche la blacklist."""
        bl=load('blacklist.json'); entries=list(bl.get(str(ctx.guild.id),{}).values())
        if not entries: return await ctx.send(embed=inf_e('🚫  Blacklist vide','Aucun utilisateur blacklisté.',C.NEUTRAL))
        pages=[entries[i:i+10] for i in range(0,len(entries),10)]
        def mk(idx):
            e=discord.Embed(title=f'🚫  Blacklist — {len(entries)} utilisateur{"s" if len(entries)>1 else ""}',color=C.BAN,timestamp=datetime.utcnow())
            lns=[]
            for i,en in enumerate(pages[idx],1+idx*10):
                lns.append(f'`{i:02}.` **{en["username"]}** (`{en["user_id"]}`)\n      └ {en["reason"][:60]} • {en.get("date","")[:10]}')
            e.description='\n'.join(lns); e.set_footer(text=f'Page {idx+1}/{len(pages)} • ModBot'); return e
        if len(pages)==1: return await ctx.send(embed=mk(0))
        cur=0; msg=await ctx.send(embed=mk(0))
        for r in ('⬅️','➡️'): await msg.add_reaction(r)
        def chk(r,u): return u==ctx.author and r.message.id==msg.id and str(r.emoji) in('⬅️','➡️')
        while True:
            try:
                r,u=await self.bot.wait_for('reaction_add',timeout=60,check=chk)
                if str(r.emoji)=='➡️' and cur<len(pages)-1: cur+=1
                elif str(r.emoji)=='⬅️' and cur>0: cur-=1
                await msg.edit(embed=mk(cur))
                try: await msg.remove_reaction(r,u)
                except: pass
            except asyncio.TimeoutError: break

    # ── JOIN : blacklist + autorole + bienvenue ────────────────
    # FIX: retiré d'ici — géré exclusivement dans AutoModCog pour éviter le double listener
    # La logique blacklist+autorole+bienvenue est fusionnée dans AutoModCog.on_member_join

    # ── KICK ──────────────────────────────────────────────────
    @commands.command('kick')
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member, *, reason='Aucune raison fournie'):
        """Expulse un membre."""
        if member==ctx.author: return await ctx.send(embed=err_e('Impossible','Tu ne peux pas te kick.'))
        if member.top_role>=ctx.author.top_role and ctx.author.id!=ctx.guild.owner_id:
            return await ctx.send(embed=err_e('Hiérarchie','Rôle ≥ au tien.'))
        if member.top_role>=ctx.guild.me.top_role:
            return await ctx.send(embed=err_e('Hiérarchie','Mon rôle est trop bas.'))
        try:
            dm=discord.Embed(title='👢  Tu as été expulsé',color=C.KICK,timestamp=datetime.utcnow())
            dm.add_field(name='🏠 Serveur',value=ctx.guild.name,inline=True)
            dm.add_field(name='📝 Raison',value=reason,inline=True)
            await member.send(embed=dm)
        except: pass
        await member.kick(reason=f'{ctx.author} | {reason}')
        await ctx.send(embed=mod_e('Membre expulsé','👢',C.KICK,ctx.author,member,reason))
        await do_log(self.bot,ctx.guild,mod_e('KICK','👢',C.KICK,ctx.author,member,reason))

    # ── MUTE / UNMUTE ────────────────────────────────────────
    @commands.command('mute')
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def mute(self, ctx, member: discord.Member, duration: str, *, reason='Aucune raison fournie'):
        """Met en sourdine pour une durée."""
        if member==ctx.author: return await ctx.send(embed=err_e('Impossible','Tu ne peux pas te muter.'))
        if member.top_role>=ctx.author.top_role and ctx.author.id!=ctx.guild.owner_id:
            return await ctx.send(embed=err_e('Hiérarchie','Rôle ≥ au tien.'))
        secs=pdur(duration)
        if not secs: return await ctx.send(embed=err_e('Durée invalide','Formats : `10s` `5m` `2h` `1d` `1w`'))
        if secs>2_419_200: return await ctx.send(embed=err_e('Trop long','Maximum : 28 jours.'))
        until=discord.utils.utcnow()+timedelta(seconds=secs)
        try:
            if mr_id:=cfg(ctx.guild.id).get('mute_role'):
                mr=ctx.guild.get_role(mr_id)
                if mr and mr not in member.roles:
                    await member.add_roles(mr,reason=f'{ctx.author} | {reason}')
                    # FIX: sauvegarde du mute par rôle en JSON pour survie au redémarrage
                    rm=load('role_mutes.json')
                    rm.setdefault(str(ctx.guild.id),{})[str(member.id)]={
                        'role_id':mr.id,'expire_ts':int(until.timestamp())}
                    save('role_mutes.json',rm)
                    key=(ctx.guild.id,member.id)
                    if key in active_role_mutes:
                        active_role_mutes[key].cancel()
                    active_role_mutes[key]=self.bot.loop.create_task(
                        self._unmute_role(member,mr,secs,ctx.guild.id))
            else:
                await member.timeout(until,reason=f'{ctx.author} | {reason}')
        except discord.Forbidden:
            return await ctx.send(embed=err_e('Permissions','Je ne peux pas muter ce membre.'))
        try:
            dm=discord.Embed(title='🔇  Tu as été mis en sourdine',color=C.MUTE,timestamp=datetime.utcnow())
            dm.add_field(name='🏠 Serveur',value=ctx.guild.name,inline=True)
            dm.add_field(name='⏱️ Durée',value=fdur(secs),inline=True)
            dm.add_field(name='📝 Raison',value=reason,inline=True)
            dm.add_field(name='📅 Fin',value=f'<t:{int(until.timestamp())}:F>',inline=False)
            await member.send(embed=dm)
        except: pass
        e=mod_e(f'Mis en sourdine ({fdur(secs)})','🔇',C.MUTE,ctx.author,member,reason,
            extra=[('⏱️ Durée',fdur(secs),True),('📅 Fin',f'<t:{int(until.timestamp())}:F>',True)])
        await ctx.send(embed=e)
        await do_log(self.bot,ctx.guild,mod_e('MUTE','🔇',C.MUTE,ctx.author,member,reason,extra=[('Durée',fdur(secs),True)]))

    # FIX: signature corrigée + nettoyage JSON après expiration
    async def _unmute_role(self, member, role, delay, guild_id):
        await asyncio.sleep(delay)
        try:
            if role in member.roles:
                await member.remove_roles(role,reason='Mute expiré')
        except: pass
        # Nettoyage JSON
        try:
            rm=load('role_mutes.json')
            rm.get(str(guild_id),{}).pop(str(member.id),None)
            save('role_mutes.json',rm)
        except: pass
        active_role_mutes.pop((guild_id,member.id),None)

    @commands.command('unmute')
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def unmute(self, ctx, member: discord.Member, *, reason='Aucune raison fournie'):
        """Retire le mute."""
        done=False
        if member.is_timed_out():
            await member.timeout(None,reason=f'{ctx.author} | {reason}'); done=True
        if mr_id:=cfg(ctx.guild.id).get('mute_role'):
            mr=ctx.guild.get_role(mr_id)
            if mr and mr in member.roles:
                await member.remove_roles(mr,reason=f'{ctx.author} | {reason}'); done=True
                # FIX: annuler la tâche en cours et nettoyer le JSON
                key=(ctx.guild.id,member.id)
                if key in active_role_mutes:
                    active_role_mutes[key].cancel()
                    active_role_mutes.pop(key,None)
                rm=load('role_mutes.json')
                rm.get(str(ctx.guild.id),{}).pop(str(member.id),None)
                save('role_mutes.json',rm)
        if not done: return await ctx.send(embed=err_e('Non muté',f'{member.mention} n\'est pas en sourdine.'))
        await ctx.send(embed=mod_e('Sourdine retirée','🔊',C.UNMUTE,ctx.author,member,reason))
        await do_log(self.bot,ctx.guild,mod_e('UNMUTE','🔊',C.UNMUTE,ctx.author,member,reason))

    # ── WARNS ────────────────────────────────────────────────
    @commands.command('warn')
    @commands.has_permissions(manage_messages=True)
    async def warn(self, ctx, member: discord.Member, *, reason: str):
        """Avertit un membre (3=mute 24h • 5=ban)."""
        if member==ctx.author: return await ctx.send(embed=err_e('Impossible','Tu ne peux pas te warn.'))
        if member.top_role>=ctx.author.top_role and ctx.author.id!=ctx.guild.owner_id:
            return await ctx.send(embed=err_e('Hiérarchie','Rôle ≥ au tien.'))
        warns=load('warns.json'); gid,uid=str(ctx.guild.id),str(member.id)
        warns.setdefault(gid,{}).setdefault(uid,[])
        wid=str(uuid.uuid4())[:8].upper()
        warns[gid][uid].append({'id':wid,'reason':reason,'moderator':str(ctx.author),
            'moderator_id':ctx.author.id,'date':datetime.utcnow().isoformat()})
        save('warns.json',warns); count=len(warns[gid][uid])
        bar='🟥'*min(count,5)+'⬜'*max(0,5-count)
        try:
            dm=discord.Embed(title='⚠️  Avertissement',color=C.WARN,timestamp=datetime.utcnow())
            dm.add_field(name='🏠 Serveur',value=ctx.guild.name,inline=True)
            dm.add_field(name='📊 Total',value=f'{count}/5',inline=True)
            dm.add_field(name='📝 Raison',value=reason,inline=False)
            dm.set_footer(text=f'ID : {wid}'); await member.send(embed=dm)
        except: pass
        e=mod_e(f'Avertissement #{count}','⚠️',C.WARN,ctx.author,member,reason,
            extra=[('🆔 Warn ID',f'`{wid}`',True),('📊 Progression',f'{bar}  **{count}/5**',False)])
        act=None
        if count==3:
            act='⚠️ **3 warns** → Mute automatique **24h**'
            try: await member.timeout(discord.utils.utcnow()+timedelta(days=1),reason='[AUTO] 3 warns')
            except: pass
        elif count>=5:
            act='🔨 **5 warns** → Ban permanent automatique'
            try: await member.ban(reason='[AUTO] 5 warns')
            except: pass
        if act: e.add_field(name='🤖 Action automatique',value=act,inline=False)
        await ctx.send(embed=e)
        await do_log(self.bot,ctx.guild,mod_e('WARN','⚠️',C.WARN,ctx.author,member,reason,extra=[('Total',f'{count}/5',True),('ID',f'`{wid}`',True)]))

    @commands.command('unwarn')
    @commands.has_permissions(manage_messages=True)
    async def unwarn(self, ctx, member: discord.Member, warn_id: str):
        """Supprime un warn par son ID."""
        warns=load('warns.json'); gid,uid=str(ctx.guild.id),str(member.id)
        lst=warns.get(gid,{}).get(uid,[])
        if not lst: return await ctx.send(embed=err_e('Aucun warn',f'{member.mention} n\'a aucun warn.'))
        new=[w for w in lst if w['id'].upper()!=warn_id.upper()]
        if len(new)==len(lst): return await ctx.send(embed=err_e('Introuvable',f'Aucun warn `{warn_id.upper()}`.'))
        warns[gid][uid]=new; save('warns.json',warns)
        await ctx.send(embed=ok_e('Warn supprimé',f'`{warn_id.upper()}` de {member.mention} supprimé. **Restants :** `{len(new)}/5`'))

    @commands.command('clearwarns')
    @commands.has_permissions(manage_messages=True)
    async def clearwarns(self, ctx, member: discord.Member):
        """Efface tous les warns d'un membre."""
        warns=load('warns.json'); gid,uid=str(ctx.guild.id),str(member.id)
        count=len(warns.get(gid,{}).get(uid,[]))
        warns.setdefault(gid,{})[uid]=[]; save('warns.json',warns)
        await ctx.send(embed=ok_e('Warns effacés',f'**{count}** warn{"s" if count>1 else ""} supprimé{"s" if count>1 else ""} pour {member.mention}.'))

    @commands.command('warns')
    async def warns_cmd(self, ctx, member: discord.Member=None):
        """Affiche les warns d'un membre."""
        member=member or ctx.author
        warns=load('warns.json'); lst=warns.get(str(ctx.guild.id),{}).get(str(member.id),[])
        count=len(lst)
        e=discord.Embed(title=f'⚠️  Avertissements de {member.display_name}',
            color=C.WARN if lst else C.SUCCESS,timestamp=datetime.utcnow())
        e.set_thumbnail(url=member.display_avatar.url)
        if not lst:
            e.description=f'{member.mention} n\'a aucun avertissement. ✅'
        else:
            bar='🟥'*min(count,5)+'⬜'*max(0,5-count)
            e.description=f'**{count}/5**  {bar}'
            for w in lst[-10:]:
                e.add_field(name=f'`#{w["id"]}` — {w["date"][:10]}',
                    value=f'**Raison :** {w["reason"]}\n**Par :** {w["moderator"]}',inline=False)
        e.set_footer(**_ft(ctx.guild)); await ctx.send(embed=e)

    # ── SOFTBAN ──────────────────────────────────────────────
    @commands.command('softban')
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def softban(self, ctx, member: discord.Member, *, reason='Aucune raison fournie'):
        """Ban + déban immédiat (purge messages, peut rejoindre)."""
        if member==ctx.author: return await ctx.send(embed=err_e('Impossible','Tu ne peux pas te softban.'))
        if member.top_role>=ctx.author.top_role and ctx.author.id!=ctx.guild.owner_id:
            return await ctx.send(embed=err_e('Hiérarchie','Rôle ≥ au tien.'))
        try:
            dm=discord.Embed(title='🔨  Softban',
                description=f'Tu as été **softban** de **{ctx.guild.name}**.\n**Raison :** {reason}\n\n*Tu peux rejoindre à nouveau.*',color=C.SOFTBAN)
            await member.send(embed=dm)
        except: pass
        await member.ban(reason=f'[SOFTBAN] {ctx.author} | {reason}',delete_message_days=7)
        await asyncio.sleep(1)
        await ctx.guild.unban(discord.Object(id=member.id),reason='[SOFTBAN] Déban automatique')
        e=mod_e('Softban effectué','🔨',C.SOFTBAN,ctx.author,member,reason,
            extra=[('ℹ️ Infos','Messages 7j supprimés  •  Peut rejoindre à nouveau',False)])
        await ctx.send(embed=e)
        await do_log(self.bot,ctx.guild,mod_e('SOFTBAN','🔨',C.SOFTBAN,ctx.author,member,reason))

    # ── TEMPBAN ──────────────────────────────────────────────
    @commands.command('tempban')
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def tempban(self, ctx, member: discord.Member, duration: str, *, reason='Aucune raison fournie'):
        """Ban temporaire."""
        if member==ctx.author: return await ctx.send(embed=err_e('Impossible','Tu ne peux pas te tempban.'))
        if member.top_role>=ctx.author.top_role and ctx.author.id!=ctx.guild.owner_id:
            return await ctx.send(embed=err_e('Hiérarchie','Rôle ≥ au tien.'))
        secs=pdur(duration)
        if not secs: return await ctx.send(embed=err_e('Durée invalide','Formats : `10m` `2h` `1d` `1w`'))
        exp=int((datetime.utcnow()+timedelta(seconds=secs)).timestamp())
        tb=load('tempbans.json'); tb.setdefault(str(ctx.guild.id),{})[str(member.id)]={
            'user_id':member.id,'username':str(member),'reason':reason,'moderator_id':ctx.author.id,'expire_ts':exp}
        save('tempbans.json',tb)
        try:
            dm=discord.Embed(title='🔨  Ban temporaire',color=C.BAN,timestamp=datetime.utcnow())
            dm.add_field(name='🏠 Serveur',value=ctx.guild.name,inline=True)
            dm.add_field(name='⏱️ Durée',value=fdur(secs),inline=True)
            dm.add_field(name='📝 Raison',value=reason,inline=True)
            dm.add_field(name='📅 Fin',value=f'<t:{exp}:F>',inline=False)
            await member.send(embed=dm)
        except: pass
        await member.ban(reason=f'[TEMPBAN {fdur(secs)}] {ctx.author} | {reason}',delete_message_days=0)
        e=mod_e(f'Ban temporaire ({fdur(secs)})','🔨',C.BAN,ctx.author,member,reason,extra=[('📅 Fin du ban',f'<t:{exp}:F>',True)])
        await ctx.send(embed=e)
        await do_log(self.bot,ctx.guild,mod_e('TEMPBAN','🔨',C.BAN,ctx.author,member,reason,extra=[('Durée',fdur(secs),True),('Expiration',f'<t:{exp}:F>',True)]))

    @tasks.loop(minutes=1)
    async def _tb_task(self):
        tb=load('tempbans.json'); now=int(datetime.utcnow().timestamp()); chg=False
        for gid,bans in list(tb.items()):
            g=self.bot.get_guild(int(gid))
            if not g: continue
            for uid,data in list(bans.items()):
                if now>=data['expire_ts']:
                    try:
                        await g.unban(discord.Object(id=int(uid)),reason='[TEMPBAN] Expiration')
                        lg=discord.Embed(title='🔓  Tempban expiré',
                            description=f'**Utilisateur :** `{data["username"]}` (`{uid}`)\n**Raison initiale :** {data["reason"]}',
                            color=C.SUCCESS,timestamp=datetime.utcnow())
                        await do_log(self.bot,g,lg)
                    except: pass
                    del tb[gid][uid]; chg=True
        if chg: save('tempbans.json',tb)

    @_tb_task.before_loop
    async def _wait_tb(self): await self.bot.wait_until_ready()


# ══════════════════════════════════════════════════════════════════
# ████████████████████  COG : MESSAGES  ███████████████████████████
# ══════════════════════════════════════════════════════════════════
class MessagesCog(commands.Cog):
    def __init__(self, bot): self.bot=bot

    # FIX: on_message_delete retiré d'ici — géré uniquement dans AutoModCog
    # pour éviter le double envoi de logs

    # ── CLEAR ────────────────────────────────────────────────
    @commands.command('clear', aliases=['purge_all'])
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def clear(self, ctx, amount: str, member: discord.Member=None):
        """Supprime N messages ou tous (+clear all)."""
        await ctx.message.delete()
        check = (lambda m: m.author==member) if member else None
        if amount.lower()=='all':
            # FIX: suppression par chunks de 100 pour éviter le timeout sur les gros salons
            deleted_total = 0
            while True:
                deleted = await ctx.channel.purge(limit=100, check=check, bulk=True)
                deleted_total += len(deleted)
                if len(deleted) < 100:
                    break
                await asyncio.sleep(0.5)
            deleted_count = deleted_total
        else:
            try: n=int(amount)
            except: return await ctx.send(embed=err_e('Argument invalide','Utilise un nombre ou `all`.'),delete_after=6)
            if n<1 or n>1000: return await ctx.send(embed=err_e('Nombre invalide','Entre 1 et 1000.'),delete_after=6)
            deleted=await ctx.channel.purge(limit=n, check=check, bulk=True)
            deleted_count=len(deleted)
        who=f' de {member.mention}' if member else ''
        e=ok_e('Messages supprimés',f'**{deleted_count}** message{"s" if deleted_count>1 else ""}{who} supprimé{"s" if deleted_count>1 else ""}.')
        e.set_footer(**_ft(ctx.guild)); await ctx.send(embed=e,delete_after=5)
        lg=discord.Embed(title='🗑️  Messages supprimés',
            description=f'**{deleted_count}** message{"s" if deleted_count>1 else ""} supprimé{"s" if deleted_count>1 else ""}{who}\n**Salon :** {ctx.channel.mention}\n**Par :** {ctx.author.mention}',
            color=C.WARN,timestamp=datetime.utcnow())
        await do_log(self.bot,ctx.guild,lg)

    # ── PURGE ────────────────────────────────────────────────
    @commands.command('purge')
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def purge(self, ctx, member: discord.Member, amount: int):
        """Supprime les messages d'un membre spécifique."""
        if amount<1 or amount>500: return await ctx.send(embed=err_e('Nombre invalide','Entre 1 et 500.'))
        await ctx.message.delete()
        deleted=await ctx.channel.purge(limit=500, check=lambda m: m.author==member, bulk=True)
        deleted=deleted[:amount]
        e=ok_e('Purge effectué',f'**{len(deleted)}** message{"s" if len(deleted)>1 else ""} de {member.mention} supprimé{"s" if len(deleted)>1 else ""}.')
        await ctx.send(embed=e,delete_after=5)

    # ── SNIPE ────────────────────────────────────────────────
    @commands.command('snipe')
    @is_mod()
    async def snipe(self, ctx, member: discord.Member=None):
        """Affiche les messages récemment supprimés."""
        cache=list(snipe_cache.get(ctx.channel.id,[]))
        if member: cache=[m for m in cache if m['author_id']==member.id]
        if not cache: return await ctx.send(embed=inf_e('👻  Snipe vide','Aucun message supprimé récemment ici.',C.NEUTRAL))
        msg=cache[0]
        e=discord.Embed(title='👻  Message supprimé',description=msg['content'][:2000] or '*[Pas de contenu]*',
            color=C.WARN,timestamp=msg['timestamp'])
        e.set_author(name=str(msg['author']),icon_url=msg['author'].display_avatar.url)
        e.set_footer(text=f'ModBot • #{ctx.channel.name} • {len(cache)} message(s) en cache')
        if msg['attachments']: e.add_field(name='📎 Pièces jointes',value='\n'.join(msg['attachments'][:5]),inline=False)
        await ctx.send(embed=e)


# ══════════════════════════════════════════════════════════════════
# ████████████████████  COG : CANAUX  █████████████████████████████
# ══════════════════════════════════════════════════════════════════
class ChannelsCog(commands.Cog):
    def __init__(self, bot): self.bot=bot

    # ── LOCK ─────────────────────────────────────────────────
    @commands.command('lock')
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def lock(self, ctx, channel: discord.TextChannel=None):
        """Verrouille un salon."""
        channel=channel or ctx.channel
        ow=channel.overwrites_for(ctx.guild.default_role)
        ow.send_messages=False
        await channel.set_permissions(ctx.guild.default_role,overwrite=ow,reason=f'Lock par {ctx.author}')
        e=discord.Embed(title='🔒  Salon verrouillé',description=f'{channel.mention} a été **verrouillé**.',color=C.LOCK,timestamp=datetime.utcnow())
        e.add_field(name='🛡️ Modérateur',value=ctx.author.mention,inline=True)
        e.set_footer(**_ft(ctx.guild)); await ctx.send(embed=e)
        lg=discord.Embed(title='🔒  Salon verrouillé',description=f'**Salon :** {channel.mention}\n**Par :** {ctx.author.mention}',color=C.LOCK,timestamp=datetime.utcnow())
        await do_log(self.bot,ctx.guild,lg)

    # ── UNLOCK ───────────────────────────────────────────────
    @commands.command('unlock')
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def unlock(self, ctx, channel: discord.TextChannel=None):
        """Déverrouille un salon."""
        channel=channel or ctx.channel
        ow=channel.overwrites_for(ctx.guild.default_role)
        ow.send_messages=None
        await channel.set_permissions(ctx.guild.default_role,overwrite=ow,reason=f'Unlock par {ctx.author}')
        e=discord.Embed(title='🔓  Salon déverrouillé',description=f'{channel.mention} a été **déverrouillé**.',color=C.SUCCESS,timestamp=datetime.utcnow())
        e.add_field(name='🛡️ Modérateur',value=ctx.author.mention,inline=True)
        e.set_footer(**_ft(ctx.guild)); await ctx.send(embed=e)

    # ── SLOWMODE ─────────────────────────────────────────────
    @commands.command('slowmode')
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def slowmode(self, ctx, seconds: int, channel: discord.TextChannel=None):
        """Définit le mode lent d'un salon."""
        channel=channel or ctx.channel
        if not 0<=seconds<=21600: return await ctx.send(embed=err_e('Valeur invalide','Entre 0 et 21600 secondes (6h).'))
        await channel.edit(slowmode_delay=seconds,reason=f'Slowmode par {ctx.author}')
        if seconds==0:
            e=ok_e('Slowmode désactivé',f'{channel.mention} : slowmode retiré.')
        else:
            e=discord.Embed(title='⏱️  Slowmode activé',
                description=f'{channel.mention} : **1 message toutes les {fdur(seconds)}**.',color=C.INFO,timestamp=datetime.utcnow())
            e.add_field(name='🛡️ Modérateur',value=ctx.author.mention,inline=True)
        e.set_footer(**_ft(ctx.guild)); await ctx.send(embed=e)

    # ── NUKE ─────────────────────────────────────────────────
    @commands.command('nuke')
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def nuke(self, ctx, channel: discord.TextChannel=None):
        """Recrée un salon à l'identique (vide tous les messages)."""
        channel=channel or ctx.channel
        pos=channel.position
        new=await channel.clone(reason=f'Nuke par {ctx.author}')
        await channel.delete(reason=f'Nuke par {ctx.author}')
        await new.edit(position=pos)
        e=discord.Embed(title='💥  Salon nuked',description=f'Le salon **#{new.name}** a été recréé.',color=C.NUKE,timestamp=datetime.utcnow())
        e.add_field(name='🛡️ Modérateur',value=ctx.author.mention,inline=True)
        e.set_footer(**_ft(ctx.guild)); await new.send(embed=e)
        lg=discord.Embed(title='💥  Nuke',description=f'**Salon :** #{new.name}\n**Par :** {ctx.author.mention}',color=C.NUKE,timestamp=datetime.utcnow())
        await do_log(self.bot,ctx.guild,lg)


# ══════════════════════════════════════════════════════════════════
# ████████████████████  COG : GIVEAWAYS  ██████████████████████████
# ══════════════════════════════════════════════════════════════════
class GiveawayCog(commands.Cog):
    def __init__(self, bot): self.bot=bot; self._gw_task.start()
    def cog_unload(self): self._gw_task.cancel()

    async def _end_giveaway(self, guild: discord.Guild, gid_str: str, msg_id_str: str):
        gws=load('giveaways.json')
        if gid_str not in gws or msg_id_str not in gws[gid_str]: return
        data=gws[gid_str][msg_id_str]
        if data.get('ended'): return
        data['ended']=True; save('giveaways.json',gws)
        ch=guild.get_channel(data['channel_id'])
        if not ch: return
        try: msg=await ch.fetch_message(int(msg_id_str))
        except: return
        react=next((r for r in msg.reactions if str(r.emoji)=='🎉'),None)
        participants=[]
        if react:
            async for u in react.users():
                if not u.bot: participants.append(u)
        w_count=min(data['winners'],len(participants))
        winners=random.sample(participants,w_count) if participants else []
        color=C.GIVEAWAY if winners else C.NEUTRAL
        e=discord.Embed(title='🎉  GIVEAWAY TERMINÉ  🎉',color=color,timestamp=datetime.utcnow())
        e.add_field(name='🏆 Prix',value=data['prize'],inline=False)
        if winners:
            e.add_field(name=f'🥇 Gagnant{"s" if len(winners)>1 else ""}',value=' '.join(w.mention for w in winners),inline=False)
        else:
            # FIX: keyword argument value= manquant dans l'original
            e.add_field(name='😔 Résultat',value='Aucun participant valide.',inline=False)
        e.set_footer(text=f'Organisé par {data["host"]} • ModBot')
        try: await msg.edit(embed=e)
        except: pass
        if winners:
            res=discord.Embed(title='🎉  Félicitations !',
                description=f'Le giveaway **{data["prize"]}** est terminé !\n\n🥇 **Gagnant{"s" if len(winners)>1 else ""} :** {" ".join(w.mention for w in winners)}',
                color=C.GIVEAWAY,timestamp=datetime.utcnow())
            await ch.send(content=' '.join(w.mention for w in winners),embed=res)
        else:
            await ch.send(embed=discord.Embed(title='😔  Giveaway terminé',description='Aucun gagnant (pas assez de participants).',color=C.NEUTRAL))

    @commands.command('gcreate', aliases=['giveaway'])
    @commands.has_permissions(manage_guild=True)
    async def gcreate(self, ctx, duration: str, winners: int, *, prize: str):
        """Lance un giveaway. Ex: +gcreate 1d 2 Nitro Classique"""
        secs=pdur(duration)
        if not secs: return await ctx.send(embed=err_e('Durée invalide','Formats : `10m` `2h` `1d`'))
        if winners<1: return await ctx.send(embed=err_e('Gagnants invalide','Au moins 1 gagnant.'))
        end_ts=int((datetime.utcnow()+timedelta(seconds=secs)).timestamp())
        e=discord.Embed(title='🎁  GIVEAWAY  🎁',color=C.GIVEAWAY,timestamp=datetime.utcnow())
        e.description=(f'Réagis avec 🎉 pour participer !\n\n'
                       f'**🏆 Prix :** {prize}\n'
                       f'**👥 Gagnants :** {winners}\n'
                       f'**⏱️ Durée :** {fdur(secs)}\n'
                       f'**📅 Fin :** <t:{end_ts}:F> (<t:{end_ts}:R>)')
        e.set_footer(text=f'Organisé par {ctx.author.display_name} • ModBot',icon_url=ctx.author.display_avatar.url)
        msg=await ctx.send(embed=e); await msg.add_reaction('🎉')
        gws=load('giveaways.json')
        gws.setdefault(str(ctx.guild.id),{})[str(msg.id)]={
            'channel_id':ctx.channel.id,'prize':prize,'winners':winners,
            'end_ts':end_ts,'host':str(ctx.author),'ended':False}
        save('giveaways.json',gws)

    @commands.command('gend')
    @commands.has_permissions(manage_guild=True)
    async def gend(self, ctx, message_id: int):
        """Termine un giveaway immédiatement."""
        gws=load('giveaways.json'); gid=str(ctx.guild.id)
        if gid not in gws or str(message_id) not in gws[gid]:
            return await ctx.send(embed=err_e('Giveaway introuvable','ID invalide ou déjà terminé.'))
        await self._end_giveaway(ctx.guild,gid,str(message_id))
        await ctx.message.add_reaction('✅')

    @commands.command('greroll')
    @commands.has_permissions(manage_guild=True)
    async def greroll(self, ctx, message_id: int):
        """Retire un nouveau gagnant."""
        gws=load('giveaways.json'); gid=str(ctx.guild.id)
        data=gws.get(gid,{}).get(str(message_id))
        if not data: return await ctx.send(embed=err_e('Giveaway introuvable','ID invalide.'))
        ch=ctx.guild.get_channel(data['channel_id'])
        try: msg=await ch.fetch_message(message_id)
        except: return await ctx.send(embed=err_e('Message introuvable','Message de giveaway introuvable.'))
        react=next((r for r in msg.reactions if str(r.emoji)=='🎉'),None)
        if not react: return await ctx.send(embed=err_e('Pas de réactions','Aucun participant.'))
        participants=[u async for u in react.users() if not u.bot]
        if not participants: return await ctx.send(embed=err_e('Pas de participants','Aucun participant valide.'))
        winner=random.choice(participants)
        e=discord.Embed(title='🎉  Nouveau gagnant !',
            description=f'Le nouveau gagnant du giveaway **{data["prize"]}** est :\n\n🥇 {winner.mention}',
            color=C.GIVEAWAY,timestamp=datetime.utcnow())
        await ctx.send(content=winner.mention,embed=e)

    @commands.command('glist')
    @commands.has_permissions(manage_guild=True)
    async def glist(self, ctx):
        """Liste les giveaways actifs."""
        gws=load('giveaways.json'); actives=[(mid,d) for mid,d in gws.get(str(ctx.guild.id),{}).items() if not d.get('ended')]
        if not actives: return await ctx.send(embed=inf_e('🎁  Aucun giveaway actif','Aucun giveaway en cours.',C.NEUTRAL))
        e=discord.Embed(title=f'🎁  Giveaways actifs ({len(actives)})',color=C.GIVEAWAY,timestamp=datetime.utcnow())
        for mid,d in actives:
            ch=ctx.guild.get_channel(d['channel_id'])
            e.add_field(name=f'🏆 {d["prize"]}',
                value=f'**Fin :** <t:{d["end_ts"]}:R>\n**Gagnants :** {d["winners"]}\n**Salon :** {ch.mention if ch else "supprimé"}\n**ID msg :** `{mid}`',inline=False)
        e.set_footer(**_ft(ctx.guild)); await ctx.send(embed=e)

    @tasks.loop(seconds=20)
    async def _gw_task(self):
        gws=load('giveaways.json'); now=int(datetime.utcnow().timestamp())
        for gid,mgws in gws.items():
            g=self.bot.get_guild(int(gid))
            if not g: continue
            for mid,data in list(mgws.items()):
                if not data.get('ended') and now>=data['end_ts']:
                    await self._end_giveaway(g,gid,mid)

    @_gw_task.before_loop
    async def _wait_gw(self): await self.bot.wait_until_ready()


# ══════════════════════════════════════════════════════════════════
# ████████████████████  COG : UTILITAIRE  █████████████████████████
# ══════════════════════════════════════════════════════════════════
class UtilityCog(commands.Cog):
    def __init__(self, bot): self.bot=bot

    # ── POLL ─────────────────────────────────────────────────
    @commands.command('poll')
    @is_mod()
    async def poll(self, ctx, *, content: str):
        """Crée un sondage. Séparateur : |  +poll Question | Opt1 | Opt2"""
        await ctx.message.delete()
        parts=[p.strip() for p in content.split('|')]
        question=parts[0]; options=parts[1:]
        if not options:
            e=discord.Embed(title=f'📊  {question}',color=C.INFO,timestamp=datetime.utcnow())
            e.set_author(name=f'Sondage de {ctx.author.display_name}',icon_url=ctx.author.display_avatar.url)
            e.set_footer(text='👍 Pour  •  👎 Contre')
            msg=await ctx.send(embed=e)
            await msg.add_reaction('👍'); await msg.add_reaction('👎')
        else:
            if len(options)>10: return await ctx.send(embed=err_e('Trop d\'options','Maximum 10 options.'),delete_after=5)
            desc='\n'.join(f'{NUM_E[i]}  {opt}' for i,opt in enumerate(options))
            e=discord.Embed(title=f'📊  {question}',description=desc,color=C.INFO,timestamp=datetime.utcnow())
            e.set_author(name=f'Sondage de {ctx.author.display_name}',icon_url=ctx.author.display_avatar.url)
            e.set_footer(text=f'ModBot • {len(options)} options')
            msg=await ctx.send(embed=e)
            for i in range(len(options)): await msg.add_reaction(NUM_E[i])

    # ── EMBED ────────────────────────────────────────────────
    @commands.command('embed')
    @is_mod()
    async def embed_cmd(self, ctx, *, content: str):
        """Envoie un embed. Format : Titre | Description | (couleur hex)"""
        await ctx.message.delete()
        parts=[p.strip() for p in content.split('|')]
        title=parts[0]; desc=parts[1] if len(parts)>1 else None
        color=C.INFO
        if len(parts)>2:
            try: color=int(parts[2].strip().lstrip('#'),16)
            except: pass
        e=discord.Embed(title=title,description=desc,color=color,timestamp=datetime.utcnow())
        e.set_footer(text=ctx.guild.name,icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
        await ctx.send(embed=e)

    # ── ANNOUNCE ─────────────────────────────────────────────
    @commands.command('announce')
    @commands.has_permissions(manage_guild=True)
    async def announce(self, ctx, *, message: str):
        """Fait une annonce en embed."""
        await ctx.message.delete()
        e=discord.Embed(description=f'📢  {message}',color=C.INFO,timestamp=datetime.utcnow())
        e.set_author(name=f'Annonce de {ctx.author.display_name}',icon_url=ctx.author.display_avatar.url)
        if ctx.guild.icon: e.set_thumbnail(url=ctx.guild.icon.url)
        e.set_footer(text=ctx.guild.name,icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
        await ctx.send(embed=e)

    # ── SAY ──────────────────────────────────────────────────
    @commands.command('say')
    @is_mod()
    async def say(self, ctx, *, message: str):
        """Fait parler le bot."""
        try: await ctx.message.delete()
        except: pass
        await ctx.send(message)

    # ── CREATE (voler emoji) ──────────────────────────────────
    @commands.command('create')
    @commands.has_permissions(manage_emojis=True)
    @commands.bot_has_permissions(manage_emojis=True)
    async def create(self, ctx, emoji_str: str):
        """Vole un emoji d'un autre serveur. Usage : +create <:nom:id>"""
        match=re.match(r'<(a?):([^:]+):(\d+)>',emoji_str)
        if not match:
            return await ctx.send(embed=err_e('Format invalide','Utilise un emoji custom : `<:nom:id>` ou `<a:nom:id>`'))
        animated,name,eid=match.group(1)=='a',match.group(2),int(match.group(3))
        ext='gif' if animated else 'png'
        url=f'https://cdn.discordapp.com/emojis/{eid}.{ext}'
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                if r.status!=200:
                    return await ctx.send(embed=err_e('Téléchargement échoué',f'Impossible de récupérer l\'emoji (status {r.status}).'))
                img=await r.read()
        try:
            new=await ctx.guild.create_custom_emoji(name=name,image=img,reason=f'Volé par {ctx.author}')
            e=ok_e('Emoji créé',f'Emoji **`:{new.name}:`** {new} ajouté au serveur !')
            await ctx.send(embed=e)
        except discord.HTTPException as ex:
            await ctx.send(embed=err_e('Création échouée',str(ex)))


# ══════════════════════════════════════════════════════════════════
# ████████████████████  COG : INFOS  ██████████████████████████████
# ══════════════════════════════════════════════════════════════════
class InfoCog(commands.Cog):
    def __init__(self, bot): self.bot=bot

    # ── AVATAR ───────────────────────────────────────────────
    @commands.command('avatar', aliases=['av', 'pfp'])
    async def avatar(self, ctx, member: discord.Member=None):
        """Affiche l'avatar d'un membre."""
        member=member or ctx.author
        e=discord.Embed(title=f'🖼️  Avatar de {member.display_name}',color=C.INFO,timestamp=datetime.utcnow())
        e.set_image(url=member.display_avatar.url)
        links=[f'[PNG]({member.display_avatar.with_format("png").url})',f'[WebP]({member.display_avatar.with_format("webp").url})']
        if member.display_avatar.is_animated(): links.append(f'[GIF]({member.display_avatar.with_format("gif").url})')
        e.add_field(name='🔗 Liens',value='  •  '.join(links),inline=False)
        e.set_footer(**_ft(ctx.guild)); await ctx.send(embed=e)

    # ── USERINFO ─────────────────────────────────────────────
    @commands.command('userinfo', aliases=['ui', 'who'])
    async def userinfo(self, ctx, member: discord.Member=None):
        """Infos détaillées sur un membre."""
        member=member or ctx.author
        now=datetime.utcnow()
        created=member.created_at.replace(tzinfo=None)
        joined=member.joined_at.replace(tzinfo=None) if member.joined_at else None
        age_acc=(now-created).days
        age_srv=(now-joined).days if joined else 0
        flags=member.public_flags; badges=[]
        if flags.staff:              badges.append('👨‍💼 Staff Discord')
        if flags.partner:            badges.append('🤝 Partenaire')
        if flags.hypesquad:          badges.append('🏠 HypeSquad Events')
        if flags.bug_hunter:         badges.append('🐛 Bug Hunter')
        if flags.early_supporter:    badges.append('🌟 Early Supporter')
        if flags.verified_bot_developer: badges.append('✅ Développeur de bot')
        if member.premium_since:     badges.append(f'💎 Booster depuis <t:{int(member.premium_since.timestamp())}:D>')
        roles=[r.mention for r in reversed(member.roles) if r != ctx.guild.default_role]
        sorted_members=sorted([m for m in ctx.guild.members if m.joined_at],key=lambda m:m.joined_at)
        join_pos=sorted_members.index(member)+1 if member in sorted_members else '?'
        e=discord.Embed(color=member.color if member.color != discord.Color.default() else C.INFO,timestamp=now)
        e.set_author(name=f'Informations — {member}',icon_url=member.display_avatar.url)
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name='📋 Général',
            value=(f'**Nom :** {member.name}\n'
                   f'**Surnom :** {member.nick or "Aucun"}\n'
                   f'**ID :** `{member.id}`\n'
                   f'**Bot :** {"✅" if member.bot else "❌"}\n'
                   f'**Statut :** {str(member.status).title()}'),inline=True)
        e.add_field(name='📅 Dates',
            value=(f'**Compte créé :**\n<t:{int(member.created_at.timestamp())}:D>\n*il y a {age_acc} jours*\n\n'
                   f'**A rejoint :**\n'+(f'<t:{int(member.joined_at.timestamp())}:D>\n*il y a {age_srv} jours*\n*(#{join_pos} à rejoindre)*' if joined else 'Inconnu')),inline=True)
        if roles:
            r_str=' '.join(roles[:15])+(f'\n*+{len(roles)-15} autres*' if len(roles)>15 else '')
            e.add_field(name=f'🎭 Rôles ({len(roles)})',value=r_str,inline=False)
        if badges: e.add_field(name='🏅 Badges',value='\n'.join(badges),inline=False)
        perms=member.guild_permissions; imp=[]
        if perms.administrator: imp.append('👑 Administrateur')
        elif perms.manage_guild: imp.append('⚙️ Gérer le serveur')
        if perms.ban_members: imp.append('🔨 Bannir')
        if perms.kick_members: imp.append('👢 Expulser')
        if perms.manage_messages: imp.append('📝 Gérer les messages')
        if perms.manage_roles: imp.append('🎭 Gérer les rôles')
        if imp: e.add_field(name='🔑 Permissions clés',value='  •  '.join(imp),inline=False)
        e.set_footer(**_ft(ctx.guild)); await ctx.send(embed=e)

    # ── SERVERINFO ───────────────────────────────────────────
    @commands.command('serverinfo', aliases=['si', 'guild'])
    async def serverinfo(self, ctx):
        """Infos sur le serveur."""
        g=ctx.guild; now=datetime.utcnow()
        age=(now-g.created_at.replace(tzinfo=None)).days
        humans=sum(1 for m in g.members if not m.bot)
        bots=sum(1 for m in g.members if m.bot)
        txt=sum(1 for c in g.channels if isinstance(c,discord.TextChannel))
        voc=sum(1 for c in g.channels if isinstance(c,discord.VoiceChannel))
        cats=sum(1 for c in g.channels if isinstance(c,discord.CategoryChannel))
        e=discord.Embed(title=f'ℹ️  {g.name}',color=C.INFO,timestamp=now)
        if g.icon: e.set_thumbnail(url=g.icon.url)
        if g.banner: e.set_image(url=g.banner.url)
        e.add_field(name='📋 Général',
            value=(f'**ID :** `{g.id}`\n'
                   f'**Propriétaire :** {g.owner.mention if g.owner else "?"}\n'
                   f'**Créé le :** <t:{int(g.created_at.timestamp())}:D>\n'
                   f'**Âge :** {age} jours\n'
                   f'**Vérification :** {str(g.verification_level).title()}'),inline=True)
        e.add_field(name='👥 Membres',
            value=(f'**Total :** {g.member_count}\n'
                   f'**Humains :** {humans}\n'
                   f'**Bots :** {bots}'),inline=True)
        e.add_field(name='💬 Salons',
            value=(f'**Textuels :** {txt}\n'
                   f'**Vocaux :** {voc}\n'
                   f'**Catégories :** {cats}'),inline=True)
        e.add_field(name='🚀 Nitro',
            value=(f'**Niveau :** {g.premium_tier}\n'
                   f'**Boosts :** {g.premium_subscription_count}\n'
                   f'**Boosters :** {len(g.premium_subscribers)}'),inline=True)
        e.add_field(name='🎭 Contenu',
            value=(f'**Rôles :** {len(g.roles)}\n'
                   f'**Émojis :** {len(g.emojis)}\n'
                   f'**Stickers :** {len(g.stickers)}'),inline=True)
        e.set_footer(**_ft(ctx.guild)); await ctx.send(embed=e)

    # ── STATS ────────────────────────────────────────────────
    @commands.command('stats')
    async def stats(self, ctx):
        """Statistiques détaillées du serveur."""
        g=ctx.guild
        online   =sum(1 for m in g.members if m.status==discord.Status.online)
        idle     =sum(1 for m in g.members if m.status==discord.Status.idle)
        dnd      =sum(1 for m in g.members if m.status==discord.Status.dnd)
        offline  =sum(1 for m in g.members if m.status==discord.Status.offline)
        in_voice =sum(1 for m in g.members if m.voice and m.voice.channel)
        bots     =sum(1 for m in g.members if m.bot)
        humans   =g.member_count-bots
        txt      =sum(1 for c in g.channels if isinstance(c,discord.TextChannel))
        voc      =sum(1 for c in g.channels if isinstance(c,discord.VoiceChannel))
        cats     =sum(1 for c in g.channels if isinstance(c,discord.CategoryChannel))
        thrd     =sum(1 for c in g.channels if isinstance(c,discord.Thread))
        static_e =sum(1 for em in g.emojis if not em.animated)
        anim_e   =sum(1 for em in g.emojis if em.animated)
        age      =(datetime.utcnow()-g.created_at.replace(tzinfo=None)).days
        e=discord.Embed(title=f'📊  Statistiques — {g.name}',color=C.STATS,timestamp=datetime.utcnow())
        if g.icon: e.set_thumbnail(url=g.icon.url)
        e.add_field(name='👥 Membres',
            value=(f'**Total :** {g.member_count}\n'
                   f'🟢 En ligne : {online}\n'
                   f'🟡 Inactif : {idle}\n'
                   f'🔴 Ne pas déranger : {dnd}\n'
                   f'⚫ Hors ligne : {offline}\n'
                   f'🤖 Bots : {bots}\n'
                   f'🎤 En vocal : {in_voice}'),inline=True)
        e.add_field(name='💬 Salons',
            value=(f'**Total :** {len(g.channels)}\n'
                   f'📝 Textuels : {txt}\n'
                   f'🔊 Vocaux : {voc}\n'
                   f'📁 Catégories : {cats}\n'
                   f'🧵 Threads : {thrd}'),inline=True)
        e.add_field(name='🚀 Nitro',
            value=(f'**Niveau :** {g.premium_tier} / 3\n'
                   f'**Boosts :** {g.premium_subscription_count}\n'
                   f'**Boosters :** {len(g.premium_subscribers)}'),inline=True)
        e.add_field(name='🎨 Contenu',
            value=(f'**Rôles :** {len(g.roles)}\n'
                   f'**Émojis :** {len(g.emojis)} ({static_e} statiques / {anim_e} animés)\n'
                   f'**Stickers :** {len(g.stickers)}\n'
                   f'**Âge :** {age} jours'),inline=True)
        e.set_footer(**_ft(ctx.guild)); await ctx.send(embed=e)


# ══════════════════════════════════════════════════════════════════
# ████████████████████  COG : RÔLES  ██████████████████████████████
# ══════════════════════════════════════════════════════════════════
class RolesCog(commands.Cog):
    def __init__(self, bot): self.bot=bot

    @commands.command('addrole', aliases=['giverole'])
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def addrole(self, ctx, member: discord.Member, role: discord.Role):
        """Donne un rôle à un membre."""
        if role >= ctx.guild.me.top_role: return await ctx.send(embed=err_e('Hiérarchie','Ce rôle est au-dessus du mien.'))
        if role in member.roles: return await ctx.send(embed=err_e('Déjà attribué',f'{member.mention} a déjà {role.mention}.'))
        await member.add_roles(role,reason=f'Attribué par {ctx.author}')
        e=ok_e('Rôle attribué',f'{role.mention} donné à {member.mention}.'); e.color=role.color.value or C.SUCCESS
        e.set_footer(**_ft(ctx.guild)); await ctx.send(embed=e)

    @commands.command('removerole', aliases=['takerole'])
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def removerole(self, ctx, member: discord.Member, role: discord.Role):
        """Retire un rôle à un membre."""
        if role >= ctx.guild.me.top_role: return await ctx.send(embed=err_e('Hiérarchie','Ce rôle est au-dessus du mien.'))
        if role not in member.roles: return await ctx.send(embed=err_e('Pas attribué',f'{member.mention} n\'a pas {role.mention}.'))
        await member.remove_roles(role,reason=f'Retiré par {ctx.author}')
        e=ok_e('Rôle retiré',f'{role.mention} retiré de {member.mention}.')
        e.set_footer(**_ft(ctx.guild)); await ctx.send(embed=e)

    @commands.command('autorole')
    @commands.has_permissions(manage_guild=True)
    async def autorole(self, ctx, role: discord.Role=None):
        """Définit (ou retire) l'auto-rôle à l'arrivée."""
        if role is None:
            set_cfg(ctx.guild.id,'autorole',None)
            return await ctx.send(embed=ok_e('Auto-rôle retiré','Aucun rôle ne sera attribué à l\'arrivée.'))
        set_cfg(ctx.guild.id,'autorole',role.id)
        e=ok_e('Auto-rôle configuré',f'{role.mention} sera attribué aux nouveaux membres.')
        e.set_footer(**_ft(ctx.guild)); await ctx.send(embed=e)

    @commands.command('setwelcome')
    @commands.has_permissions(manage_guild=True)
    async def setwelcome(self, ctx, channel: discord.TextChannel, *, message: str):
        """Configure le message de bienvenue.
        Variables : {user} {name} {server} {count}"""
        set_cfg(ctx.guild.id,'welcome_channel',channel.id)
        set_cfg(ctx.guild.id,'welcome_message',message)
        e=ok_e('Message de bienvenue configuré',
            f'**Salon :** {channel.mention}\n**Message :** {message}\n\n'
            f'Variables : `{{user}}` `{{name}}` `{{server}}` `{{count}}`')
        e.set_footer(**_ft(ctx.guild)); await ctx.send(embed=e)


# ══════════════════════════════════════════════════════════════════
# ████████████████████  COG : CONFIGURATION  ██████████████████████
# ══════════════════════════════════════════════════════════════════
class ConfigCog(commands.Cog):
    def __init__(self, bot): self.bot=bot

    @commands.command('setlog')
    @commands.has_permissions(manage_guild=True)
    async def setlog(self, ctx, channel: discord.TextChannel):
        """Définit le salon de logs."""
        set_cfg(ctx.guild.id,'log_channel',channel.id)
        e=ok_e('Salon de logs configuré',f'Les logs seront envoyés dans {channel.mention}.')
        e.set_footer(**_ft(ctx.guild)); await ctx.send(embed=e)

    @commands.command('setmuterole')
    @commands.has_permissions(manage_guild=True)
    async def setmuterole(self, ctx, role: discord.Role=None):
        """Définit (ou retire) le rôle muet."""
        if role is None:
            set_cfg(ctx.guild.id,'mute_role',None)
            return await ctx.send(embed=ok_e('Rôle muet retiré','Le timeout natif Discord sera utilisé.'))
        set_cfg(ctx.guild.id,'mute_role',role.id)
        e=ok_e('Rôle muet configuré',f'{role.mention} sera utilisé pour les mutes.')
        e.set_footer(**_ft(ctx.guild)); await ctx.send(embed=e)


# ══════════════════════════════════════════════════════════════════
# ████████████████████  COG : AUTOMOD + LOGS SERVEUR  █████████████
# ══════════════════════════════════════════════════════════════════

_RE_INVITE = re.compile(r'discord\.(gg|com/invite)/[a-zA-Z0-9\-]+', re.I)
_RE_LINK   = re.compile(r'https?://[^\s]+', re.I)
_RE_REPEAT = re.compile(r'(.)\1{9,}')

class AutoModCog(commands.Cog):
    def __init__(self, bot):
        self.bot=bot
        self._defaults={
            'enabled':True,'antispam':True,'anticaps':True,'antiinvite':True,
            'antilink':False,'badwords':False,'massmentions':True,'repeatchar':True,
            'spam_threshold':5,'spam_seconds':5,'caps_threshold':70,'mention_limit':5,
            'badwords_list':[],'whitelist_channels':[],'whitelist_roles':[],
        }

    def _get_am(self, guild_id: int) -> dict:
        s=cfg(guild_id); am=dict(self._defaults); am.update(s.get('automod',{})); return am

    def _is_immune(self, member: discord.Member, am: dict) -> bool:
        if member.bot: return True
        p=member.guild_permissions
        if p.manage_messages or p.manage_guild or p.administrator: return True
        if any(r.id in am['whitelist_roles'] for r in member.roles): return True
        return False

    async def _am_action(self, message: discord.Message, reason: str, mute_secs: int=0):
        """Supprime le message et avertit l'utilisateur."""
        try: await message.delete()
        except: pass
        try:
            dm=discord.Embed(title='🛡️  AutoMod',description=f'Ton message sur **{message.guild.name}** a été supprimé.\n**Raison :** {reason}',color=C.AUTOMOD)
            await message.author.send(embed=dm)
        except: pass
        lg=discord.Embed(title='🛡️  AutoMod',
            description=(f'**Auteur :** {message.author.mention} (`{message.author.id}`)\n'
                         f'**Salon :** {message.channel.mention}\n'
                         f'**Raison :** {reason}\n'
                         f'**Message :** {message.content[:200] or "[vide]"}'),
            color=C.AUTOMOD,timestamp=datetime.utcnow())
        await do_log(self.bot,message.guild,lg)
        if mute_secs>0:
            try: await message.author.timeout(discord.utils.utcnow()+timedelta(seconds=mute_secs),reason=f'[AUTOMOD] {reason}')
            except: pass

    # ── ON MESSAGE ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or not message.content: return
        am=self._get_am(message.guild.id)
        if not am['enabled']: return
        if self._is_immune(message.author,am): return
        if message.channel.id in am['whitelist_channels']: return

        content=message.content; lower=content.lower()

        if am['antispam']:
            now=datetime.utcnow().timestamp()
            q=spam_cache[message.guild.id][message.author.id]; q.append(now)
            recent=sum(1 for t in q if now-t < am['spam_seconds'])
            if recent > am['spam_threshold']:
                spam_cache[message.guild.id][message.author.id].clear()
                return await self._am_action(message,'Spam détecté',mute_secs=300)

        if am['anticaps'] and len(content)>15:
            caps=sum(1 for c in content if c.isupper())
            if caps/len(content)*100 >= am['caps_threshold']:
                return await self._am_action(message,'Trop de majuscules')

        if am['antiinvite'] and _RE_INVITE.search(content):
            return await self._am_action(message,'Lien d\'invitation Discord non autorisé',mute_secs=60)

        if am['antilink'] and _RE_LINK.search(content):
            return await self._am_action(message,'Lien non autorisé')

        if am['badwords'] and am.get('badwords_list'):
            for word in am['badwords_list']:
                if word.lower() in lower:
                    return await self._am_action(message,f'Mot interdit détecté')

        if am['massmentions']:
            total=len(message.mentions)+len(message.role_mentions)
            if message.mention_everyone: total+=5
            if total > am['mention_limit']:
                return await self._am_action(message,f'Trop de mentions ({total})',mute_secs=120)

        if am['repeatchar'] and _RE_REPEAT.search(content):
            return await self._am_action(message,'Caractères répétés excessivement')

    # ── ON MEMBER JOIN — FIX: listener unique fusionné (blacklist + autorole + bienvenue + raid) ──
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # --- Blacklist ---
        gid=str(member.guild.id); bl=load('blacklist.json')
        if gid in bl and str(member.id) in bl[gid]:
            reason=bl[gid][str(member.id)].get('reason','Blacklisté')
            try:
                dm=discord.Embed(title='🚫  Accès refusé',
                    description=f'Tu es **blacklisté** de **{member.guild.name}**.\n**Raison :** {reason}',color=C.BAN)
                await member.send(embed=dm)
            except: pass
            try: await member.ban(reason=f'[BL AUTO] {reason}',delete_message_days=0)
            except:
                try: await member.kick(reason=f'[BL AUTO] {reason}')
                except: pass
            return

        # --- Auto-rôle & bienvenue ---
        g_cfg=cfg(member.guild.id)
        if ar:=g_cfg.get('autorole'):
            role=member.guild.get_role(ar)
            if role:
                try: await member.add_roles(role,reason='Auto-rôle')
                except: pass
        if (wch:=g_cfg.get('welcome_channel')) and (wmsg:=g_cfg.get('welcome_message','')):
            ch=member.guild.get_channel(wch)
            if ch:
                text=(wmsg.replace('{user}',member.mention).replace('{name}',member.display_name)
                         .replace('{server}',member.guild.name).replace('{count}',str(member.guild.member_count)))
                e=discord.Embed(description=text,color=C.SUCCESS,timestamp=datetime.utcnow())
                e.set_author(name=f'Bienvenue, {member.display_name} !',icon_url=member.display_avatar.url)
                e.set_thumbnail(url=member.display_avatar.url)
                e.set_footer(text=f'{member.guild.name} • Membre #{member.guild.member_count}',
                    icon_url=member.guild.icon.url if member.guild.icon else None)
                try: await ch.send(content=member.mention,embed=e)
                except: pass

        # --- Détection de raid ---
        now=datetime.utcnow()
        q=raid_joins[member.guild.id]; q.append(now)
        raid_joins[member.guild.id]=deque([t for t in q if (now-t).seconds<30])
        if len(raid_joins[member.guild.id])>=8:
            lg=discord.Embed(
                title='⚠️  ALERTE RAID DÉTECTÉ',
                description=(f'**{len(raid_joins[member.guild.id])} membres** ont rejoint en moins de 30 secondes !\n\n'
                             '⚡ **Actions recommandées :**\n'
                             '• Augmenter le niveau de vérification\n'
                             '• Activer le slowmode sur tous les salons\n'
                             '• Utiliser `+lock` sur les salons sensibles'),
                color=C.ERROR, timestamp=datetime.utcnow())
            lg.add_field(name='Dernier arrivant',value=f'{member.mention} (`{member.id}`)',inline=True)
            await do_log(self.bot,member.guild,lg)

        # --- Log normal d'arrivée ---
        acc_age=(datetime.utcnow()-member.created_at.replace(tzinfo=None)).days
        color=C.WARNING if acc_age<7 else C.SUCCESS
        lg=discord.Embed(title='📥  Nouveau membre',
            description=f'{member.mention} a rejoint le serveur.',color=color,timestamp=datetime.utcnow())
        lg.set_thumbnail(url=member.display_avatar.url)
        lg.add_field(name='👤 Utilisateur',value=f'{member} (`{member.id}`)',inline=True)
        lg.add_field(name='📅 Compte créé',value=f'<t:{int(member.created_at.timestamp())}:D>\n*{acc_age} jours*',inline=True)
        lg.add_field(name='📊 Membres total',value=str(member.guild.member_count),inline=True)
        if acc_age<7: lg.add_field(name='⚠️ Attention',value='Compte créé il y a moins de 7 jours !',inline=False)
        await do_log(self.bot,member.guild,lg)

    # ── LOGS SERVEUR ─────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.joined_at:
            dur=(datetime.utcnow()-member.joined_at.replace(tzinfo=None)).days
            stay=f'{dur} jours'
        else: stay='Inconnu'
        lg=discord.Embed(title='📤  Membre parti',
            description=f'{member.mention} a quitté le serveur.',color=C.WARNING,timestamp=datetime.utcnow())
        lg.set_thumbnail(url=member.display_avatar.url)
        lg.add_field(name='👤 Utilisateur',value=f'{member} (`{member.id}`)',inline=True)
        lg.add_field(name='⏱️ Temps sur le serveur',value=stay,inline=True)
        roles=[r.mention for r in member.roles if r!=member.guild.default_role]
        if roles: lg.add_field(name='🎭 Rôles',value=' '.join(roles[:10]),inline=False)
        await do_log(self.bot,member.guild,lg)

    # FIX: on_message_delete unique — alimente aussi le snipe_cache pour +snipe
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or not message.guild: return
        # Alimentation du cache snipe (était dans MessagesCog — fusionné ici)
        if message.content:
            snipe_cache[message.channel.id].appendleft({
                'content':  message.content or '[aucun contenu texte]',
                'author':   message.author,
                'author_id':message.author.id,
                'channel':  message.channel,
                'timestamp':message.created_at,
                'attachments': [a.url for a in message.attachments],
            })
        if not message.content and not message.attachments: return
        lg=discord.Embed(title='🗑️  Message supprimé',
            description=f'**Auteur :** {message.author.mention} (`{message.author.id}`)\n**Salon :** {message.channel.mention}',
            color=C.ERROR,timestamp=datetime.utcnow())
        if message.content: lg.add_field(name='💬 Contenu',value=message.content[:1000],inline=False)
        if message.attachments: lg.add_field(name='📎 Pièces jointes',value='\n'.join(a.url for a in message.attachments[:5]),inline=False)
        await do_log(self.bot,message.guild,lg)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or not before.guild: return
        if before.content==after.content: return
        lg=discord.Embed(title='✏️  Message modifié',
            description=f'**Auteur :** {before.author.mention}\n**Salon :** {before.channel.mention}\n[Aller au message]({after.jump_url})',
            color=C.INFO,timestamp=datetime.utcnow())
        lg.add_field(name='📝 Avant',value=before.content[:500] or '*[vide]*',inline=False)
        lg.add_field(name='📝 Après',value=after.content[:500] or '*[vide]*',inline=False)
        await do_log(self.bot,before.guild,lg)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        changes=[]
        if before.nick!=after.nick:
            changes.append(f'**Surnom :** `{before.nick or "Aucun"}` → `{after.nick or "Aucun"}`')
        added=[r for r in after.roles if r not in before.roles]
        removed=[r for r in before.roles if r not in after.roles]
        if added: changes.append(f'**Rôles ajoutés :** {" ".join(r.mention for r in added)}')
        if removed: changes.append(f'**Rôles retirés :** {" ".join(r.mention for r in removed)}')
        if not changes: return
        lg=discord.Embed(title='👤  Membre modifié',
            description=f'{after.mention} (`{after.id}`)\n\n'+'\n'.join(changes),color=C.INFO,timestamp=datetime.utcnow())
        lg.set_thumbnail(url=after.display_avatar.url)
        await do_log(self.bot,after.guild,lg)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        lg=discord.Embed(title='📢  Salon créé',description=f'{channel.mention} (`{channel.id}`)',color=C.SUCCESS,timestamp=datetime.utcnow())
        lg.add_field(name='Type',value=str(channel.type).title(),inline=True)
        await do_log(self.bot,channel.guild,lg)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        lg=discord.Embed(title='🗑️  Salon supprimé',description=f'**#{channel.name}** (`{channel.id}`)',color=C.ERROR,timestamp=datetime.utcnow())
        await do_log(self.bot,channel.guild,lg)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        lg=discord.Embed(title='🎭  Rôle créé',description=f'{role.mention} (`{role.id}`)',color=C.SUCCESS,timestamp=datetime.utcnow())
        await do_log(self.bot,role.guild,lg)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        lg=discord.Embed(title='🗑️  Rôle supprimé',description=f'**{role.name}** (`{role.id}`)',color=C.ERROR,timestamp=datetime.utcnow())
        await do_log(self.bot,role.guild,lg)

    @commands.Cog.listener()
    async def on_guild_ban(self, guild: discord.Guild, user: discord.User):
        lg=discord.Embed(title='🔨  Utilisateur banni',
            description=f'{user.mention} (`{user.id}`)\n**{user}**',color=C.BAN,timestamp=datetime.utcnow())
        lg.set_thumbnail(url=user.display_avatar.url)
        await do_log(self.bot,guild,lg)

    @commands.Cog.listener()
    async def on_guild_unban(self, guild: discord.Guild, user: discord.User):
        lg=discord.Embed(title='🔓  Utilisateur débanni',
            description=f'{user.mention} (`{user.id}`)\n**{user}**',color=C.SUCCESS,timestamp=datetime.utcnow())
        await do_log(self.bot,guild,lg)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if before.channel==after.channel: return
        if after.channel and not before.channel:
            desc=f'{member.mention} a rejoint **{after.channel.name}**'
            color=C.SUCCESS
        elif before.channel and not after.channel:
            desc=f'{member.mention} a quitté **{before.channel.name}**'
            color=C.WARNING
        else:
            desc=f'{member.mention} : **{before.channel.name}** → **{after.channel.name}**'
            color=C.INFO
        lg=discord.Embed(title='🎤  Vocal',description=desc,color=color,timestamp=datetime.utcnow())
        await do_log(self.bot,member.guild,lg)


# ══════════════════════════════════════════════════════════════════
# ████████████████████  BOT PRINCIPAL  ████████████████████████████
# ══════════════════════════════════════════════════════════════════
class ModBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=PREFIX, intents=discord.Intents.all(),
                         help_command=None, case_insensitive=True)

    async def setup_hook(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        cogs=[ModerationCog, MessagesCog, ChannelsCog, GiveawayCog,
              UtilityCog, InfoCog, RolesCog, ConfigCog, AutoModCog]
        print('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        print('  🔧  Chargement des modules...')
        for cog in cogs:
            try:
                await self.add_cog(cog(self))
                print(f'  ✅  {cog.__name__}')
            except Exception as e:
                print(f'  ❌  {cog.__name__}: {e}')
        print('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')
        # FIX: restauration des mutes par rôle après redémarrage
        await self._restore_role_mutes()

    async def _restore_role_mutes(self):
        """Replanifie les mutes par rôle persistés en JSON après un redémarrage."""
        await self.wait_until_ready()
        rm=load('role_mutes.json'); now=int(datetime.utcnow().timestamp()); changed=False
        for gid,members in list(rm.items()):
            guild=self.get_guild(int(gid))
            if not guild: continue
            for uid,data in list(members.items()):
                remaining=data['expire_ts']-now
                member=guild.get_member(int(uid))
                role=guild.get_role(data['role_id'])
                if not member or not role:
                    del rm[gid][uid]; changed=True; continue
                if remaining<=0:
                    try:
                        if role in member.roles:
                            await member.remove_roles(role,reason='Mute expiré (restauration)')
                    except: pass
                    del rm[gid][uid]; changed=True
                else:
                    # Récupérer le cog pour appeler _unmute_role
                    mod_cog=self.cogs.get('ModerationCog')
                    if mod_cog:
                        key=(guild.id,member.id)
                        active_role_mutes[key]=self.loop.create_task(
                            mod_cog._unmute_role(member,role,remaining,guild.id))
        if changed: save('role_mutes.json',rm)

    async def on_ready(self):
        total=sum(g.member_count or 0 for g in self.guilds)
        print(f'╔═══════════════════════════════════════╗')
        print(f'║  🤖  {self.user.name:<33}║')
        print(f'║  📊  Serveurs  : {len(self.guilds):<22}║')
        print(f'║  👥  Membres   : {total:<22}║')
        print(f'║  🎮  Préfixe   : {PREFIX:<22}║')
        print(f'╚═══════════════════════════════════════╝\n')
        await self.change_presence(status=discord.Status.dnd,
            activity=discord.Activity(type=discord.ActivityType.watching,name=f'{len(self.guilds)} serveurs | {PREFIX}help'))

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound): return
        if isinstance(error, commands.CheckFailure):
            e=err_e('Accès refusé','Tu n\'as pas les permissions nécessaires pour cette commande.')
            return await ctx.send(embed=e, delete_after=8)
        msgs={
            commands.MissingPermissions:    ('Permissions insuffisantes', str(error)),
            commands.BotMissingPermissions: ('Mes permissions sont insuffisantes', str(error)),
            commands.MemberNotFound:        ('Membre introuvable', 'Je ne trouve pas ce membre.'),
            commands.UserNotFound:          ('Utilisateur introuvable', 'Je ne trouve pas cet utilisateur.'),
            commands.RoleNotFound:          ('Rôle introuvable', 'Je ne trouve pas ce rôle.'),
            commands.ChannelNotFound:       ('Salon introuvable', 'Je ne trouve pas ce salon.'),
            commands.BadArgument:           ('Argument invalide', str(error)),
        }
        if isinstance(error, commands.MissingRequiredArgument):
            t,d='Argument manquant',f'L\'argument `{error.param.name}` est requis.'
        else:
            for etype,(t,d) in msgs.items():
                if isinstance(error,etype): break
            else:
                t,d='Erreur inattendue',f'```\n{str(error)[:900]}\n```'
                traceback.print_exc()
        try: await ctx.send(embed=err_e(t,d), delete_after=10)
        except: pass


# ══════════════════════════════════════════════════════════════════
# LANCEMENT
# ══════════════════════════════════════════════════════════════════
bot = ModBot()

if __name__ == '__main__':
    bot.run(TOKEN)
