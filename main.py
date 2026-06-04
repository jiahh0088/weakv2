"""
weak - Discord Bot
Bleed-inspired multipurpose bot with full Lavalink v4 music via Wavelink.
Single file, Railway-deployable.
"""

import os
import json
import asyncio
import re
import random
import datetime
import logging
import typing
from collections import defaultdict, deque
from typing import Optional, List, Dict, Any

import discord
from discord.ext import commands, tasks
import aiohttp
import wavelink

# ─── CONFIGURATION ──────────────────────────────────────────────────────────

def load_config() -> dict:
    config = {
        "token": os.getenv("DISCORD_TOKEN", ""),
        "prefix": os.getenv("BOT_PREFIX", "."),
        "owner_ids": list(map(int, os.getenv("OWNER_IDS", "").split(","))) if os.getenv("OWNER_IDS") else [],
        "lavalink_uri": os.getenv("LAVALINK_URI", "https://lavalinkv4.serenetia.com:443"),
        "lavalink_password": os.getenv("LAVALINK_PASSWORD", "https://seretia.link/discord"),
        "log_channel": int(os.getenv("LOG_CHANNEL", "0")) if os.getenv("LOG_CHANNEL") else None,
        "welcome_channel": int(os.getenv("WELCOME_CHANNEL", "0")) if os.getenv("WELCOME_CHANNEL") else None,
        "antinuke_enabled": os.getenv("ANTINUKE_ENABLED", "true").lower() == "true",
        "antinuke_threshold": int(os.getenv("ANTINUKE_THRESHOLD", "3")),
        "antinuke_window": int(os.getenv("ANTINUKE_WINDOW", "10")),
        "automod_enabled": os.getenv("AUTOMOD_ENABLED", "true").lower() == "true",
    }
    try:
        with open("config.json", "r") as f:
            config.update(json.load(f))
    except FileNotFoundError:
        pass
    return config

CONFIG = load_config()

# ─── LOGGING ────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.FileHandler('weak.log'), logging.StreamHandler()]
)
logger = logging.getLogger('weak')

# ─── BOT SETUP ───────────────────────────────────────────────────────────────

intents = discord.Intents.all()
intents.members = True
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix=CONFIG.get("prefix", "."),
    intents=intents,
    help_command=None,
    case_insensitive=True,
    owner_ids=set(CONFIG.get("owner_ids", []))
)

# ─── GLOBAL STATE ───────────────────────────────────────────────────────────

antinuke_cache: Dict[int, Dict[str, deque]] = defaultdict(
    lambda: {
        "bans": deque(maxlen=50),
        "kicks": deque(maxlen=50),
        "role_deletes": deque(maxlen=50),
        "channel_deletes": deque(maxlen=50),
        "webhook_creations": deque(maxlen=50),
    }
)

spam_cache: Dict[int, Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))
warns_db: Dict[int, List[Dict]] = defaultdict(list)
afk_users: Dict[int, str] = {}

# ─── EMBED HELPERS (Bleed-style dark) ────────────────────────────────────────

def weak_embed(title: str = None, description: str = None, color: int = 0x2B2D31) -> discord.Embed:
    embed = discord.Embed(
        title=title, description=description, color=color,
        timestamp=datetime.datetime.utcnow()
    )
    embed.set_footer(text="weak", icon_url=bot.user.display_avatar.url if bot.user else None)
    return embed

def success_embed(description: str) -> discord.Embed:
    return weak_embed(description=description, color=0x57F287)

def error_embed(description: str) -> discord.Embed:
    return weak_embed(description=description, color=0xED4245)

def info_embed(description: str) -> discord.Embed:
    return weak_embed(description=description, color=0x5865F2)

# ─── ANTI-NUKE SYSTEM ───────────────────────────────────────────────────────

class AntiNuke:
    THRESHOLD = CONFIG.get("antinuke_threshold", 3)
    WINDOW = CONFIG.get("antinuke_window", 10)
    
    @staticmethod
    def check_rate(guild_id: int, action: str, user_id: int) -> bool:
        now = datetime.datetime.utcnow().timestamp()
        cache = antinuke_cache[guild_id][action]
        while cache and (now - cache[0]) > AntiNuke.WINDOW:
            cache.popleft()
        cache.append(now)
        return len(cache) >= AntiNuke.THRESHOLD
    
    @staticmethod
    async def punish(guild: discord.Guild, member: discord.Member, reason: str):
        try:
            await guild.ban(member, reason=f"[weak Anti-Nuke] {reason}", delete_message_days=0)
            logger.warning(f"Anti-nuke banned {member} in {guild.name}: {reason}")
        except discord.Forbidden:
            try:
                await guild.kick(member, reason=f"[weak Anti-Nuke] {reason}")
                logger.warning(f"Anti-nuke kicked {member} in {guild.name}: {reason}")
            except discord.Forbidden:
                logger.error(f"Failed to punish {member} in {guild.name}")

