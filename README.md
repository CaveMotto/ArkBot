# ArkBot

A Discord bot for tracking ARK: Survival Evolved / Ascended players and servers via the [BattleMetrics](https://www.battlemetrics.com) API.

## Features

- Look up player profiles, sessions, playtime, bans, and identifiers
- Search players and servers on BattleMetrics
- Track players — get alerts when they go online, offline, or change servers
- Track servers — get join/leave logs for every player on a server
- Bulk track players from a `.txt` file
- Export tracked player IDs

## Hosted Instance

A live instance is already running — [click here to add it to your server](https://arkbot.kjhyuga.com). No hosting required.

## Setup

**Requirements:** Python 3.10+

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your tokens:

```
DISCORD_TOKEN=your_discord_bot_token
BM_TOKEN=your_battlemetrics_api_token
```

Then run:

```bash
python main.py
```

The bot syncs slash commands on startup and checks tracked players/servers every 60 seconds.

## Commands

| Category | Commands |
|---|---|
| Lookup | `/check` `/sessions` `/playtime` `/bans` `/identifiers` `/searchplayer` `/searchserver` |
| Server | `/servercheck` `/serverplayers` `/leaderboard` |
| Player Tracking | `/track` `/untrack` `/untrackname` `/trackbulk` `/setplayerchannel` `/moveplayers` |
| Server Tracking | `/servertrack` `/serveruntrack` `/setserverchannel` `/moveservers` |
| Database | `/list` `/findplayer` `/findserver` `/counts` `/trackedonline` `/export` |

## Support

Join the Discord: [discord.gg/6g3XHNbdP6](https://discord.gg/6g3XHNbdP6)  
Contact: **motto08** / **kjhyuga**

## Dependencies

- [discord.py](https://github.com/Rapptz/discord.py) >= 2.4
- [aiohttp](https://docs.aiohttp.org) >= 3.9
- [aiosqlite](https://github.com/omnilib/aiosqlite) >= 0.20
- [python-dotenv](https://github.com/theskumar/python-dotenv) >= 1.0
