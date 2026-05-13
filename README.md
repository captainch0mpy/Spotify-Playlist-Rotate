# Playlist Rotator

A two-way rotation tool for Spotify playlists. Tracks that have been in your primary playlist for too long move to a holding playlist; tracks that have rested in holding long enough come back. The age of each track resets every time it moves, so the cycle continues naturally with no bookkeeping.

**🎵 Live demo: <https://captainch0mpy.github.io/Spotify-Playlist-Rotate/>**

You'll still need to create your own Spotify developer app (see [Prerequisites](#prerequisites-spotify-app-setup) below) — the demo is the same code, just hosted so you don't have to run it locally.

Comes in two flavors that share the same logic:

| Tool | Best for | Runs where |
|---|---|---|
| `playlist-rotator.html` | Interactive use, previewing changes before applying | A browser, served from anywhere static |
| `spotify_rotate.py` | Scheduled / unattended rotations | Any machine with Python, e.g. cron |

Both talk directly to Spotify's Web API. No third-party server, no data leaves your machine.

---

## How it works

Spotify stamps every track in a playlist with an `added_at` timestamp. The tool reads those timestamps and decides:

- **Rotate out:** tracks in PRIMARY older than the rotate threshold → copy to HOLDING, remove from PRIMARY
- **Revive:** tracks in HOLDING older than the revive threshold → copy to PRIMARY, remove from HOLDING

Defaults are 6 months to rotate out, 1 year to revive — so each track does about an 18-month cycle. Both thresholds are configurable per run.

When a track moves between playlists, Spotify stamps it with a new `added_at`. The next cycle's clock starts fresh automatically.

---

## Prerequisites: Spotify app setup

This part is the same regardless of which flavor you use. You need a Spotify Developer app for the tool to authenticate against. Takes about three minutes.

1. **Premium required.** As of February 2026, Spotify requires development-mode app owners to have an active Spotify Premium subscription. If your subscription lapses, the app stops working until you resubscribe.

2. **Create the app** at <https://developer.spotify.com/dashboard>. Click **Create app**, name it anything ("Playlist Rotator" is fine).

3. **Redirect URI.** What you enter depends on which flavor:
   - **Using the hosted demo** at <https://captainch0mpy.github.io/Spotify-Playlist-Rotate/>: enter that URL, trailing slash included.
   - **Self-hosting the web app on your own HTTPS URL**: enter that URL.
   - **Running the web app locally** at `http://127.0.0.1:8080/`: enter exactly that, including the trailing slash.
   - **Python script**: enter `http://127.0.0.1:8888/callback`.

   Spotify rejects `localhost` and rejects any HTTP redirect URI that isn't a loopback IP literal — the only HTTP URLs that work are `http://127.0.0.1:PORT` and `http://[::1]:PORT`. Everything else must be HTTPS. You can register multiple URIs per app, so if you want both the hosted demo and a local setup, add both.

4. **APIs used.** Select **Web API**. Save.

5. **User Management.** Open the app's Settings → User Management tab and add yourself: your Spotify display name and the email registered to your Spotify account. Without this, API calls return 403 even though login succeeds.

