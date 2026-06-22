import asyncio
import logging
import re
import time
import json
import os
import threading
from typing import Optional
from collections import OrderedDict

import aiohttp
import winrt.windows.media.control as wmc
import customtkinter as ctk
from PIL import Image
from pypresence import AioPresence  # new lib for rpc card

# logic config
MAX_STATUS_LEN = 100
CACHE_LIMIT    = 100
CONFIG_FILE    = "config.json"

lyrics_cache: OrderedDict[str, list[tuple[float, str]]] = OrderedDict()

log = logging.getLogger("spotify-discord")
log.setLevel(logging.INFO)
log.propagate = False

# helper functions
def format_time(seconds: float) -> str:
    if seconds < 0: seconds = 0
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"

def parse_lrc(synced: str) -> list[tuple[float, str]]:
    lines = []
    for line in synced.splitlines():
        m = re.match(r'\[(\d+):(\d+\.\d+)\]\s*(.*)', line)
        if not m: continue
        t = int(m.group(1)) * 60 + float(m.group(2))
        text = m.group(3).strip()
        if text: lines.append((t, text))
    return lines

async def get_smtc_state() -> Optional[dict]:
    try:
        manager = await wmc.GlobalSystemMediaTransportControlsSessionManager.request_async()
        sp_session = None
        for session in manager.get_sessions():
            if "spotify" in (session.source_app_user_model_id or "").lower():
                sp_session = session
                break
        if sp_session is None: return None

        info     = await sp_session.try_get_media_properties_async()
        timeline = sp_session.get_timeline_properties()
        status   = int(sp_session.get_playback_info().playback_status)

        title = info.title or ""
        if not title: return None

        return {
            "artist":   info.artist or "",
            "title":    title,
            "position": timeline.position.total_seconds(),
            "duration": timeline.end_time.total_seconds(),
            "playing":  status == 4,
        }
    except Exception as e:
        log.warning(f"windows media player read error {e}")
        return None

async def search_lrclib(session: aiohttp.ClientSession, q: str) -> list[tuple[float, str]]:
    try:
        async with session.get("https://lrclib.net/api/search", params={"q": q}) as r:
            if r.status != 200: return []
            results = await r.json()
        for item in results:
            synced = item.get("syncedLyrics", "")
            if synced:
                lines = parse_lrc(synced)
                if lines: return lines
    except Exception as e: 
        log.warning(f"lrclib error {q} {e}")
    return []

async def get_lyrics(session: aiohttp.ClientSession, artist: str, title: str) -> list[tuple[float, str]]:
    cache_key = f"{artist}|{title}"
    if cache_key in lyrics_cache:
        lyrics_cache.move_to_end(cache_key)
        return lyrics_cache[cache_key]

    queries = []
    if artist:
        queries.append(f"{artist} {title}")
        queries.append(f"{artist.split(',')[0].strip()} {title}")
    queries.append(title)
    queries = list(dict.fromkeys(queries))
    
    lines = []
    for q in queries:
        lines = await search_lrclib(session, q)
        if lines: break

    if len(lyrics_cache) >= CACHE_LIMIT: lyrics_cache.popitem(last=False)
    lyrics_cache[cache_key] = lines
    return lines

class DiscordClient:
    API = "https://discord.com/api/v9"
    def __init__(self, token: str):
        self.headers = {"Authorization": token.strip(), "Content-Type": "application/json"}
        self._status_text = ""
        self._emoji = ""
        self.session = aiohttp.ClientSession(headers=self.headers, timeout=aiohttp.ClientTimeout(total=5))
        self.cooldown_until = 0.0

    async def set_status(self, text: str, emoji_name: str = "🎵", status_cb=None):
        if text == self._status_text and emoji_name == self._emoji: return
        now = time.time()
        if now < self.cooldown_until: return

        self._status_text = text
        self._emoji = emoji_name
        try:
            payload = {"custom_status": {"text": text, "emoji_name": emoji_name} if text else None}
            async with self.session.patch(f"{self.API}/users/@me/settings", json=payload) as r:
                if r.status == 200:
                    if status_cb and text: status_cb(f"Status: {emoji_name} {text}")
                elif r.status == 429:
                    retry_after = 5.0
                    try:
                        res_json = await r.json()
                        retry_after = res_json.get("retry_after", 5.0)
                    except Exception:
                        retry_after = float(r.headers.get("Retry-After", 5.0))
                    self.cooldown_until = time.time() + retry_after
                    log.warning(f"discord rate limit 429 cooling down for {retry_after}s")
        except Exception as e: log.warning(f"discord request failed {e}")

    async def clear_status(self): await self.set_status("", "")
    async def close(self): await self.session.close()