# ─── EVENTS ─────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    logger.info(f"weak logged in as {bot.user} (ID: {bot.user.id}) | {len(bot.guilds)} guilds")
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name=f"{len(bot.guilds)} servers | {CONFIG.get('prefix', '.')}help")
    )

@bot.event
async def on_wavelink_node_ready(payload: wavelink.NodeReadyEventPayload):
    logger.info(f"Lavalink node ready: {payload.node.identifier} | Session: {payload.session_id}")

@bot.event
async def on_wavelink_track_end(payload: wavelink.TrackEndEventPayload):
    player = payload.player
    if player.queue:
        next_track = player.queue.get()
        await player.play(next_track)
    else:
        await player.disconnect()

@bot.event
async def on_guild_join(guild: discord.Guild):
    logger.info(f"Joined guild: {guild.name} ({guild.id})")

@bot.event
async def on_member_join(member: discord.Member):
    welcome_id = CONFIG.get("welcome_channel")
    if welcome_id:
        channel = member.guild.get_channel(welcome_id)
        if channel:
            embed = weak_embed(
                title="Welcome",
                description=f"{member.mention} joined the server.\nAccount created: <t:{int(member.created_at.timestamp())}:R>"
            )
            await channel.send(embed=embed)

@bot.event
async def on_member_remove(member: discord.Member):
    welcome_id = CONFIG.get("welcome_channel")
    if welcome_id:
        channel = member.guild.get_channel(welcome_id)
        if channel:
            embed = weak_embed(title="Goodbye", description=f"{member.mention} left the server.")
            await channel.send(embed=embed)

# ─── ANTI-NUKE LISTENERS ─────────────────────────────────────────────────────

@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    if not CONFIG.get("antinuke_enabled", True):
        return
    await asyncio.sleep(1)
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
        if entry.target.id == user.id and entry.user_id != bot.user.id:
            if AntiNuke.check_rate(guild.id, "bans", entry.user_id):
                mod = guild.get_member(entry.user_id)
                if mod:
                    await AntiNuke.punish(guild, mod, "Mass ban detected")

@bot.event
async def on_member_remove(member: discord.Member):
    if not CONFIG.get("antinuke_enabled", True):
        return
    await asyncio.sleep(1)
    async for entry in member.guild.audit_logs(limit=1, action=discord.AuditLogAction.kick):
        if entry.target.id == member.id and entry.user_id != bot.user.id:
            if AntiNuke.check_rate(member.guild.id, "kicks", entry.user_id):
                mod = member.guild.get_member(entry.user_id)
                if mod:
                    await AntiNuke.punish(member.guild, mod, "Mass kick detected")

@bot.event
async def on_guild_role_delete(role: discord.Role):
    if not CONFIG.get("antinuke_enabled", True):
        return
    await asyncio.sleep(1)
    async for entry in role.guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete):
        if entry.user_id != bot.user.id:
            if AntiNuke.check_rate(role.guild.id, "role_deletes", entry.user_id):
                mod = role.guild.get_member(entry.user_id)
                if mod:
                    await AntiNuke.punish(role.guild, mod, "Mass role deletion detected")

@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    if not CONFIG.get("antinuke_enabled", True):
        return
    await asyncio.sleep(1)
    async for entry in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
        if entry.user_id != bot.user.id:
            if AntiNuke.check_rate(channel.guild.id, "channel_deletes", entry.user_id):
                mod = channel.guild.get_member(entry.user_id)
                if mod:
                    await AntiNuke.punish(channel.guild, mod, "Mass channel deletion detected")

