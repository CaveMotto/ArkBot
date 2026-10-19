import json
import aiosqlite

DB_FILE = "tracker.db"


async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tracked_players (
                player_id TEXT NOT NULL,
                guild_id  INTEGER NOT NULL,
                name      TEXT,
                channel_id INTEGER,
                last_server TEXT,
                PRIMARY KEY (player_id, guild_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tracked_servers (
                server_id  TEXT NOT NULL,
                guild_id   INTEGER NOT NULL,
                name       TEXT,
                channel_id INTEGER,
                last_players TEXT,
                PRIMARY KEY (server_id, guild_id)
            )
        """)
        await db.commit()
        await _migrate(db)


async def _migrate(db):
    for table, id_col in [("tracked_players", "player_id"), ("tracked_servers", "server_id")]:
        async with db.execute(f"PRAGMA table_info({table})") as c:
            cols = [row[1] for row in await c.fetchall()]
        if "guild_id" not in cols:
            extra = "last_server" if table == "tracked_players" else "last_players"
            await db.execute(f"ALTER TABLE {table} RENAME TO _{table}_old")
            if table == "tracked_players":
                await db.execute("""
                    CREATE TABLE tracked_players (
                        player_id TEXT NOT NULL, guild_id INTEGER NOT NULL,
                        name TEXT, channel_id INTEGER, last_server TEXT,
                        PRIMARY KEY (player_id, guild_id)
                    )
                """)
            else:
                await db.execute("""
                    CREATE TABLE tracked_servers (
                        server_id TEXT NOT NULL, guild_id INTEGER NOT NULL,
                        name TEXT, channel_id INTEGER, last_players TEXT,
                        PRIMARY KEY (server_id, guild_id)
                    )
                """)
            await db.execute(
                f"INSERT INTO {table} ({id_col}, guild_id, name, channel_id, {extra}) "
                f"SELECT {id_col}, 0, name, channel_id, {extra} FROM _{table}_old"
            )
            await db.execute(f"DROP TABLE _{table}_old")
            await db.commit()


async def get_tracked_players(guild_id, query=None):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        if query:
            like = f"%{query}%"
            async with db.execute(
                "SELECT * FROM tracked_players WHERE guild_id=? AND (name LIKE ? OR player_id LIKE ?) ORDER BY name",
                (guild_id, like, like),
            ) as c:
                return await c.fetchall()
        async with db.execute(
            "SELECT * FROM tracked_players WHERE guild_id=? ORDER BY name", (guild_id,)
        ) as c:
            return await c.fetchall()


async def get_tracked_servers(guild_id, query=None):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        if query:
            like = f"%{query}%"
            async with db.execute(
                "SELECT * FROM tracked_servers WHERE guild_id=? AND (name LIKE ? OR server_id LIKE ?) ORDER BY name",
                (guild_id, like, like),
            ) as c:
                return await c.fetchall()
        async with db.execute(
            "SELECT * FROM tracked_servers WHERE guild_id=? ORDER BY name", (guild_id,)
        ) as c:
            return await c.fetchall()


async def get_all_tracked_players():
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tracked_players") as c:
            return await c.fetchall()


async def get_all_tracked_servers():
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tracked_servers") as c:
            return await c.fetchall()


async def upsert_player(player_id, guild_id, name, channel_id, last_server):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT player_id FROM tracked_players WHERE player_id=? AND guild_id=?", (player_id, guild_id)
        ) as c:
            exists = await c.fetchone()
        if exists:
            await db.execute(
                "UPDATE tracked_players SET name=?, channel_id=?, last_server=? WHERE player_id=? AND guild_id=?",
                (name, channel_id, last_server, player_id, guild_id),
            )
        else:
            await db.execute(
                "INSERT INTO tracked_players (player_id, guild_id, name, channel_id, last_server) VALUES (?,?,?,?,?)",
                (player_id, guild_id, name, channel_id, last_server),
            )
        await db.commit()
        return not exists


async def remove_player(player_id, guild_id):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT name FROM tracked_players WHERE player_id=? AND guild_id=?", (player_id, guild_id)
        ) as c:
            row = await c.fetchone()
        if not row:
            return None
        await db.execute("DELETE FROM tracked_players WHERE player_id=? AND guild_id=?", (player_id, guild_id))
        await db.commit()
        return row[0]


async def upsert_server(server_id, guild_id, name, channel_id, last_players):
    lp = json.dumps(last_players) if isinstance(last_players, dict) else (last_players or "{}")
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT server_id FROM tracked_servers WHERE server_id=? AND guild_id=?", (server_id, guild_id)
        ) as c:
            exists = await c.fetchone()
        if exists:
            await db.execute(
                "UPDATE tracked_servers SET name=?, channel_id=?, last_players=? WHERE server_id=? AND guild_id=?",
                (name, channel_id, lp, server_id, guild_id),
            )
        else:
            await db.execute(
                "INSERT INTO tracked_servers (server_id, guild_id, name, channel_id, last_players) VALUES (?,?,?,?,?)",
                (server_id, guild_id, name, channel_id, lp),
            )
        await db.commit()
        return not exists


async def remove_server(server_id, guild_id):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT name FROM tracked_servers WHERE server_id=? AND guild_id=?", (server_id, guild_id)
        ) as c:
            row = await c.fetchone()
        if not row:
            return None
        await db.execute("DELETE FROM tracked_servers WHERE server_id=? AND guild_id=?", (server_id, guild_id))
        await db.commit()
        return row[0]


async def update_player_status(player_id, guild_id, name, server):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE tracked_players SET name=?, last_server=? WHERE player_id=? AND guild_id=?",
            (name, server, player_id, guild_id),
        )
        await db.commit()


async def update_server_players(server_id, guild_id, name, players):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE tracked_servers SET name=?, last_players=? WHERE server_id=? AND guild_id=?",
            (name, json.dumps(players), server_id, guild_id),
        )
        await db.commit()


async def move_player_channels(guild_id, channel_id):
    async with aiosqlite.connect(DB_FILE) as db:
        c = await db.execute(
            "UPDATE tracked_players SET channel_id=? WHERE guild_id=?", (channel_id, guild_id)
        )
        await db.commit()
        return c.rowcount


async def move_server_channels(guild_id, channel_id):
    async with aiosqlite.connect(DB_FILE) as db:
        c = await db.execute(
            "UPDATE tracked_servers SET channel_id=? WHERE guild_id=?", (channel_id, guild_id)
        )
        await db.commit()
        return c.rowcount
