import os
import re
import asyncio
import aiohttp
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

_HEADERS = {"Authorization": f"Bearer {os.getenv('BM_TOKEN')}"}
_BASE = "https://api.battlemetrics.com"
_WEB = "https://www.battlemetrics.com"
_ID_RE = re.compile(r"/(?:players|servers|bans)/(\d+)")
_HTTP_ERRORS = {
    401: "Invalid API token.",
    403: "API subscription required.",
    404: "Not found on BattleMetrics.",
    429: "Rate limited. Try again shortly.",
}


def playerUrl(pid):
    return f"{_WEB}/players/{pid}"


def serverUrl(sid):
    return f"{_WEB}/servers/{sid}"


def utcNow():
    return datetime.now(timezone.utc)


def extractId(raw):
    text = str(raw or "").strip()
    m = _ID_RE.search(text)
    return m.group(1) if m else text


def parseIso(s):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fmtRel(dt):
    if isinstance(dt, str):
        dt = parseIso(dt)
    return f"<t:{int(dt.timestamp())}:R>" if dt else "Unknown"


def fmtFull(dt):
    if isinstance(dt, str):
        dt = parseIso(dt)
    return f"<t:{int(dt.timestamp())}:f>" if dt else "Unknown"


def fmtTime(seconds):
    t = max(0, int(seconds or 0))
    d, r = divmod(t, 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    parts = []
    if d:
        parts.append(f"{d}d")
    parts.append(f"{h}h {m}m")
    return " ".join(parts)


class _Result:
    def __init__(self, status, data=None, error=None):
        self.status = status
        self.data = data
        self.error = error

    @property
    def ok(self):
        return self.status == 200 and self.data is not None


async def _fetch(session, path, params=None):
    url = f"{_BASE}{path}" if path.startswith("/") else path
    try:
        async with session.get(url, headers=_HEADERS, params=params,
                               timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status == 200:
                try:
                    return _Result(200, await r.json())
                except Exception:
                    return _Result(200, None, "Invalid JSON.")
            return _Result(r.status, None, _HTTP_ERRORS.get(r.status, f"HTTP {r.status}."))
    except asyncio.TimeoutError:
        return _Result(0, None, "BattleMetrics timed out.")
    except Exception:
        return _Result(0, None, "Could not reach BattleMetrics.")



async def getServerInfo(session, server_id):
    r = await _fetch(session, f"/servers/{server_id}?include=player")
    if not r.ok:
        return None, r.error
    d = r.data.get("data", {})
    attrs = d.get("attributes", {})
    players = {
        inc["id"]: (inc.get("attributes") or {}).get("name", "?")
        for inc in r.data.get("included", [])
        if inc.get("type") == "player"
    }
    org_id = (((d.get("relationships") or {}).get("organization") or {}).get("data") or {}).get("id")
    return {
        "name": attrs.get("name", "Unknown"),
        "status": attrs.get("status", "offline"),
        "players": attrs.get("players", 0),
        "max_players": attrs.get("maxPlayers", 0),
        "player_list": players,
        "ip": attrs.get("ip"),
        "port": attrs.get("port"),
        "rank": attrs.get("rank"),
        "details": attrs.get("details") or {},
        "org_id": org_id,
    }, None


async def getServerLeaderboard(session, server_id, period="AT", size=10):
    r = await _fetch(session, f"/servers/{server_id}/relationships/leaderboards/time",
                   params={"filter[period]": period, "page[size]": str(size)})
    if not r.ok:
        return None, r.error
    return [
        {
            "id": item["id"],
            "rank": item["attributes"]["rank"],
            "name": item["attributes"].get("name", "?"),
            "seconds": item["attributes"].get("value", 0),
        }
        for item in r.data.get("data", [])
    ], None


async def searchPlayers(session, query, size=10):
    r = await _fetch(session, "/players", params={"filter[search]": query, "page[size]": str(size)})
    if not r.ok:
        return None, r.error
    return [
        {"id": item["id"], "name": (item.get("attributes") or {}).get("name", "?")}
        for item in r.data.get("data", [])
    ], None


async def searchServers(session, query, game="ark", size=10):
    params = {"filter[search]": query, "page[size]": str(size), "sort": "-players"}
    if game and game != "any":
        params["filter[game]"] = game
    r = await _fetch(session, "/servers", params=params)
    if not r.ok:
        return None, r.error
    return [
        {
            "id": item["id"],
            "name": (item.get("attributes") or {}).get("name", "?"),
            "status": (item.get("attributes") or {}).get("status", "offline"),
            "players": (item.get("attributes") or {}).get("players", 0),
            "max_players": (item.get("attributes") or {}).get("maxPlayers", 0),
        }
        for item in r.data.get("data", [])
    ], None


async def getPlayerSessions(session, player_id, size=10):
    r = await _fetch(session, f"/players/{player_id}/relationships/sessions",
                   params={"include": "server", "page[size]": str(size)})
    if not r.ok:
        return None, r.error
    names = {
        inc["id"]: (inc.get("attributes") or {}).get("name", "?")
        for inc in r.data.get("included", [])
        if inc.get("type") == "server"
    }
    sessions = []
    for item in r.data.get("data", []):
        attrs = item.get("attributes") or {}
        sid = (((item.get("relationships") or {}).get("server") or {}).get("data") or {}).get("id")
        start = parseIso(attrs.get("start"))
        stop = parseIso(attrs.get("stop"))
        secs = attrs.get("duration")
        if secs is None and start:
            secs = max(0, int(((stop or utcNow()) - start).total_seconds()))
        sessions.append({
            "server_id": sid,
            "server_name": names.get(sid, "Unknown") if sid else "Unknown",
            "start": start,
            "seconds": secs or 0,
            "online": attrs.get("stop") is None,
        })
    return sessions, None


async def getPlayerPlaytime(session, player_id):
    r = await _fetch(session, f"/players/{player_id}", params={"include": "server"})
    if not r.ok:
        return None, r.error
    d = r.data.get("data", {}) or {}
    names = {
        inc["id"]: (inc.get("attributes") or {}).get("name", "?")
        for inc in r.data.get("included", [])
        if inc.get("type") == "server"
    }
    rels = ((d.get("relationships") or {}).get("servers") or {}).get("data") or []
    entries = [
        {
            "id": rel["id"],
            "name": names.get(rel["id"], f"Server {rel['id']}"),
            "seconds": (rel.get("meta") or {}).get("timePlayed") or 0,
            "online": (rel.get("meta") or {}).get("online", False),
        }
        for rel in rels
    ]
    entries.sort(key=lambda e: e["seconds"], reverse=True)
    return {
        "name": (d.get("attributes") or {}).get("name", "?"),
        "servers": entries,
        "total": sum(e["seconds"] for e in entries),
    }, None


async def getPlayerBans(session, player_id, size=10):
    r = await _fetch(session, "/bans",
                   params={"filter[player]": player_id, "include": "server,organization",
                           "page[size]": str(size)})
    if not r.ok:
        return None, r.error
    server_names = {}
    org_names = {}
    for inc in r.data.get("included", []):
        if inc.get("type") == "server":
            server_names[inc["id"]] = (inc.get("attributes") or {}).get("name", "?")
        elif inc.get("type") == "organization":
            org_names[inc["id"]] = (inc.get("attributes") or {}).get("name", "?")
    bans = []
    for item in r.data.get("data", []):
        attrs = item.get("attributes") or {}
        rels = item.get("relationships") or {}
        sid = ((rels.get("server") or {}).get("data") or {}).get("id")
        oid = ((rels.get("organization") or {}).get("data") or {}).get("id")
        expires = parseIso(attrs.get("expires"))
        bans.append({
            "id": item["id"],
            "reason": attrs.get("reason") or "No reason given",
            "permanent": attrs.get("permanent", False),
            "expires": expires,
            "created": parseIso(attrs.get("timestamp")),
            "server_id": sid,
            "server_name": server_names.get(sid) if sid else None,
            "org_name": org_names.get(oid) if oid else None,
            "active": bool(attrs.get("permanent")) or not expires or expires > utcNow(),
        })
    return bans, None



async def getPlayerIdentifiers(session, player_id):
    r = await _fetch(session, f"/players/{player_id}/relationships/identifiers",
                   params={"include": "identifier"})
    if not r.ok:
        return None, r.error
    idents = []
    for inc in r.data.get("included", []):
        if inc.get("type") != "identifier":
            continue
        attrs = inc.get("attributes") or {}
        idents.append({
            "type": attrs.get("type", "unknown"),
            "identifier": attrs.get("identifier", ""),
            "last_seen": parseIso(attrs.get("lastSeen")),
            "private": attrs.get("private", False),
        })
    idents.sort(
        key=lambda x: x["last_seen"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return idents, None


async def getPlayerInfo(player_id):
    async with aiohttp.ClientSession() as session:
        pr = await _fetch(session, f"/players/{player_id}")
        if not pr.ok:
            return None, pr.error
        sr = await _fetch(session, f"/players/{player_id}/relationships/sessions",
                        params={"page[size]": "20"})
        pattrs = ((pr.data.get("data") or {}).get("attributes") or {})
        name = pattrs.get("name", "?")
        first_seen = parseIso(pattrs.get("createdAt"))
        last_seen = None
        current_server_id = None
        history = []
        seen_ids = set()
        for sess in (sr.data or {}).get("data", []) if sr.ok else []:
            attrs = sess.get("attributes") or {}
            sid = (((sess.get("relationships") or {}).get("server") or {}).get("data") or {}).get("id")
            start = parseIso(attrs.get("start"))
            stop = parseIso(attrs.get("stop"))
            if start:
                first_seen = min(first_seen, start) if first_seen else start
                marker = stop or start
                last_seen = max(last_seen, marker) if last_seen else marker
            if attrs.get("stop") is None and sid:
                current_server_id = sid
            elif sid and sid not in seen_ids and sid != current_server_id:
                seen_ids.add(sid)
                history.append((sid, start))
        all_ids = ([current_server_id] if current_server_id else []) + [sid for sid, _ in history[:5]]
        name_map = {}
        for sid in all_ids:
            r2 = await _fetch(session, f"/servers/{sid}")
            if r2.ok:
                name_map[sid] = ((r2.data.get("data") or {}).get("attributes") or {}).get("name")
        recent = [
            (name_map.get(sid), ts, sid)
            for sid, ts in history[:5]
            if name_map.get(sid) and name_map.get(sid) != name_map.get(current_server_id)
        ]
    return {
        "name": name,
        "first_seen": first_seen.isoformat() if first_seen else None,
        "last_seen": last_seen.isoformat() if last_seen else None,
        "current_server": name_map.get(current_server_id),
        "current_server_id": current_server_id,
        "recent_servers": recent,
        "online": current_server_id is not None,
    }, None