# ─── AUTO-MOD ───────────────────────────────────────────────────────────────

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    if not CONFIG.get("automod_enabled", True):
        await bot.process_commands(message)
        return
    
    now = datetime.datetime.utcnow().timestamp()
    
    # Spam detection
    user_spam = spam_cache[message.guild.id][message.author.id]
    user_spam.append(now)
    user_spam[:] = [t for t in user_spam if now - t <= 5]
    if len(user_spam) >= 7:
        try:
            await message.author.timeout(datetime.timedelta(minutes=10), reason="Auto-mod: Spam")
            await message.delete()
            embed = error_embed(f"{message.author.mention} timed out for spamming.")
            await message.channel.send(embed=embed, delete_after=10)
            return
        except discord.Forbidden:
            pass
    
    # Invite filter
    if re.search(r'(discord\.gg|discord\.com\/invite|discordapp\.com\/invite)\/[a-zA-Z0-9]+', message.content):
        if not message.author.guild_permissions.manage_guild:
            try:
                await message.delete()
                embed = error_embed(f"{message.author.mention} Discord invites are not allowed.")
                await message.channel.send(embed=embed, delete_after=5)
                return
            except discord.Forbidden:
                pass
    
    # Mass mention
    if len(message.mentions) + len(message.role_mentions) >= 10:
        try:
            await message.delete()
            embed = error_embed(f"{message.author.mention} Mass mentions are not allowed.")
            await message.channel.send(embed=embed, delete_after=5)
            return
        except discord.Forbidden:
            pass
    
    # AFK check
    if message.author.id in afk_users:
        del afk_users[message.author.id]
        embed = info_embed(f"Welcome back {message.author.mention}, I removed your AFK.")
        await message.channel.send(embed=embed, delete_after=5)
    
    for mention in message.mentions:
        if mention.id in afk_users and mention.id != message.author.id:
            embed = info_embed(f"{mention.mention} is AFK: {afk_users[mention.id]}")
            await message.channel.send(embed=embed, delete_after=10)
    
    await bot.process_commands(message)

# ─── MODERATION COG ─────────────────────────────────────────────────────────

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.command(name="kick", aliases=["k"])
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason"):
        if member.top_role >= ctx.author.top_role:
            return await ctx.send(embed=error_embed("You cannot kick this member."))
        await member.kick(reason=f"By {ctx.author}: {reason}")
        await ctx.send(embed=success_embed(f"Kicked {member.mention}\nReason: {reason}"))
    
    @commands.command(name="ban", aliases=["b"])
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason"):
        if member.top_role >= ctx.author.top_role:
            return await ctx.send(embed=error_embed("You cannot ban this member."))
        await member.ban(reason=f"By {ctx.author}: {reason}", delete_message_days=0)
        await ctx.send(embed=success_embed(f"Banned {member.mention}\nReason: {reason}"))
    
    @commands.command(name="unban", aliases=["ub"])
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, user_id: int):
        user = await self.bot.fetch_user(user_id)
        await ctx.guild.unban(user)
        await ctx.send(embed=success_embed(f"Unbanned {user.mention}"))
    
    @commands.command(name="mute", aliases=["m", "timeout"])
    @commands.has_permissions(moderate_members=True)
    async def mute(self, ctx: commands.Context, member: discord.Member, duration: str, *, reason: str = "No reason"):
        time_map = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}
        match = re.match(r'(\d+)([smhdw])', duration)
        if not match:
            return await ctx.send(embed=error_embed("Invalid format. Use: 1m, 1h, 1d, 1w"))
        val, unit = int(match.group(1)), match.group(2)
        seconds = val * time_map[unit]
        await member.timeout(datetime.timedelta(seconds=seconds), reason=f"By {ctx.author}: {reason}")
        await ctx.send(embed=success_embed(f"Timed out {member.mention} for {duration}\nReason: {reason}"))
    
    @commands.command(name="unmute", aliases=["um"])
    @commands.has_permissions(moderate_members=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member):
        await member.timeout(None)
        await ctx.send(embed=success_embed(f"Removed timeout from {member.mention}"))
    
    @commands.command(name="warn", aliases=["w"])
    @commands.has_permissions(manage_messages=True)
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str):
        warns_db[member.id].append({
            "moderator": ctx.author.id, "reason": reason,
            "timestamp": datetime.datetime.utcnow().isoformat()
        })
        await ctx.send(embed=success_embed(f"Warned {member.mention}\nReason: {reason}\nTotal: {len(warns_db[member.id])}"))
    
    @commands.command(name="warns", aliases=["warnings"])
    @commands.has_permissions(manage_messages=True)
    async def warns(self, ctx: commands.Context, member: discord.Member):
        user_warns = warns_db.get(member.id, [])
        if not user_warns:
            return await ctx.send(embed=info_embed(f"{member.mention} has no warnings."))
        desc = ""
        for i, w in enumerate(user_warns, 1):
            mod = self.bot.get_user(w["moderator"])
            desc += f"**{i}.** {w['reason']} | By {mod.mention if mod else 'Unknown'} | <t:{int(datetime.datetime.fromisoformat(w['timestamp']).timestamp())}:R>\n"
        await ctx.send(embed=weak_embed(title=f"Warnings for {member}", description=desc))
    
    @commands.command(name="clearwarns", aliases=["cw"])
    @commands.has_permissions(manage_messages=True)
    async def clearwarns(self, ctx: commands.Context, member: discord.Member):
        warns_db[member.id] = []
        await ctx.send(embed=success_embed(f"Cleared all warnings for {member.mention}"))
    
    @commands.command(name="purge", aliases=["clear", "c"])
    @commands.has_permissions(manage_messages=True)
    async def purge(self, ctx: commands.Context, amount: int = 10):
        if amount > 1000:
            return await ctx.send(embed=error_embed("Max 1000."))
        deleted = await ctx.channel.purge(limit=amount + 1)
        await ctx.send(embed=success_embed(f"Cleared {len(deleted)} messages."), delete_after=5)
    
    @commands.command(name="slowmode", aliases=["sm"])
    @commands.has_permissions(manage_channels=True)
    async def slowmode(self, ctx: commands.Context, seconds: int):
        await ctx.channel.edit(slowmode_delay=seconds)
        await ctx.send(embed=success_embed(f"Slowmode set to {seconds}s."))
    
    @commands.command(name="lock", aliases=["l"])
    @commands.has_permissions(manage_channels=True)
    async def lock(self, ctx: commands.Context):
        overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        await ctx.send(embed=success_embed(f"Locked {ctx.channel.mention}"))
    
    @commands.command(name="unlock", aliases=["ul"])
    @commands.has_permissions(manage_channels=True)
    async def unlock(self, ctx: commands.Context):
        overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = True
        await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        await ctx.send(embed=success_embed(f"Unlocked {ctx.channel.mention}"))
    
    @commands.command(name="nuke", aliases=["n"])
    @commands.has_permissions(manage_channels=True)
    @commands.is_owner()
    async def nuke(self, ctx: commands.Context):
        new_channel = await ctx.channel.clone()
        await new_channel.edit(position=ctx.channel.position)
        await ctx.channel.delete()
        await new_channel.send(embed=success_embed(f"Nuked and recreated by {ctx.author.mention}"))
    
    @commands.command(name="jail", aliases=["j"])
    @commands.has_permissions(manage_roles=True)
    async def jail(self, ctx: commands.Context, member: discord.Member):
        jail_role = discord.utils.get(ctx.guild.roles, name="jailed")
        if not jail_role:
            jail_role = await ctx.guild.create_role(name="jailed", color=discord.Color.dark_gray())
            for channel in ctx.guild.channels:
                await channel.set_permissions(jail_role, view_channel=False, send_messages=False, connect=False)
        await member.add_roles(jail_role, reason=f"Jailed by {ctx.author}")
        await ctx.send(embed=success_embed(f"Jailed {member.mention}"))
    
    @commands.command(name="unjail", aliases=["uj"])
    @commands.has_permissions(manage_roles=True)
    async def unjail(self, ctx: commands.Context, member: discord.Member):
        jail_role = discord.utils.get(ctx.guild.roles, name="jailed")
        if jail_role:
            await member.remove_roles(jail_role, reason=f"Unjailed by {ctx.author}")
        await ctx.send(embed=success_embed(f"Unjailed {member.mention}"))