6. **Copy credentials.** Note the Client ID. For the Python script you also need the Client Secret (the web app uses PKCE and doesn't need a secret).

---

## Option A: Web app

A single static HTML file with no backend. Auth runs in the browser using PKCE.

### Quickest local run (Podman / Docker)

Put `playlist-rotator.html` and `Containerfile` in a folder, then:

```bash
podman build -t playlist-rotator .
podman run -d --name playlist-rotator \
  -p 127.0.0.1:8080:80 \
  --restart unless-stopped \
  playlist-rotator
```

Visit <http://127.0.0.1:8080/>. The `-p 127.0.0.1:8080:80` binding ensures the page is only reachable from your own machine.

To update after editing the HTML:

```bash
podman rm -f playlist-rotator
podman build -t playlist-rotator .
podman run -d --name playlist-rotator -p 127.0.0.1:8080:80 --restart unless-stopped playlist-rotator
```

### Without containers

Any static file server works. From the folder containing the HTML:

```bash
python -m http.server 8080 --bind 127.0.0.1
```

Visit <http://127.0.0.1:8080/playlist-rotator.html>.

### Deploy to a public host

The file is fully self-contained — drop it on any static host that gives you HTTPS (GitHub Pages, Netlify Drop, Cloudflare Pages, Vercel) and register the resulting HTTPS URL as the redirect URI in your Spotify app. The token still only lives in your browser; the host never sees it.

This project's own live deployment at <https://captainch0mpy.github.io/Spotify-Playlist-Rotate/> is hosted via GitHub Pages straight from the `index.html` in this repo — no build step, just push to `main` and Pages serves the latest. The same `index.html` works without modification on any other static host.

### Using it

On first visit you'll be prompted for your Client ID (one-time, stored in localStorage). Then log in with Spotify. Pick your primary and holding playlists from the dropdowns, set the thresholds, click **Preview** to see what would move in both directions, click **Apply** to commit.

Preview shows up to 50 tracks per direction. The full list is moved when you Apply.

---

## Option B: Python script

Best for cron-driven rotations where you don't want to open a browser.

### Install

```bash
pip install spotipy requests python-dateutil
```

### Configure

Open `spotify_rotate.py` and fill in the CONFIG block at the top:

```python
CLIENT_ID            = "..."
CLIENT_SECRET        = "..."
REDIRECT_URI         = "http://127.0.0.1:8888/callback"

PRIMARY_PLAYLIST_ID  = "..."
HOLDING_PLAYLIST_ID  = "..."

ROTATE_THRESHOLD     = "6m"
REVIVE_THRESHOLD     = "1y"
```

To get playlist IDs: right-click a playlist in Spotify → Share → Copy link. The ID is the long string after `/playlist/` and before `?si=...`.

### Run

```bash
python spotify_rotate.py                    # dry run, both directions
python spotify_rotate.py --apply            # actually move tracks
python spotify_rotate.py --only rotate      # only stale-out
python spotify_rotate.py --only revive      # only bring-back
python spotify_rotate.py --rotate 90d       # override rotate threshold
python spotify_rotate.py --revive 2y        # override revive threshold
```

The first run opens a browser to authorize. After that, a cached token in `.spotify_token_cache` lets it run unattended.

### Automating with cron

A weekly rotation, Sundays at 3am:

```cron
0 3 * * 0  cd /path/to/folder && /usr/bin/python3 spotify_rotate.py --apply >> rotate.log 2>&1
```

For Windows, Task Scheduler does the equivalent.

---

## Duration syntax

Both flavors accept calendar-aware durations:

| Format | Meaning |
|---|---|
| `Nd` | N days |
| `Nm` | N months (calendar-aware — Feb 29 + 1m = Mar 29) |
| `Ny` | N years |

`6m` and `180d` are close but not identical (months vary in length, leap years exist). Use months/years when you mean "next time this date comes around" and days when you mean a fixed count.

---

## Known issues and Spotify quirks

**Track count shows `(?)` next to playlist names in the web app.** Cosmetic. Spotify dropped the track-count field from playlist list responses in their February 2026 dev-mode changes. Rotation still works correctly.

**Local files are skipped.** Tracks you've added from your own machine (rather than from Spotify's catalog) can't be moved via the API, so the tool quietly ignores them. They'll stay in whichever playlist they're in.

**The development-mode user limit.** New Spotify apps are now capped at 5 authorized users (down from 25 in early 2026). For personal use this never matters — you only need yourself authorized. If you want others to use *your* app instance, each one has to be added via Settings → User Management. The cleaner alternative is for each person to create their own app.

**Token expiry in the web app.** Access tokens last about an hour. If you leave the page open longer than that and come back, you'll be asked to log in again. The cached refresh token persists across sessions, so this is usually one click.

**If a rotated track was on shuffle queue.** Spotify sometimes keeps a queued track playing after you remove it from a playlist. New playback after that point won't include it.

---

## Files

```
index.html              # The web app, single file, no dependencies
Containerfile           # nginx-based image for serving locally
spotify_rotate.py       # The CLI version, for scheduled runs
README.md               # This file
.gitignore              # Prevents the spotipy token cache from being committed
```

---

## License & attribution

Personal-use code, no warranty. Spotify is a trademark of Spotify AB; this project is not affiliated with or endorsed by Spotify.
