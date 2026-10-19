import json
import asyncio
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from io import BytesIO

import api
import db

C_GREEN  = discord.Color.from_rgb(87, 242, 135)
C_RED    = discord.Color.from_rgb(237, 66, 69)
C_BLUE   = discord.Color.from_rgb(88, 101, 242)
C_GOLD   = discord.Color.from_rgb(254, 231, 92)
C_ORANGE = discord.Color.from_rgb(254, 168, 68)
C_DARK   = discord.Color.from_rgb(47, 49, 54)


def stamp(embed):
    embed.set_footer(text="ArkBot")
    embed.timestamp = api.utcNow()
    return embed


def err(message):
    return stamp(discord.Embed(title="Request Failed", description=message, color=C_RED))


def chunked(embed, name, lines, empty="None"):
    if not lines:
        embed.add_field(name=name, value=empty, inline=False)
        return
    chunk = ""
    first = True
    for line in lines:
        extra = line + "\n"
        if len(chunk) + len(extra) > 1024:
            embed.add_field(name=name if first else "​", value=chunk, inline=False)
            chunk = extra
            first = False
        else:
            chunk += extra
    if chunk:
        embed.add_field(name=name if first else "​", value=chunk, inline=False)


PAGE_SIZE = 10


class SearchView(discord.ui.View):
    def __init__(self, lines, build_embed, total):
        super().__init__(timeout=120)
        self.lines = lines
        self.build_embed = build_embed
        self.total = total
        self.page = 0
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = (self.page + 1) * PAGE_SIZE >= len(self.lines)

    def current_embed(self):
        start = self.page * PAGE_SIZE
        chunk = self.lines[start: start + PAGE_SIZE]
        pages = (len(self.lines) + PAGE_SIZE - 1) // PAGE_SIZE
        embed = self.build_embed(chunk, start)
        embed.set_footer(text=f"ArkBot — page {self.page + 1}/{pages} ({self.total} results)")
        embed.timestamp = api.utcNow()
        return embed

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)


def player_line(row):
    ch = f"<#{row['channel_id']}>" if row["channel_id"] else "no channel"
    status = row["last_server"] or "Offline"
    return (
        f"**{row['name'] or row['player_id']}** "
        f"([{row['player_id']}]({api.playerUrl(row['player_id'])})) "
        f"— {status} — {ch}"
    )


def server_line(row):
    ch = f"<#{row['channel_id']}>" if row["channel_id"] else "no channel"
    return (
        f"**{row['name'] or row['server_id']}** "
        f"([{row['server_id']}]({api.serverUrl(row['server_id'])})) "
        f"— {ch}"
    )


class BotCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tracker.start()

    def cog_unload(self):
        self.tracker.cancel()

    @tasks.loop(seconds=60)
    async def tracker(self):
        players = await db.get_all_tracked_players()
        for row in players:
            try:
                info, _ = await api.getPlayerInfo(row["player_id"])
                if not info:
                    continue
                old, new = row["last_server"], info["current_server"]
                if new == old:
                    continue
                channel = self.bot.get_channel(row["channel_id"])
                if channel:
                    url = api.playerUrl(row["player_id"])
                    if new is None:
                        embed = discord.Embed(title=f"{info['name']} went offline", url=url, color=C_RED)
                        embed.add_field(name="Last Server", value=old or "Unknown")
                    elif old is None:
                        embed = discord.Embed(title=f"{info['name']} came online", url=url, color=C_GREEN)
                        embed.add_field(name="Server", value=new)
                    else:
                        embed = discord.Embed(title=f"{info['name']} changed server", url=url, color=C_ORANGE)
                        embed.add_field(name="From", value=old, inline=True)
                        embed.add_field(name="To", value=new, inline=True)
                    stamp(embed)
                    await channel.send(embed=embed)
                await db.update_player_status(row["player_id"], row["guild_id"], info["name"], new)
            except Exception:
                continue

        async with aiohttp.ClientSession() as session:
            servers = await db.get_all_tracked_servers()
            for row in servers:
                try:
                    info, _ = await api.getServerInfo(session, row["server_id"])
                    if not info or info["status"] != "online":
                        continue
                    old_players = json.loads(row["last_players"] or "{}")
                    new_players = info["player_list"]
                    joined = set(new_players) - set(old_players)
                    left = set(old_players) - set(new_players)
                    channel = self.bot.get_channel(row["channel_id"])
                    if channel and (joined or left):
                        embed = discord.Embed(
                            title=f"Server Log | {info['name']}",
                            url=api.serverUrl(row["server_id"]),
                            color=C_DARK,
                        )
                        if joined:
                            embed.add_field(
                                name=f"Joined ({len(joined)})",
                                value="\n".join(
                                    f"[{new_players[pid]}]({api.playerUrl(pid)})" for pid in joined
                                )[:1024],
                                inline=False,
                            )
                        if left:
                            embed.add_field(
                                name=f"Left ({len(left)})",
                                value="\n".join(
                                    f"[{old_players[pid]}]({api.playerUrl(pid)})" for pid in left
                                )[:1024],
                                inline=False,
                            )
                        stamp(embed)
                        await channel.send(embed=embed)
                    await db.update_server_players(row["server_id"], row["guild_id"], info["name"], new_players)
                except Exception:
                    continue

    @tracker.before_loop
    async def before_tracker(self):
        await self.bot.wait_until_ready()

    # ── Lookup ────────────────────────────────────────────────────────────────

    @app_commands.command(name="check", description="Look up a player profile and recent servers.")
    async def check(self, interaction: discord.Interaction, player_id: str):
        await interaction.response.defer()
        pid = api.extractId(player_id)
        info, error = await api.getPlayerInfo(pid)
        if not info:
            await interaction.followup.send(embed=err(error or "Player not found."))
            return
        embed = discord.Embed(
            title=info["name"],
            url=api.playerUrl(pid),
            description="Online" if info["online"] else "Offline",
            color=C_GREEN if info["online"] else C_DARK,
        )
        embed.add_field(name="First Seen", value=api.fmtFull(info["first_seen"]), inline=True)
        embed.add_field(name="Last Seen",  value=api.fmtRel(info["last_seen"]),  inline=True)
        embed.add_field(name="Player ID",  value=f"`{pid}`",                      inline=True)
        if info["current_server"]:
            sid = info.get("current_server_id")
            val = f"[{info['current_server']}]({api.serverUrl(sid)})" if sid else info["current_server"]
            embed.add_field(name="Current Server", value=val, inline=False)
        if info["recent_servers"]:
            lines = [
                f"[{name}]({api.serverUrl(sid)}) — {api.fmtRel(ts)}"
                for name, ts, sid in info["recent_servers"]
            ]
            embed.add_field(name="Recent Servers", value="\n".join(lines), inline=False)
        stamp(embed)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="sessions", description="Show a player's recent sessions.")
    async def sessions(self, interaction: discord.Interaction, player_id: str):
        await interaction.response.defer()
        pid = api.extractId(player_id)
        async with aiohttp.ClientSession() as session:
            data, error = await api.getPlayerSessions(session, pid)
        if data is None:
            await interaction.followup.send(embed=err(error or "Could not load sessions."))
            return
        embed = discord.Embed(title=f"Sessions | Player {pid}", url=api.playerUrl(pid), color=C_BLUE)
        if not data:
            embed.description = "No session history found."
        else:
            lines = []
            for s in data:
                state = "Live" if s["online"] else "Ended"
                srv = (
                    f"[{s['server_name']}]({api.serverUrl(s['server_id'])})"
                    if s["server_id"] else s["server_name"]
                )
                lines.append(f"{srv}\n{api.fmtRel(s['start'])} — `{api.fmtTime(s['seconds'])}` — {state}")
            chunked(embed, "Recent Sessions", lines)
        stamp(embed)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="playtime", description="Show a player's time on servers.")
    async def playtime(self, interaction: discord.Interaction, player_id: str):
        await interaction.response.defer()
        pid = api.extractId(player_id)
        async with aiohttp.ClientSession() as session:
            data, error = await api.getPlayerPlaytime(session, pid)
        if not data:
            await interaction.followup.send(embed=err(error or "Player not found."))
            return
        embed = discord.Embed(
            title=f"Playtime | {data['name']}",
            url=api.playerUrl(pid),
            description=f"Total tracked: `{api.fmtTime(data['total'])}`",
            color=C_GOLD,
        )
        lines = [
            f"**{i}.** [{e['name']}]({api.serverUrl(e['id'])}) — `{api.fmtTime(e['seconds'])}`"
            + (" — Online" if e.get("online") else "")
            for i, e in enumerate(data["servers"][:10], 1)
        ]
        chunked(embed, "Most Played Servers", lines, "No data.")
        stamp(embed)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="bans", description="Show a player's ban history.")
    async def bans(self, interaction: discord.Interaction, player_id: str):
        await interaction.response.defer()
        pid = api.extractId(player_id)
        async with aiohttp.ClientSession() as session:
            bans, error = await api.getPlayerBans(session, pid)
        if bans is None:
            await interaction.followup.send(embed=err(error or "Could not load bans."))
            return
        active = sum(1 for b in bans if b["active"])
        embed = discord.Embed(
            title=f"Ban History | Player {pid}",
            url=api.playerUrl(pid),
            description=f"Active: **{active}** / Total: **{len(bans)}**",
            color=C_RED if active else C_DARK,
        )
        if not bans:
            embed.description = "No bans found."
        else:
            lines = []
            for b in bans:
                state = "Permanent" if b["permanent"] else ("Active" if b["active"] else "Expired")
                org = f" — {b['org_name']}" if b["org_name"] else ""
                srv = (
                    f"\n  [{b['server_name']}]({api.serverUrl(b['server_id'])})"
                    if b["server_id"] else ""
                )
                lines.append(
                    f"**{state}** {api.fmtRel(b['created'])}{org}\n  {b['reason'][:100]}{srv}"
                )
            chunked(embed, "Bans", lines)
        stamp(embed)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="identifiers", description="Show a player's known names and linked IDs.")
    async def identifiers(self, interaction: discord.Interaction, player_id: str):
        await interaction.response.defer()
        pid = api.extractId(player_id)
        async with aiohttp.ClientSession() as session:
            data, error = await api.getPlayerIdentifiers(session, pid)
        if data is None:
            await interaction.followup.send(embed=err(error or "Could not load identifiers."))
            return
        embed = discord.Embed(
            title=f"Identifiers | Player {pid}",
            url=api.playerUrl(pid),
            description=f"{len(data)} identifier(s) found.",
            color=C_BLUE,
        )
        if not data:
            embed.description = "No identifiers found."
        else:
            by_type: dict = {}
            for item in data:
                by_type.setdefault(item["type"], []).append(item)
            for itype, items in by_type.items():
                lines = [
                    (f"`{i['identifier']}`" if not i["private"] else "`[private]`")
                    + (f" — {api.fmtRel(i['last_seen'])}" if i["last_seen"] else "")
                    for i in items[:15]
                ]
                embed.add_field(name=itype.capitalize(), value="\n".join(lines)[:1024], inline=False)
        stamp(embed)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="searchplayer", description="Search BattleMetrics players by name.")
    async def searchplayer(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer()
        async with aiohttp.ClientSession() as session:
            players, error = await api.searchPlayers(session, name, size=50)
        if players is None:
            await interaction.followup.send(embed=err(error or "Search failed."))
            return
        if not players:
            await interaction.followup.send(embed=err("No players found."))
            return
        lines = [
            f"**{i}.** [{p['name']}]({api.playerUrl(p['id'])}) `{p['id']}`"
            for i, p in enumerate(players, 1)
        ]

        def build(chunk, start):
            embed = discord.Embed(title="Player Search", description=f"Query: `{name}`", color=C_BLUE)
            embed.add_field(name="Results", value="\n".join(chunk), inline=False)
            return embed

        view = SearchView(lines, build, len(players))
        await interaction.followup.send(embed=view.current_embed(), view=view)

    @app_commands.command(name="searchserver", description="Search BattleMetrics servers by name.")
    @app_commands.describe(game="Game filter")
    @app_commands.choices(game=[
        app_commands.Choice(name="ARK: Survival Evolved",  value="ark"),
        app_commands.Choice(name="ARK: Survival Ascended", value="arksa"),
        app_commands.Choice(name="Any game",               value="any"),
    ])
    async def searchserver(
        self,
        interaction: discord.Interaction,
        query: str,
        game: app_commands.Choice[str] = None,
    ):
        await interaction.response.defer()
        async with aiohttp.ClientSession() as session:
            servers, error = await api.searchServers(session, query, game=game.value if game else "ark", size=50)
        if servers is None:
            await interaction.followup.send(embed=err(error or "Search failed."))
            return
        if not servers:
            await interaction.followup.send(embed=err("No servers found."))
            return
        label = game.name if game else "ARK: Survival Evolved"
        lines = [
            f"**{i}.** [{s['name']}]({api.serverUrl(s['id'])})\n"
            f"`{s['id']}` — {'Online' if s['status']=='online' else 'Offline'} — {s['players']}/{s['max_players']}"
            for i, s in enumerate(servers, 1)
        ]

        def build(chunk, start):
            embed = discord.Embed(
                title="Server Search",
                description=f"Query: `{query}` — {label}",
                color=C_BLUE,
            )
            embed.add_field(name="Results", value="\n".join(chunk), inline=False)
            return embed

        view = SearchView(lines, build, len(servers))
        await interaction.followup.send(embed=view.current_embed(), view=view)

    # ── Server ────────────────────────────────────────────────────────────────

    @app_commands.command(name="servercheck", description="Server status, current players, and top playtime.")
    async def servercheck(self, interaction: discord.Interaction, server_id: str):
        await interaction.response.defer()
        sid = api.extractId(server_id)
        async with aiohttp.ClientSession() as session:
            info, error = await api.getServerInfo(session, sid)
            lb, _ = await api.getServerLeaderboard(session, sid, size=10) if info else (None, None)
        if not info:
            await interaction.followup.send(embed=err(error or "Server not found."))
            return
        online = info["status"] == "online"
        embed = discord.Embed(title=info["name"], url=api.serverUrl(sid), color=C_GREEN if online else C_RED)
        embed.add_field(name="Status",  value="Online" if online else "Offline",    inline=True)
        embed.add_field(name="Players", value=f"{info['players']}/{info['max_players']}", inline=True)
        if info.get("rank"):
            embed.add_field(name="Rank", value=f"#{info['rank']}", inline=True)
        if info.get("ip") and info.get("port"):
            embed.add_field(name="Address", value=f"`{info['ip']}:{info['port']}`", inline=True)
        d = info.get("details") or {}
        detail_lines = [
            f"**{k[0].upper()+k[1:]}:** {v}"
            for k in ["map", "gameMode", "official", "modded", "version", "pve", "clusterId"]
            if k in d and d[k] not in (None, "")
        ]
        if detail_lines:
            embed.add_field(name="Details", value="\n".join(detail_lines)[:1024], inline=False)
        if info["player_list"]:
            names = [f"[{n}]({api.playerUrl(p)})" for p, n in list(info["player_list"].items())[:20]]
            extra = f"\n+{len(info['player_list'])-20} more" if len(info["player_list"]) > 20 else ""
            embed.add_field(
                name=f"Online ({len(info['player_list'])})",
                value=("\n".join(names) + extra)[:1024],
                inline=False,
            )
        if lb:
            lines = [
                f"{e['rank']}. [{e['name']}]({api.playerUrl(e['id'])}) — `{api.fmtTime(e['seconds'])}`"
                for e in lb
            ]
            embed.add_field(name="Top Playtime", value="\n".join(lines)[:1024], inline=False)
        stamp(embed)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="serverplayers", description="List players currently online on a server.")
    async def serverplayers(self, interaction: discord.Interaction, server_id: str):
        await interaction.response.defer()
        sid = api.extractId(server_id)
        async with aiohttp.ClientSession() as session:
            info, error = await api.getServerInfo(session, sid)
        if not info:
            await interaction.followup.send(embed=err(error or "Server not found."))
            return
        embed = discord.Embed(
            title=info["name"],
            url=api.serverUrl(sid),
            description=f"{info['players']}/{info['max_players']} online",
            color=C_GREEN if info["status"] == "online" else C_RED,
        )
        lines = [f"[{n}]({api.playerUrl(p)}) `{p}`" for p, n in info["player_list"].items()]
        chunked(embed, "Players", lines, "No players listed.")
        stamp(embed)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="leaderboard", description="Most time played on a server.")
    @app_commands.describe(period="Time range")
    @app_commands.choices(period=[
        app_commands.Choice(name="All Time",      value="AT"),
        app_commands.Choice(name="Last 30 Days",  value="30D"),
    ])
    async def leaderboard(
        self,
        interaction: discord.Interaction,
        server_id: str,
        period: app_commands.Choice[str] = None,
    ):
        await interaction.response.defer()
        from datetime import timedelta
        sid = api.extractId(server_id)
        p_val = period.value if period else "AT"
        if p_val == "30D":
            end = api.utcNow()
            start = end - timedelta(days=30)
            filter_p = f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')}:{end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            label = "Last 30 Days"
        else:
            filter_p = "AT"
            label = "All Time"
        async with aiohttp.ClientSession() as session:
            server_info, _ = await api.getServerInfo(session, sid)
            entries, error = await api.getServerLeaderboard(session, sid, period=filter_p, size=15)
        if entries is None:
            await interaction.followup.send(embed=err(error or "Leaderboard unavailable."))
            return
        name = server_info["name"] if server_info else f"Server {sid}"
        embed = discord.Embed(title=f"Leaderboard | {name}", url=api.serverUrl(sid), color=C_GOLD)
        embed.description = "\n".join(
            f"**{e['rank']}.** [{e['name']}]({api.playerUrl(e['id'])}) — `{api.fmtTime(e['seconds'])}`"
            for e in entries
        )
        stamp(embed)
        embed.set_footer(text=f"ArkBot • {label}")
        await interaction.followup.send(embed=embed)

    # ── Player Tracking ───────────────────────────────────────────────────────

    @app_commands.command(name="track", description="Track a player. Alerts go to the selected channel.")
    async def track(self, interaction: discord.Interaction, player_id: str, channel: discord.TextChannel):
        await interaction.response.defer()
        pid = api.extractId(player_id)
        info, error = await api.getPlayerInfo(pid)
        if not info:
            await interaction.followup.send(embed=err(error or "Player not found."))
            return
        added = await db.upsert_player(pid, interaction.guild_id, info["name"], channel.id, info["current_server"])
        embed = discord.Embed(
            title="Now Tracking" if added else "Tracking Updated",
            description=f"**{info['name']}**",
            url=api.playerUrl(pid),
            color=C_GREEN,
        )
        embed.add_field(name="Status",    value=info["current_server"] or "Offline", inline=True)
        embed.add_field(name="Channel",   value=channel.mention,                     inline=True)
        embed.add_field(name="Player ID", value=f"`{pid}`",                          inline=True)
        stamp(embed)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="untrack", description="Stop tracking a player by BattleMetrics ID.")
    async def untrack(self, interaction: discord.Interaction, player_id: str):
        await interaction.response.defer()
        pid = api.extractId(player_id)
        name = await db.remove_player(pid, interaction.guild_id)
        if name is None:
            await interaction.followup.send(embed=err("That player is not tracked here."))
            return
        embed = stamp(discord.Embed(
            title="Tracking Stopped",
            description=f"Stopped tracking **{name}**.",
            color=C_RED,
        ))
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="untrackname", description="Stop tracking a player by stored name.")
    async def untrackname(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer()
        rows = await db.get_tracked_players(interaction.guild_id, name)
        if not rows:
            await interaction.followup.send(embed=err("No tracked player matched that name."))
            return
        if len(rows) > 1:
            embed = discord.Embed(
                title="Multiple Matches",
                description="Use `/untrack` with the exact ID.",
                color=C_ORANGE,
            )
            chunked(embed, "Matches", [player_line(r) for r in rows])
            stamp(embed)
            await interaction.followup.send(embed=embed)
            return
        row = rows[0]
        await db.remove_player(row["player_id"], interaction.guild_id)
        embed = stamp(discord.Embed(
            title="Tracking Stopped",
            description=f"Stopped tracking **{row['name']}** (`{row['player_id']}`).",
            color=C_RED,
        ))
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="trackbulk", description="Bulk track players from a .txt file (one ID per line).")
    async def trackbulk(self, interaction: discord.Interaction, file: discord.Attachment, channel: discord.TextChannel):
        await interaction.response.defer()
        if not file.filename.endswith(".txt"):
            await interaction.followup.send(embed=err("Upload a .txt file only."))
            return
        text = (await file.read()).decode("utf-8", errors="ignore")
        ids = [
            api.extractId(line.strip())
            for line in text.splitlines()
            if api.extractId(line.strip()).isdigit()
        ]
        if not ids:
            await interaction.followup.send(embed=err("No valid BattleMetrics IDs found."))
            return
        added = updated = failed = 0
        for pid in ids:
            await asyncio.sleep(0.4)
            info, _ = await api.getPlayerInfo(pid)
            if not info:
                failed += 1
                continue
            was_new = await db.upsert_player(pid, interaction.guild_id, info["name"], channel.id, info["current_server"])
            if was_new:
                added += 1
            else:
                updated += 1
        embed = discord.Embed(
            title="Bulk Tracking Complete",
            color=C_GREEN if added or updated else C_ORANGE,
        )
        embed.add_field(name="Added",   value=str(added),   inline=True)
        embed.add_field(name="Updated", value=str(updated), inline=True)
        embed.add_field(name="Failed",  value=str(failed),  inline=True)
        embed.add_field(name="Channel", value=channel.mention, inline=False)
        stamp(embed)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="setplayerchannel", description="Change the alert channel for a tracked player.")
    async def setplayerchannel(self, interaction: discord.Interaction, player_id: str, channel: discord.TextChannel):
        await interaction.response.defer()
        pid = api.extractId(player_id)
        rows = await db.get_tracked_players(interaction.guild_id)
        row = next((r for r in rows if r["player_id"] == pid), None)
        if not row:
            await interaction.followup.send(embed=err("That player is not tracked here."))
            return
        await db.upsert_player(pid, interaction.guild_id, row["name"], channel.id, row["last_server"])
        embed = stamp(discord.Embed(
            title="Channel Updated",
            description=f"**{row['name']}** alerts will go to {channel.mention}.",
            color=C_GREEN,
        ))
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="moveplayers", description="Move all player alerts to one channel.")
    async def moveplayers(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer()
        count = await db.move_player_channels(interaction.guild_id, channel.id)
        embed = stamp(discord.Embed(
            title="Player Channels Updated",
            description=f"Moved **{count}** player alert(s) to {channel.mention}.",
            color=C_GREEN,
        ))
        await interaction.followup.send(embed=embed)

    # ── Server Tracking ───────────────────────────────────────────────────────

    @app_commands.command(name="servertrack", description="Track a server. Join/leave logs go to the selected channel.")
    async def servertrack(self, interaction: discord.Interaction, server_id: str, channel: discord.TextChannel):
        await interaction.response.defer()
        sid = api.extractId(server_id)
        async with aiohttp.ClientSession() as session:
            info, error = await api.getServerInfo(session, sid)
        if not info:
            await interaction.followup.send(embed=err(error or "Server not found."))
            return
        added = await db.upsert_server(sid, interaction.guild_id, info["name"], channel.id, info["player_list"])
        embed = discord.Embed(
            title="Now Tracking Server" if added else "Server Tracking Updated",
            description=f"**{info['name']}**",
            url=api.serverUrl(sid),
            color=C_GREEN,
        )
        embed.add_field(name="Players",   value=f"{info['players']}/{info['max_players']}", inline=True)
        embed.add_field(name="Channel",   value=channel.mention,                            inline=True)
        embed.add_field(name="Server ID", value=f"`{sid}`",                                 inline=True)
        stamp(embed)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="serveruntrack", description="Stop tracking a server.")
    async def serveruntrack(self, interaction: discord.Interaction, server_id: str):
        await interaction.response.defer()
        sid = api.extractId(server_id)
        name = await db.remove_server(sid, interaction.guild_id)
        if name is None:
            await interaction.followup.send(embed=err("That server is not tracked here."))
            return
        embed = stamp(discord.Embed(
            title="Tracking Stopped",
            description=f"Stopped tracking **{name}**.",
            color=C_RED,
        ))
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="setserverchannel", description="Change the log channel for a tracked server.")
    async def setserverchannel(self, interaction: discord.Interaction, server_id: str, channel: discord.TextChannel):
        await interaction.response.defer()
        sid = api.extractId(server_id)
        rows = await db.get_tracked_servers(interaction.guild_id)
        row = next((r for r in rows if r["server_id"] == sid), None)
        if not row:
            await interaction.followup.send(embed=err("That server is not tracked here."))
            return
        await db.upsert_server(sid, interaction.guild_id, row["name"], channel.id, row["last_players"])
        embed = stamp(discord.Embed(
            title="Channel Updated",
            description=f"**{row['name']}** logs will go to {channel.mention}.",
            color=C_GREEN,
        ))
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="moveservers", description="Move all server logs to one channel.")
    async def moveservers(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer()
        count = await db.move_server_channels(interaction.guild_id, channel.id)
        embed = stamp(discord.Embed(
            title="Server Channels Updated",
            description=f"Moved **{count}** server log(s) to {channel.mention}.",
            color=C_GREEN,
        ))
        await interaction.followup.send(embed=embed)

    # ── Database ──────────────────────────────────────────────────────────────

    @app_commands.command(name="list", description="List all tracked players and servers.")
    async def list_tracked(self, interaction: discord.Interaction):
        await interaction.response.defer()
        players = await db.get_tracked_players(interaction.guild_id)
        servers = await db.get_tracked_servers(interaction.guild_id)
        embed = discord.Embed(title="Tracked Entries", color=C_BLUE)
        chunked(embed, "Players", [player_line(p) for p in players], "None.")
        chunked(embed, "Servers", [server_line(s) for s in servers], "None.")
        stamp(embed)
        embed.set_footer(text=f"{len(players)} player(s) • {len(servers)} server(s)")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="findplayer", description="Search tracked players by name or ID.")
    async def findplayer(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        rows = await db.get_tracked_players(interaction.guild_id, query)
        embed = discord.Embed(title="Player Search", description=f"Query: `{query}`", color=C_BLUE)
        chunked(embed, "Results", [player_line(r) for r in rows], "No matches.")
        stamp(embed)
        embed.set_footer(text=f"{len(rows)} match(es)")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="findserver", description="Search tracked servers by name or ID.")
    async def findserver(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        rows = await db.get_tracked_servers(interaction.guild_id, query)
        embed = discord.Embed(title="Server Search", description=f"Query: `{query}`", color=C_BLUE)
        chunked(embed, "Results", [server_line(r) for r in rows], "No matches.")
        stamp(embed)
        embed.set_footer(text=f"{len(rows)} match(es)")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="counts", description="Show tracking totals.")
    async def counts(self, interaction: discord.Interaction):
        await interaction.response.defer()
        players = await db.get_tracked_players(interaction.guild_id)
        servers = await db.get_tracked_servers(interaction.guild_id)
        embed = discord.Embed(title="Tracking Counts", color=C_BLUE)
        embed.add_field(name="Players", value=str(len(players)), inline=True)
        embed.add_field(name="Servers", value=str(len(servers)), inline=True)
        stamp(embed)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="trackedonline", description="Show which tracked players are currently online.")
    async def trackedonline(self, interaction: discord.Interaction):
        await interaction.response.defer()
        players = await db.get_tracked_players(interaction.guild_id)
        if not players:
            await interaction.followup.send(embed=err("No players are tracked here."))
            return
        online = [
            f"**{r['name'] or r['player_id']}** — {r['last_server']}"
            for r in players if r["last_server"]
        ]
        offline_count = sum(1 for r in players if not r["last_server"])
        embed = discord.Embed(title="Tracked Player Status", color=C_BLUE)
        chunked(embed, f"Online ({len(online)})", online, "None online.")
        embed.add_field(name="Offline", value=str(offline_count), inline=True)
        stamp(embed)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="export", description="Export tracked player IDs as a text file.")
    async def export(self, interaction: discord.Interaction):
        await interaction.response.defer()
        players = await db.get_tracked_players(interaction.guild_id)
        if not players:
            await interaction.followup.send(embed=err("No players are tracked here."))
            return
        lines = [f"{p['player_id']} {p['name'] or ''}".rstrip() for p in players]
        payload = BytesIO("\n".join(lines).encode())
        embed = stamp(discord.Embed(
            title="Export Ready",
            description=f"Exported **{len(players)}** player ID(s).",
            color=C_BLUE,
        ))
        await interaction.followup.send(embed=embed, file=discord.File(payload, "tracked_players.txt"))

    # ── Help ──────────────────────────────────────────────────────────────────

    @app_commands.command(name="help", description="List all commands.")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(title="ArkBot Commands", color=C_BLUE)
        embed.add_field(name="Lookup", value=(
            "`/check` `/sessions` `/playtime`\n"
            "`/bans` `/identifiers`\n"
            "`/searchplayer` `/searchserver`"
        ), inline=False)
        embed.add_field(name="Server", value=(
            "`/servercheck` `/serverplayers` `/leaderboard`"
        ), inline=False)
        embed.add_field(name="Player Tracking", value=(
            "`/track` `/untrack` `/untrackname` `/trackbulk`\n"
            "`/setplayerchannel` `/moveplayers`"
        ), inline=False)
        embed.add_field(name="Server Tracking", value=(
            "`/servertrack` `/serveruntrack`\n"
            "`/setserverchannel` `/moveservers`"
        ), inline=False)
        embed.add_field(name="Database", value=(
            "`/list` `/findplayer` `/findserver` `/counts`\n"
            "`/trackedonline` `/export`"
        ), inline=False)
        stamp(embed)
        await interaction.response.send_message(embed=embed)