# ─── MUSIC COG (Wavelink/Lavalink v4) ───────────────────────────────────────

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    async def get_player(self, ctx: commands.Context) -> Optional[wavelink.Player]:
        if not ctx.voice_client:
            if not ctx.author.voice:
                await ctx.send(embed=error_embed("You are not in a voice channel."))
                return None
            return await ctx.author.voice.channel.connect(cls=wavelink.Player)
        return typing.cast(wavelink.Player, ctx.voice_client)
    
    @commands.command(name="join", aliases=["j", "connect"])
    async def join(self, ctx: commands.Context):
        if not ctx.author.voice:
            return await ctx.send(embed=error_embed("You are not in a voice channel."))
        if ctx.voice_client:
            await ctx.voice_client.move_to(ctx.author.voice.channel)
        else:
            await ctx.author.voice.channel.connect(cls=wavelink.Player)
        await ctx.send(embed=success_embed(f"Joined {ctx.author.voice.channel.mention}"))
    
    @commands.command(name="leave", aliases=["dc", "disconnect", "stop"])
    async def leave(self, ctx: commands.Context):
        if not ctx.voice_client:
            return await ctx.send(embed=error_embed("Not connected."))
        player = typing.cast(wavelink.Player, ctx.voice_client)
        player.queue.clear()
        await player.disconnect()
        await ctx.send(embed=success_embed("Disconnected."))
    
    @commands.command(name="play", aliases=["p"])
    async def play(self, ctx: commands.Context, *, query: str):
        player = await self.get_player(ctx)
        if not player:
            return
        
        if ctx.author.voice.channel.id != player.channel.id:
            return await ctx.send(embed=error_embed("You must be in the same voice channel."))
        
        tracks = await wavelink.Playable.search(query)
        if not tracks:
            return await ctx.send(embed=error_embed("No tracks found."))
        
        track = tracks[0]
        await player.queue.put_wait(track)
        
        if not player.playing:
            next_track = player.queue.get()
            await player.play(next_track)
            embed = success_embed(f"Now playing: [{track.title}]({track.uri})")
            embed.add_field(name="Artist", value=track.author, inline=True)
            embed.add_field(name="Duration", value=f"{track.length // 60000}:{track.length % 60000 // 1000:02d}", inline=True)
            if track.artwork:
                embed.set_thumbnail(url=track.artwork)
            await ctx.send(embed=embed)
        else:
            await ctx.send(embed=info_embed(f"Added to queue: [{track.title}]({track.uri})"))
    
    @commands.command(name="pause")
    async def pause(self, ctx: commands.Context):
        player = typing.cast(wavelink.Player, ctx.voice_client)
        if not player:
            return await ctx.send(embed=error_embed("Not connected."))
        await player.pause(True)
        await ctx.send(embed=success_embed("Paused."))
    
    @commands.command(name="resume")
    async def resume(self, ctx: commands.Context):
        player = typing.cast(wavelink.Player, ctx.voice_client)
        if not player:
            return await ctx.send(embed=error_embed("Not connected."))
        await player.pause(False)
        await ctx.send(embed=success_embed("Resumed."))
    
    @commands.command(name="skip", aliases=["s", "next"])
    async def skip(self, ctx: commands.Context):
        player = typing.cast(wavelink.Player, ctx.voice_client)
        if not player:
            return await ctx.send(embed=error_embed("Not connected."))
        await player.skip()
        await ctx.send(embed=success_embed("Skipped."))
    
    @commands.command(name="queue", aliases=["q"])
    async def queue(self, ctx: commands.Context):
        player = typing.cast(wavelink.Player, ctx.voice_client)
        if not player:
            return await ctx.send(embed=error_embed("Not connected."))
        if not player.queue:
            return await ctx.send(embed=info_embed("Queue is empty."))
        desc = ""
        for i, track in enumerate(player.queue[:20], 1):
            desc += f"**{i}.** [{track.title}]({track.uri}) | {track.length // 60000}:{track.length % 60000 // 1000:02d}\n"
        embed = weak_embed(title="Queue", description=desc)
        embed.add_field(name="Total", value=f"{len(player.queue)} tracks", inline=True)
        if player.current:
            embed.add_field(name="Now Playing", value=f"[{player.current.title}]({player.current.uri})", inline=True)
        await ctx.send(embed=embed)
    
    @commands.command(name="nowplaying", aliases=["np"])
    async def nowplaying(self, ctx: commands.Context):
        player = typing.cast(wavelink.Player, ctx.voice_client)
        if not player or not player.current:
            return await ctx.send(embed=error_embed("Nothing is playing."))
        track = player.current
        embed = weak_embed(title="Now Playing", description=f"[{track.title}]({track.uri})")
        embed.add_field(name="Artist", value=track.author, inline=True)
        embed.add_field(name="Duration", value=f"{track.length // 60000}:{track.length % 60000 // 1000:02d}", inline=True)
        embed.add_field(name="Position", value=f"{player.position // 60000}:{player.position % 60000 // 1000:02d}", inline=True)
        if track.artwork:
            embed.set_thumbnail(url=track.artwork)
        await ctx.send(embed=embed)
    
    @commands.command(name="volume", aliases=["vol"])
    async def volume(self, ctx: commands.Context, vol: int):
        if not 0 <= vol <= 1000:
            return await ctx.send(embed=error_embed("Volume must be 0-1000."))
        player = typing.cast(wavelink.Player, ctx.voice_client)
        if not player:
            return await ctx.send(embed=error_embed("Not connected."))
        await player.set_volume(vol)
        await ctx.send(embed=success_embed(f"Volume set to {vol}%"))
    
    @commands.command(name="seek")
    async def seek(self, ctx: commands.Context, position: str):
        match = re.match(r'(\d+):(\d+)', position)
        if not match:
            return await ctx.send(embed=error_embed("Use format MM:SS"))
        mins, secs = int(match.group(1)), int(match.group(2))
        ms = (mins * 60 + secs) * 1000
        player = typing.cast(wavelink.Player, ctx.voice_client)
        if not player:
            return await ctx.send(embed=error_embed("Not connected."))
        await player.seek(ms)
        await ctx.send(embed=success_embed(f"Seeked to {position}"))
    
    @commands.command(name="shuffle")
    async def shuffle(self, ctx: commands.Context):
        player = typing.cast(wavelink.Player, ctx.voice_client)
        if not player:
            return await ctx.send(embed=error_embed("Not connected."))
        player.queue.shuffle()
        await ctx.send(embed=success_embed("Queue shuffled."))
    
    @commands.command(name="loop", aliases=["repeat"])
    async def loop(self, ctx: commands.Context):
        player = typing.cast(wavelink.Player, ctx.voice_client)
        if not player:
            return await ctx.send(embed=error_embed("Not connected."))
        player.queue.mode = wavelink.QueueMode.loop if player.queue.mode != wavelink.QueueMode.loop else wavelink.QueueMode.normal
        status = "enabled" if player.queue.mode == wavelink.QueueMode.loop else "disabled"
        await ctx.send(embed=success_embed(f"Loop {status}."))