def get_current_line(lines: list[tuple[float, str]], position: float) -> str:
    current = lines[0][1] if lines else ""
    for t, text in lines:
        if position >= t: current = text
        else: break
    return current

# main async loop
async def status_loop(client_id: str, token: str, offset: float, mode: str, user_emoji: str, update_ui_callback, status_cb, stop_event: threading.Event):
    log.info("starting status module")
    
    dc = DiscordClient(token) if token else None
    lrc_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
    rpc = None
    
    # init rich presence
    if client_id:
        try:
            rpc = AioPresence(client_id)
            await rpc.connect()
            log.info("connected to local Discord RPC (Rich Presence)")
        except Exception as e:
            log.warning(f"failed to connect to local Discord RPC: {e}")
            rpc = None

    current_key, lines = None, []
    none_count, prev_position, base_position, base_time = 0, -999.0, 0.0, 0.0
    last_discord_update = 0.0

    update_ui_callback("Waiting for music...", "gray")
    try:
        while not stop_event.is_set():
            state = await get_smtc_state()
            if state is None:
                none_count += 1
                if none_count >= 5 and current_key is not None:
                    log.info("spotify inactive clearing status")
                    current_key, lines = None, []
                    if dc: await dc.clear_status()
                    if rpc: await rpc.clear()
                    update_ui_callback("Spotify closed / No music playing", "gray")
            else:
                none_count = 0
                artist, title = state["artist"], state["title"]
                position, duration, playing = state["position"], state["duration"], state["playing"]
                key = f"{artist}|{title}"
                track_name = f"{artist} — {title}" if artist else title

                # track changed or seeked
                if key != current_key or abs(position - prev_position) > 1.5:
                    if key != current_key:
                        log.info(f"new track detected: {track_name}")
                        if dc and "Lyrics" in mode:
                            update_ui_callback(f"Searching lyrics: {track_name}", "orange")
                            lines = await get_lyrics(lrc_session, artist, title)
                        else:
                            lines = []
                    
                    current_key = key
                    base_position, base_time = position, time.monotonic()
                    
                    # update rpc card
                    if rpc and playing:
                        is_local_file = not artist  # no artist means local file
                        if is_local_file:
                            try:
                                start_epoch = int(time.time() - position)
                                end_epoch = start_epoch + int(duration)
                                await rpc.update(
                                    details=title[:128],
                                    state="Local File",
                                    start=start_epoch,
                                    end=end_epoch,
                                    large_image="spotify", # img from art assets
                                    large_text="Spotify Local"
                                )
                                status_cb(f"RPC Card: {title} - {format_time(position)}/{format_time(duration)}")
                            except Exception as e:
                                log.warning(f"rpc update error: {e}")
                        else:
                            # clear custom rpc for normal tracks to allow native spotify integration
                            try:
                                await rpc.clear()
                            except Exception:
                                pass

                prev_position = position

                if not playing:
                    if current_key is not None:
                        log.info("player paused")
                        current_key, lines = None, []
                        if dc: await dc.clear_status()
                        if rpc: await rpc.clear() # clear card on pause
                        update_ui_callback("Track paused", "gray")
                else:
                    elapsed = time.monotonic() - base_time
                    now = time.time()
                    
                    # update custom status text
                    if dc:
                        if lines:
                            line = get_current_line(lines, base_position + elapsed + offset)
                            if len(line) > MAX_STATUS_LEN: line = line[:MAX_STATUS_LEN - 1] + "…"
                            await dc.set_status(line, user_emoji, status_cb)
                            update_ui_callback(f"Streaming lyrics: {track_name}", "#2ebd59")
                        else:
                            if mode == "Smart (Lyrics Only)":
                                status_text = track_name[:MAX_STATUS_LEN]
                                if now - last_discord_update >= 8.0:
                                    await dc.set_status(status_text, user_emoji, status_cb)
                                    last_discord_update = now
                                update_ui_callback(f"Streaming title: {track_name}", "#2ebd59")
                            else:
                                time_str = f"[{format_time(base_position + elapsed)} / {format_time(duration)}]"
                                status_text = f"{track_name} {time_str}"
                                if len(status_text) > MAX_STATUS_LEN:
                                    avail = MAX_STATUS_LEN - len(time_str) - 2
                                    status_text = f"{track_name[:avail]}… {time_str}" if avail > 0 else track_name[:MAX_STATUS_LEN]
                                
                                if now - last_discord_update >= 8.0:
                                    await dc.set_status(status_text, user_emoji, status_cb)
                                    last_discord_update = now
                                update_ui_callback(f"Streaming timer: {track_name}", "#2ebd59")

            await asyncio.sleep(1.0)
    except Exception as e:
        log.error(f"loop error {e}")
        update_ui_callback(f"Error: {e}", "red")
    finally:
        log.info("status module stopped")
        if dc:
            await dc.clear_status()
            await dc.close()
        if rpc: 
            await rpc.clear()
            rpc.close()
        await lrc_session.close()

