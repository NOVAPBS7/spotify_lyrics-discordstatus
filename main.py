import asyncio
import logging
import re
import time
from typing import Optional

import aiohttp
import winrt.windows.media.control as wmc

DISCORD_TOKEN  = "YOUR_DISCORD_USER_TOKEN"
MAX_STATUS_LEN = 100
TIME_OFFSET    = 1.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("spotify-discord")

lyrics_cache: dict[str, list[tuple[float, str]]] = {}

async def get_smtc_state() -> Optional[dict]:
    try:
        manager = await wmc.GlobalSystemMediaTransportControlsSessionManager.request_async()

        # look for Spotify session
        sp_session = None
        for session in manager.get_sessions():
            app_id = (session.source_app_user_model_id or "").lower()
            if "spotify" in app_id:
                sp_session = session
                break

        if sp_session is None:
            return None  # spotify not running

        if sp_session is None:
            return None

        info     = await sp_session.try_get_media_properties_async()
        timeline = sp_session.get_timeline_properties()
        status   = int(sp_session.get_playback_info().playback_status)
        playing  = status == 4  # 4=Playing, 5=Paused

        title = info.title or ""
        if not title:
            return None

        return {
            "artist":   info.artist or "",
            "title":    title,
            "position": timeline.position.total_seconds(),
            "playing":  playing,
        }
    except Exception as e:
        log.warning(f"SMTC: {e}")
        return None

def parse_lrc(synced: str) -> list[tuple[float, str]]:
    lines = []
    for line in synced.splitlines():
        m = re.match(r'\[(\d+):(\d+\.\d+)\]\s*(.*)', line)
        if not m:
            continue
        t = int(m.group(1)) * 60 + float(m.group(2))
        text = m.group(3).strip()
        if text:
            lines.append((t, text))
    return lines

async def search_lrclib(q: str) -> list[tuple[float, str]]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://lrclib.net/api/search",
                params={"q": q},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status != 200:
                    return []
                results = await r.json()
        for item in results:
            synced = item.get("syncedLyrics", "")
            if synced:
                lines = parse_lrc(synced)
                if lines:
                    return lines
    except Exception as e:
        log.warning(f"lrclib '{q}': {e}")
    return []

async def get_lyrics(artist: str, title: str) -> list[tuple[float, str]]:
    cache_key = f"{artist}|{title}"
    if cache_key in lyrics_cache:
        return lyrics_cache[cache_key]
    log.info(f"Searching lyrics: {artist} — {title}")
    queries = [
        f"{artist} {title}",
        title,
        f"{artist.split(',')[0].strip()} {title}",
    ]
    lines = []
    for q in queries:
        lines = await search_lrclib(q)
        if lines:
            log.info(f"lrclib: {len(lines)} lines")
            break
    if not lines:
        log.warning("Lyrics not found")
    lyrics_cache[cache_key] = lines
    return lines

class DiscordClient:
    API = "https://discord.com/api/v9"

    def __init__(self, token: str):
        self.headers = {"Authorization": token, "Content-Type": "application/json"}
        self._status_text = ""

    async def set_status(self, text: str):
        if text == self._status_text:
            return
        self._status_text = text
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                payload = {"custom_status": {"text": text, "emoji_name": "🎵"} if text else None}
                async with session.patch(f"{self.API}/users/@me/settings", json=payload) as r:
                    if r.status == 200:
                        log.info(f"Status → {text!r}")
                    else:
                        log.warning(f"Discord {r.status}")
        except Exception as e:
            log.warning(f"Discord: {e}")

    async def clear_status(self):
        await self.set_status("")

def get_current_line(lines: list[tuple[float, str]], position: float) -> str:
    current = lines[0][1]
    for t, text in lines:
        if position >= t:
            current = text
        else:
            break
    return current

async def main():
    dc = DiscordClient(DISCORD_TOKEN)
    current_key: Optional[str] = None
    lines: list[tuple[float, str]] = []
    none_count    = 0      # consecutive None returns from SMTC
    paused_count  = 0      # consecutive position not moving
    prev_position = -999.0
    base_position = 0.0
    base_time     = 0.0

    log.info("Started — waiting for music...")

    while True:
        state = await get_smtc_state()

        if state is None:
            none_count += 1
            # clear only if no session for long (player closed)
            if none_count >= 5 and current_key is not None:
                log.info("Player closed — clearing status")
                current_key = None
                lines = []
                none_count = 0
                paused_count = 0
                prev_position = -999.0
                await dc.clear_status()
            # continue showing interpolated lyrics
            if lines and current_key is not None:
                elapsed = time.monotonic() - base_time
                cur_pos = base_position + elapsed + TIME_OFFSET
                line = get_current_line(lines, cur_pos)
                if len(line) > MAX_STATUS_LEN:
                    line = line[:MAX_STATUS_LEN - 1] + "…"
                await dc.set_status(f"🎵 {line}")
        else:
            none_count = 0
            artist   = state["artist"]
            title    = state["title"]
            position = state["position"]
            playing  = state["playing"]
            key      = f"{artist}|{title}"

            # new track - reset everything
            if key != current_key:
                log.info(f"New track: {artist} — {title}")
                current_key   = key
                paused_count  = 0
                prev_position = -999.0
                base_position = position
                base_time     = time.monotonic()
                lines = await get_lyrics(artist, title)

            # update position base if moving
            if abs(position - prev_position) > 0.3:
                base_position = position
                base_time     = time.monotonic()
            prev_position = position

            # pause - clear immediately
            if not playing:
                if current_key is not None:
                    log.info("Paused — clearing status")
                    current_key   = None
                    lines         = []
                    prev_position = -999.0
                    await dc.clear_status()
            else:
                # show current line
                elapsed = time.monotonic() - base_time
                cur_pos = base_position + elapsed + TIME_OFFSET
                if lines:
                    line = get_current_line(lines, cur_pos)
                    if len(line) > MAX_STATUS_LEN:
                        line = line[:MAX_STATUS_LEN - 1] + "…"
                    await dc.set_status(f" {line}")
                else:
                    await dc.set_status(f" {artist} — {title}")

        await asyncio.sleep(2)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped.")