# ─── UTILITY COG ────────────────────────────────────────────────────────────

class Utility(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.command(name="ping", aliases=["latency"])
    async def ping(self, ctx: commands.Context):
        latency = round(self.bot.latency * 1000)
        await ctx.send(embed=info_embed(f"Latency: `{latency}ms`"))
    
    @commands.command(name="userinfo", aliases=["ui", "whois"])
    async def userinfo(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        roles = [r.mention for r in member.roles[1:]]
        roles_str = ", ".join(roles[:10]) or "None"
        if len(roles) > 10:
            roles_str += f" (+{len(roles) - 10} more)"
        embed = weak_embed(title=f"User Info: {member}")
        embed.add_field(name="ID", value=member.id, inline=True)
        embed.add_field(name="Nickname", value=member.nick or "None", inline=True)
        embed.add_field(name="Bot", value="Yes" if member.bot else "No", inline=True)
        embed.add_field(name="Created", value=f"<t:{int(member.created_at.timestamp())}:F>", inline=False)
        embed.add_field(name="Joined", value=f"<t:{int(member.joined_at.timestamp())}:F>", inline=False)
        embed.add_field(name=f"Roles ({len(member.roles) - 1})", value=roles_str, inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)
    
    @commands.command(name="serverinfo", aliases=["si", "guildinfo"])
    async def serverinfo(self, ctx: commands.Context):
        guild = ctx.guild
        embed = weak_embed(title=f"Server Info: {guild.name}")
        embed.add_field(name="ID", value=guild.id, inline=True)
        embed.add_field(name="Owner", value=guild.owner.mention if guild.owner else "Unknown", inline=True)
        embed.add_field(name="Members", value=guild.member_count, inline=True)
        embed.add_field(name="Channels", value=f"{len(guild.text_channels)} Text | {len(guild.voice_channels)} Voice", inline=True)
        embed.add_field(name="Roles", value=len(guild.roles), inline=True)
        embed.add_field(name="Created", value=f"<t:{int(guild.created_at.timestamp())}:F>", inline=False)
        embed.add_field(name="Boost Level", value=guild.premium_tier, inline=True)
        embed.add_field(name="Boosts", value=guild.premium_subscription_count, inline=True)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        await ctx.send(embed=embed)
    
    @commands.command(name="avatar", aliases=["av"])
    async def avatar(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        embed = weak_embed(title=f"{member}'s Avatar")
        embed.set_image(url=member.display_avatar.url)
        await ctx.send(embed=embed)
    
    @commands.command(name="banner")
    async def banner(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        user = await self.bot.fetch_user(member.id)
        if user.banner:
            embed = weak_embed(title=f"{member}'s Banner")
            embed.set_image(url=user.banner.url)
            await ctx.send(embed=embed)
        else:
            await ctx.send(embed=error_embed("No banner."))
    
    @commands.command(name="roleinfo", aliases=["ri"])
    async def roleinfo(self, ctx: commands.Context, role: discord.Role):
        embed = weak_embed(title=f"Role Info: {role.name}")
        embed.add_field(name="ID", value=role.id, inline=True)
        embed.add_field(name="Color", value=str(role.color), inline=True)
        embed.add_field(name="Members", value=len(role.members), inline=True)
        embed.add_field(name="Position", value=role.position, inline=True)
        embed.add_field(name="Mentionable", value="Yes" if role.mentionable else "No", inline=True)
        embed.add_field(name="Hoisted", value="Yes" if role.hoist else "No", inline=True)
        embed.add_field(name="Created", value=f"<t:{int(role.created_at.timestamp())}:F>", inline=False)
        embed.color = role.color
        await ctx.send(embed=embed)
    
    @commands.command(name="membercount", aliases=["mc"])
    async def membercount(self, ctx: commands.Context):
        guild = ctx.guild
        humans = len([m for m in guild.members if not m.bot])
        bots = guild.member_count - humans
        await ctx.send(embed=info_embed(f"Total: `{guild.member_count}` | Humans: `{humans}` | Bots: `{bots}`"))
    
    @commands.command(name="steal", aliases=["addemoji"])
    @commands.has_permissions(manage_emojis=True)
    async def steal(self, ctx: commands.Context, name: str, emoji: str = None):
        if emoji is None and ctx.message.reference:
            ref_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            if ref_msg.attachments:
                image = await ref_msg.attachments[0].read()
            else:
                return await ctx.send(embed=error_embed("No image in referenced message."))
        else:
            if emoji.startswith("<") and emoji.endswith(">"):
                emoji_id = int(emoji.split(":")[-1][:-1])
                emoji_obj = await self.bot.fetch_emoji(emoji_id)
                async with aiohttp.ClientSession() as session:
                    async with session.get(emoji_obj.url) as resp:
                        image = await resp.read()
            else:
                return await ctx.send(embed=error_embed("Provide a valid emoji or reply to an image."))
        try:
            new_emoji = await ctx.guild.create_custom_emoji(name=name, image=image)
            await ctx.send(embed=success_embed(f"Added emoji: {new_emoji}"))
        except discord.HTTPException as e:
            await ctx.send(embed=error_embed(f"Failed: {e.text}"))
    
    @commands.command(name="afk")
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK"):
        afk_users[ctx.author.id] = reason
        await ctx.send(embed=info_embed(f"Set AFK: {reason}"))
    
    @commands.command(name="poll")
    async def poll(self, ctx: commands.Context, *, question: str):
        embed = weak_embed(title="Poll", description=question)
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")
    
    @commands.command(name="remind", aliases=["remindme"])
    async def remind(self, ctx: commands.Context, duration: str, *, reminder: str):
        match = re.match(r'(\d+)([smhd])', duration)
        if not match:
            return await ctx.send(embed=error_embed("Use format: 1m, 1h, 1d"))
        val, unit = int(match.group(1)), match.group(2)
        seconds = val * {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[unit]
        await ctx.send(embed=success_embed(f"I'll remind you in {duration}."))
        await asyncio.sleep(seconds)
        await ctx.send(f"{ctx.author.mention} Reminder: {reminder}")

# ─── FUN COG ────────────────────────────────────────────────────────────────

class Fun(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.command(name="8ball", aliases=["ask"])
    async def eightball(self, ctx: commands.Context, *, question: str):
        responses = [
            "It is certain.", "It is decidedly so.", "Without a doubt.",
            "Yes definitely.", "You may rely on it.", "As I see it, yes.",
            "Most likely.", "Outlook good.", "Yes.", "Signs point to yes.",
            "Reply hazy, try again.", "Ask again later.", "Better not tell you now.",
            "Cannot predict now.", "Concentrate and ask again.",
            "Don't count on it.", "My reply is no.", "My sources say no.",
            "Outlook not so good.", "Very doubtful."
        ]
        await ctx.send(embed=weak_embed(title="Magic 8-Ball", description=f"**Q:** {question}\n**A:** {random.choice(responses)}"))
    
    @commands.command(name="roll", aliases=["dice"])
    async def roll(self, ctx: commands.Context, sides: int = 6):
        await ctx.send(embed=info_embed(f"Rolled **{random.randint(1, sides)}** (1-{sides})"))
    
    @commands.command(name="coinflip", aliases=["cf"])
    async def coinflip(self, ctx: commands.Context):
        await ctx.send(embed=info_embed(f"**{random.choice(['Heads', 'Tails'])}**"))
    
    @commands.command(name="choose", aliases=["pick"])
    async def choose(self, ctx: commands.Context, *, options: str):
        choices = [c.strip() for c in options.split(",")]
        if len(choices) < 2:
            return await ctx.send(embed=error_embed("Provide at least 2 options separated by commas."))
        await ctx.send(embed=info_embed(f"I choose: **{random.choice(choices)}**"))
    
    @commands.command(name="reverse", aliases=["rev"])
    async def reverse(self, ctx: commands.Context, *, text: str):
        await ctx.send(embed=info_embed(text[::-1]))
    
    @commands.command(name="mock", aliases=["spongebob"])
    async def mock(self, ctx: commands.Context, *, text: str):
        result = "".join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(text))
        await ctx.send(embed=info_embed(result))
    
    @commands.command(name="rate")
    async def rate(self, ctx: commands.Context, *, thing: str):
        await ctx.send(embed=info_embed(f"I rate **{thing}** a **{random.randint(0, 10)}/10**"))

# ─── OWNER COG ─────────────────────────────────────────────────────────────

class Owner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.command(name="eval", hidden=True)
    @commands.is_owner()
    async def eval_cmd(self, ctx: commands.Context, *, code: str):
        try:
            result = eval(code)
            if asyncio.iscoroutine(result):
                result = await result
            await ctx.send(embed=success_embed(f"```py\n{result}\n```"))
        except Exception as e:
            await ctx.send(embed=error_embed(f"```py\n{type(e).__name__}: {e}\n```"))
    
    @commands.command(name="shutdown", aliases=["die"], hidden=True)
    @commands.is_owner()
    async def shutdown(self, ctx: commands.Context):
        await ctx.send(embed=info_embed("Shutting down..."))
        await self.bot.close()

# ─── HELP COG ───────────────────────────────────────────────────────────────

class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.command(name="help", aliases=["h", "commands"])
    async def help_cmd(self, ctx: commands.Context, command: str = None):
        if command:
            cmd = self.bot.get_command(command)
            if cmd:
                embed = weak_embed(
                    title=f"Help: {cmd.name}",
                    description=f"**Usage:** `{CONFIG.get('prefix', '.')}{cmd.name} {cmd.signature}`\n"
                               f"**Aliases:** {', '.join(cmd.aliases) or 'None'}\n\n"
                               f"{cmd.help or 'No description.'}"
                )
                return await ctx.send(embed=embed)
            return await ctx.send(embed=error_embed("Command not found."))
        
        embed = weak_embed(title="weak Commands", description="Bleed-inspired multipurpose bot.")
        embed.add_field(
            name="Moderation",
            value="`kick`, `ban`, `unban`, `mute`, `unmute`, `warn`, `warns`, `clearwarns`, `purge`, `slowmode`, `lock`, `unlock`, `nuke`, `jail`, `unjail`",
            inline=False
        )
        embed.add_field(
            name="Music (Lavalink v4)",
            value="`join`, `leave`, `play`, `pause`, `resume`, `skip`, `queue`, `nowplaying`, `volume`, `seek`, `shuffle`, `loop`",
            inline=False
        )
        embed.add_field(
            name="Utility",
            value="`ping`, `userinfo`, `serverinfo`, `avatar`, `banner`, `roleinfo`, `membercount`, `steal`, `afk`, `poll`, `remind`",
            inline=False
        )
        embed.add_field(
            name="Fun",
            value="`8ball`, `roll`, `coinflip`, `choose`, `reverse`, `mock`, `rate`",
            inline=False
        )
        embed.add_field(
            name="System",
            value="Anti-Nuke | Auto-Mod | Welcome/Leave | AFK",
            inline=False
        )
        await ctx.send(embed=embed)

# ─── ERROR HANDLING ─────────────────────────────────────────────────────────

@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        embed = error_embed(f"You need `{error.missing_permissions[0]}` permission.")
    elif isinstance(error, commands.BotMissingPermissions):
        embed = error_embed(f"I need `{error.missing_permissions[0]}` permission.")
    elif isinstance(error, commands.MissingRequiredArgument):
        embed = error_embed(f"Missing argument: `{error.param.name}`")
    elif isinstance(error, commands.BadArgument):
        embed = error_embed("Invalid argument.")
    elif isinstance(error, commands.CommandOnCooldown):
        embed = error_embed(f"Cooldown: `{error.retry_after:.1f}s`.")
    elif isinstance(error, commands.NotOwner):
        embed = error_embed("Owner only.")
    else:
        logger.error(f"Command error in {ctx.command}: {error}")
        embed = error_embed(f"Error: `{str(error)}`")
    await ctx.send(embed=embed)

# ─── SETUP & RUN ────────────────────────────────────────────────────────────

async def setup():
    await bot.add_cog(Moderation(bot))
    await bot.add_cog(Music(bot))
    await bot.add_cog(Utility(bot))
    await bot.add_cog(Fun(bot))
    await bot.add_cog(Owner(bot))
    await bot.add_cog(Help(bot))

    node = wavelink.Node(
        uri=CONFIG.get("lavalink_uri", "https://lavalinkv4.serenetia.com:443"),
        password=CONFIG.get("lavalink_password", "https://seretia.link/discord")
    )
    await wavelink.Pool.connect(nodes=[node], client=bot)

async def main():
    async with bot:
        await setup()
        await bot.start(CONFIG.get("token", ""))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user.")
    except Exception as e:
        logger.error(f"Fatal: {e}")