# console log handler
class AppConsoleHandler(logging.Handler):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        msg = self.format(record)
        self.callback(msg)

# gui implementation
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        self.title("Spotify Status for Discord")
        self.geometry("760x650")
        self.resizable(False, False)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")
        
        def set_icon():
            try:
                icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "img", "logo.png")
                if os.path.exists(icon_path):
                    from PIL import ImageTk, Image
                    raw_icon = Image.open(icon_path)
                    raw_icon = raw_icon.resize((32, 32), Image.Resampling.LANCZOS)
                    self.window_icon = ImageTk.PhotoImage(raw_icon)
                    self.wm_iconphoto(False, self.window_icon)
            except Exception as e:
                log.warning(f"failed to load window icon: {e}")
        
        self.after(500, set_icon)

        self.async_thread = None
        self.stop_event = threading.Event()

        self.creator_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.creator_frame.place(relx=0.98, rely=0.02, anchor="ne")

        try:
            image_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "img", "banneroff.png")
            if os.path.exists(image_path):
                raw_img = Image.open(image_path).convert("RGBA")
                alpha = raw_img.split()[3]
                alpha = alpha.point(lambda p: int(p * 0.7))
                raw_img.putalpha(alpha)
                orig_w, orig_h = raw_img.size
                target_h = 24 
                target_w = int(orig_w * (target_h / orig_h))
                loaded_img = ctk.CTkImage(light_image=raw_img, dark_image=raw_img, size=(target_w, target_h))
                
                self.creator_text = ctk.CTkLabel(self.creator_frame, text="Created by", font=ctk.CTkFont(size=11, slant="italic"), text_color="#777777")
                self.creator_text.pack(side="left", padx=(0, 4)) 
                self.creator_img = ctk.CTkLabel(self.creator_frame, text="", image=loaded_img)
                self.creator_img.pack(side="left")
            else:
                self.creator_label = ctk.CTkLabel(self.creator_frame, text="Created by NOVAPBS", font=ctk.CTkFont(size=11, slant="italic"), text_color="#777777")
                self.creator_label.pack(side="left")
        except Exception: pass
        
        # header
        self.label_title = ctk.CTkLabel(self, text="Spotify ➔ Discord Status", font=ctk.CTkFont(size=20, weight="bold"))
        self.label_title.pack(pady=(15, 10))
        
        # auth widgets
        self.auth_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.auth_frame.pack(pady=5)
        
        self.client_id_entry = ctk.CTkEntry(self.auth_frame, placeholder_text="Discord Client ID (Для большой карточки RPC)", width=350)
        self.client_id_entry.pack(pady=(0, 5))

        self.token_entry = ctk.CTkEntry(self.auth_frame, placeholder_text="Discord Token (Для текстов песен под ником)", width=350, show="*")
        self.token_entry.pack(pady=(0, 5))

        # display mode
        self.mode_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.mode_frame.pack(pady=5, fill="x", padx=200)
        self.mode_label = ctk.CTkLabel(self.mode_frame, text="Display Mode:")
        self.mode_label.pack(side="left")
        self.mode_select = ctk.CTkOptionMenu(self.mode_frame, values=["Smart (Lyrics + Timer)", "Smart (Lyrics Only)", "Title + Timer Only"], width=190)
        self.mode_select.pack(side="right")

        # custom emoji
        self.emoji_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.emoji_frame.pack(pady=5, fill="x", padx=200)
        self.emoji_label = ctk.CTkLabel(self.emoji_frame, text="Status Emoji:")
        self.emoji_label.pack(side="left")
        self.emoji_entry = ctk.CTkEntry(self.emoji_frame, width=50)
        self.emoji_entry.insert(0, "🎵")
        self.emoji_entry.pack(side="right")

        # offset adjustment
        self.slider_label = ctk.CTkLabel(self, text="Lyrics Offset: 0.8 sec")
        self.slider_label.pack(pady=(5, 0))
        self.offset_slider = ctk.CTkSlider(self, from_=0.0, to=3.0, number_of_steps=30, width=350, command=self.update_slider_label)
        self.offset_slider.set(0.8)
        self.offset_slider.pack(pady=5)

        # main exec button
        self.btn_toggle = ctk.CTkButton(self, text="Start Status", font=ctk.CTkFont(weight="bold", size=14), fg_color="#2ebd59", hover_color="#1ed760", command=self.toggle_script)
        self.btn_toggle.pack(pady=15)

        # short status bar
        self.status_label = ctk.CTkLabel(self, text="Status: Disabled", text_color="gray", font=ctk.CTkFont(size=12))
        self.status_label.pack(pady=5)

        # consoles
        self.consoles_container = ctk.CTkFrame(self, fg_color="transparent")
        self.consoles_container.pack(fill="both", expand=True, padx=15, pady=(0, 15), side="bottom")

        self.left_frame = ctk.CTkFrame(self.consoles_container, fg_color="transparent")
        self.left_frame.pack(side="left", fill="both", expand=True, padx=(0, 7))
        self.left_label = ctk.CTkLabel(self.left_frame, text="System Log", font=ctk.CTkFont(size=11, weight="bold"), text_color="gray")
        self.left_label.pack(anchor="w")
        self.console = ctk.CTkTextbox(self.left_frame, font=ctk.CTkFont(family="Consolas", size=11), fg_color="#1e1e1e", text_color="#cccccc", state="disabled")
        self.console.pack(fill="both", expand=True)

        self.right_frame = ctk.CTkFrame(self.consoles_container, fg_color="transparent")
        self.right_frame.pack(side="right", fill="both", expand=True, padx=(7, 0))
        self.right_label = ctk.CTkLabel(self.right_frame, text="Discord Output", font=ctk.CTkFont(size=11, weight="bold"), text_color="#2ebd59")
        self.right_label.pack(anchor="w")
        self.status_console = ctk.CTkTextbox(self.right_frame, font=ctk.CTkFont(family="Consolas", size=11), fg_color="#141414", text_color="#2ebd59", state="disabled")
        self.status_console.pack(fill="both", expand=True)

        log.addHandler(AppConsoleHandler(self.append_to_console))
        self.load_config()
        log.info("application initialized successfully")

    def append_to_console(self, text):
        def update():
            self.console.configure(state="normal")
            self.console.insert("end", text + "\n")
            self.console.see("end")
            self.console.configure(state="disabled")
        self.after(0, update)

    def append_to_status_console(self, text):
        def update():
            t_str = time.strftime("%H:%M:%S")
            self.status_console.configure(state="normal")
            self.status_console.insert("end", f"{t_str} -> {text}\n")
            self.status_console.see("end")
            self.status_console.configure(state="disabled")
        self.after(0, update)

    def update_slider_label(self, val):
        self.slider_label.configure(text=f"Lyrics Offset: {val:.1f} sec")

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    self.client_id_entry.insert(0, config.get("client_id", ""))
                    self.token_entry.insert(0, config.get("token", ""))
                    loaded_offset = config.get("offset", 0.8)
                    self.offset_slider.set(loaded_offset)
                    self.update_slider_label(loaded_offset)
                    
                    if "mode" in config: self.mode_select.set(config["mode"])
                    if "emoji" in config:
                        self.emoji_entry.delete(0, ctk.END)
                        self.emoji_entry.insert(0, config["emoji"])
            except Exception as e: log.warning(f"failed to load config {e}")

    def save_config(self):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "client_id": self.client_id_entry.get().strip(),
                "token": self.token_entry.get().strip(), 
                "offset": self.offset_slider.get(),
                "mode": self.mode_select.get(),
                "emoji": self.emoji_entry.get().strip() or "🎵"
            }, f, ensure_ascii=False, indent=4)

    def update_status_ui(self, text, color="white"):
        self.after(0, lambda: self.status_label.configure(text=text, text_color=color))

    def toggle_script(self):
        if self.async_thread and self.async_thread.is_alive():
            self.stop_event.set()
            self.btn_toggle.configure(text="Start Status", fg_color="#2ebd59", hover_color="#1ed760")
            self.update_status_ui("Stopping...", "orange")
            def wait_for_stop():
                self.async_thread.join(timeout=3.0)
                self.update_status_ui("Status: Disabled", "gray")
            threading.Thread(target=wait_for_stop, daemon=True).start()
        else:
            client_id = self.client_id_entry.get().strip()
            token = self.token_entry.get().strip()
            
            if not client_id and not token:
                self.update_status_ui("Error: Provide Token or Client ID", "red")
                return
                
            self.save_config()
            self.stop_event.clear()
            self.btn_toggle.configure(text="Stop Status", fg_color="#e91e63", hover_color="#c2185b")
            
            self.async_thread = threading.Thread(
                target=self.run_async, 
                args=(client_id, token, self.offset_slider.get(), self.mode_select.get(), self.emoji_entry.get().strip() or "🎵"), 
                daemon=True
            )
            self.async_thread.start()

    def run_async(self, client_id, token, offset, mode, emoji):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(status_loop(client_id, token, offset, mode, emoji, self.update_status_ui, self.append_to_status_console, self.stop_event))
        finally:
            loop.close()

if __name__ == "__main__":
    app = App()
    app.mainloop()