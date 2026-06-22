---

# Spotify lyrics to Discord status

A script that synchronously displays the lyrics of the current Spotify song in your Discord status, while providing a fallback Rich Presence card for local files.

---

A real-time Python application with a modern graphical user interface (GUI) that captures your currently playing Spotify track from Windows and broadcasts the live synchronized lyrics directly to your Discord custom status.

With the latest update, the application now fully supports **Local Files**, automatically generating a custom Discord Rich Presence (RPC) card when native Spotify integration fails to recognize your downloaded music.

---

## Features

* **Real-Time SMTC Integration:** Intercepts media states natively from Windows Media Control (SMTC) without requiring a Spotify Developer API account.
* **Smart Synchronization:** Dynamically fetches time-synced `.lrc` lyrics via the LRCLIB API.
* **Local Files Support (Rich Presence):** Automatically detects local audio playback and connects to your local Discord client to display a custom "Playing" card with the track name and a live duration timer.
* **Seamless Handoff:** The custom RPC card gracefully clears itself the moment a standard Spotify track plays, allowing Discord's native Spotify integration to take over without conflict.
* **Dual-Console GUI:** Built with `CustomTkinter`. Features a split-view system:
* **System Log (Left):** Tracks backend execution, network responses, and rate limits.
* **Status Output (Right):** Monitors exactly what text and RPC data are currently visible to your Discord friends.


* **Intelligent Rate-Limit Protection:** Intercepts Discord HTTP `429 Too Many Requests` responses, automatically reading the `retry_after` header and pausing execution to protect your account.
* **Customization:** Adjustable time offsets (to counter network latency), multiple display modes, and custom status emoji support.

---

## How It Works

The backend operates as an asynchronous polling loop driven by `asyncio` and `threading`, utilizing a dual-path routing system for standard and local tracks:

```text
[ Spotify Player ] 
       │
       ▼ (Every 1.0s via Windows SMTC)
[ Windows Media Session Manager ] 
       │
       ▼ (Extracts Artist + Title + Position)
[ Python Core Loop ] ──► (Is Local File?) ──► [ Local Discord RPC (pypresence) ]
       │
       ▼ (If Standard Track)
[ Check Internal Cache ] ──► (If Missing) ──► [ LRCLIB API ]
       │
       ▼ (Matches Timestamp to Status Line)
[ Discord Client Patch Request ] ───► [ Discord User Status ]

```

1. **Extraction:** Every second, the application queries the Windows Global System Media Transport Controls to read the current media broadcasting state.
2. **Routing:** It checks the track metadata. If the track lacks an `artist` tag (identifying it as a local file), the app pipes the data directly to Discord via local IPC (Rich Presence).
3. **Lyrics Matching:** For standard streaming tracks, it queries LRCLIB (or the local cache) and parses timestamps formatted as `[mm:ss.xx]` into matching floating-point seconds.
4. **API Dispatch:** The script matches the track's current timestamp against the lyrics array and updates your custom text status via an asynchronous HTTP PATCH request.

---

## Authentication: Token & Client ID

To power both the Custom Status (lyrics) and the Rich Presence (local files), the application requires two distinct forms of authentication.

### 1. The Discord Token (For Lyrics)

Unlike standard Discord bots, modifying a human user's custom status requires your personal **Discord User Token** (self-token).

* **How to get it:** Open Discord in your browser ➔ Press `F12` (Developer Tools) ➔ **Network** tab ➔ Reload the page ➔ Search `/api/v9/users/@me/settings` ➔ Look for **Authorization** in the *Request Headers*.

### 2. The Discord Client ID (For Local Files RPC)

Rich Presence operates through Discord's official developer framework. This is entirely safe and officially supported by Discord.

* **How to get it:**
1. Go to the [Discord Developer Portal](https://www.google.com/search?q=https://discord.com/developers/applications).
2. Click **New Application** and give it a name (e.g., "Spotify Local").
3. Copy the **Application ID** (This is your Client ID).
4. *(Optional)* To display the Spotify logo on your card, go to **Rich Presence ➔ Art Assets** in the developer portal, add a Spotify icon image, and name it exactly `spotify`.



---

## Security Risks & Warnings

> ⚠️ **CRITICAL WARNING: READ BEFORE RUNNING**
> Utilizing a user token to automate account actions (Custom Status lyrics) is classified as **Self-Botting** and is a direct violation of **Discord's Terms of Service (ToS)**. *(Note: The Client ID / RPC feature is fully ToS-compliant).*

### 1. Risk of Account Suspension

Automated status changes generate a high volume of API traffic. If Discord's automated anti-spam systems flag your token for unnatural activity, your account could face a permanent ban or temporary suspension.

### 2. Token Security

Your token is essentially your plaintext password. If someone steals this token, they gain **complete access to your Discord account** without needing your password or 2FA.

* **Never** hardcode your token into public repositories.
* The `config.json` file generated by this application contains your token locally. Treat it with extreme caution.

### 3. Mitigating the Risks (How this App Protects You)

* **Adaptive Timer Mode:** If a song lacks lyrics, the app switches to timer mode and scales back requests to **once every 8 seconds** instead of every second.
* **Smart Rate Limiter:** If the script encounters an HTTP `429` error, it immediately freezes all outbound requests for the precise amount of time requested by Discord's servers, eliminating aggressive spam loops.

---

## Installation & Launch

### Prerequisites

* Windows 10 or Windows 11 (SMTC architecture is exclusive to Windows)
* Python 3.10 or higher
* Desktop Discord Client running in the background (Required for Local Files RPC)

### Step-by-Step Setup

1. Clone the repository and navigate into the project folder:

```bash
cd spotify_lyrics-discordstatus

```

2. Initialize a clean virtual environment:

```bash
python -m venv .venv

```

3. Activate the environment and install the foundational runtime extensions alongside the GUI framework and RPC library:

```powershell
.venv\Scripts\Activate.ps1

.venv\Scripts\pip.exe install customtkinter pillow aiohttp pypresence winrt-runtime winrt-Windows.Media.Control winrt-Windows.Foundation winrt-Windows.Foundation.Collections

```

4. Launch the application:

```powershell
.venv\Scripts\python.exe main.py

```
