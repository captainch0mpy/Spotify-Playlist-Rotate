"""
Spotify Playlist Rotator
------------------------
Two-way rotation between a primary playlist and a holding playlist:

  ROTATE  : tracks in PRIMARY older than ROTATE_THRESHOLD  ->  HOLDING
  REVIVE  : tracks in HOLDING older than REVIVE_THRESHOLD  ->  PRIMARY

A track's "age" is when it was added to its current playlist, so the cycle
restarts naturally each time a song moves.

Thresholds use a compact duration string built from any combination of:
    Ny    years          (calendar-aware)
    Nmo   months         (calendar-aware; also accepts plain Nm)
    Nd    days
    Nh    hours

Combine units in any order, with or without spaces:
    1y                   = 1 year
    6mo                  = 6 months
    18mo                 = 18 months
    180d                 = 180 days
    1y 2mo 15d           = 1 year, 2 months, 15 days
    1y2mo15d6h           = same, with no spaces
    36h                  = 36 hours
Months and years use calendar-aware math (Feb-29 + 1y = Feb-28, etc.).

SETUP (one-time)
================
1. Install dependencies:
       pip install spotipy requests python-dateutil

2. Create a Spotify app at https://developer.spotify.com/dashboard
   - Add a Redirect URI: http://127.0.0.1:8888/callback
   - Note: Spotify no longer accepts 'localhost' in redirect URIs; use the
     loopback IP 127.0.0.1 explicitly.
   - Copy the Client ID and Client Secret.
   - In the app's Settings > User Management tab, add your own Spotify
     account (display name + the email registered to your Spotify account).
     This is required for new development-mode apps to use the API.

3. Get your two playlist IDs:
   In Spotify, right-click a playlist -> Share -> Copy link to playlist.
   The ID is the long string after /playlist/ and before the ?si=...

4. Fill in the CONFIG section below.

USAGE
=====
    python spotify_rotate.py                       # dry run, both directions
    python spotify_rotate.py --apply               # actually move tracks
    python spotify_rotate.py --only rotate         # only stale-out
    python spotify_rotate.py --only revive         # only bring-back
    python spotify_rotate.py --rotate 90d          # override rotate threshold
    python spotify_rotate.py --revive "1y 6mo"     # composite duration
    python spotify_rotate.py --rotate 36h          # rotate after 36 hours

The first run opens a browser to authorize. After that a cached token
lets it run unattended (good for cron / Task Scheduler).
"""

import argparse
import re
import sys
from datetime import datetime, timezone

import requests
import spotipy
from dateutil.relativedelta import relativedelta
from spotipy.oauth2 import SpotifyOAuth

# ============================ CONFIG ============================
CLIENT_ID            = "YOUR_CLIENT_ID_HERE"
CLIENT_SECRET        = "YOUR_CLIENT_SECRET_HERE"
REDIRECT_URI         = "http://127.0.0.1:8888/callback"

PRIMARY_PLAYLIST_ID  = "YOUR_PRIMARY_PLAYLIST_ID"
HOLDING_PLAYLIST_ID  = "YOUR_HOLDING_PLAYLIST_ID"

ROTATE_THRESHOLD     = "6m"     # primary -> holding after this long
REVIVE_THRESHOLD     = "1y"     # holding -> primary after this long
# ================================================================

API    = "https://api.spotify.com/v1"
SCOPES = "playlist-modify-public playlist-modify-private playlist-read-private"


# ---------- duration parsing ----------

# Single-unit regex used by the composite parser below.
# Alternation order matters: 'mo' must be tried before 'm' so that '6mo' parses
# as 6 months rather than '6m' followed by stray 'o'.
_PART_RE = re.compile(r"(\d+)\s*(y|mo|m|d|h)", re.IGNORECASE)


def parse_duration(s):
    """Parse a composite duration string into (relativedelta, human_label).

    Accepts any combination of Ny / Nmo / Nm / Nd / Nh in any order, with
    optional whitespace. Returns a relativedelta (which handles years,
    months, days, and hours uniformly) plus a short label like '1y 2mo 15d'.
    """
    raw = str(s).strip().lower()
    if not raw:
        raise ValueError("Empty duration.")

    matches = list(_PART_RE.finditer(raw))
    if not matches:
        raise ValueError(
            f"Bad duration: {s!r}. "
            "Use Ny, Nmo, Nd, Nh (e.g. '1y 6mo', '90d', '36h')."
        )

    # Validate full coverage: every character (ignoring whitespace) must
    # belong to a match, otherwise the input had garbage in it.
    covered = "".join(m.group(0) for m in matches)
    if re.sub(r"\s+", "", covered) != re.sub(r"\s+", "", raw):
        raise ValueError(
            f"Bad duration: {s!r}. Unparseable characters present."
        )

    totals = {"y": 0, "mo": 0, "d": 0, "h": 0}
    for m in matches:
        n, unit = int(m.group(1)), m.group(2).lower()
        if unit == "m":           # 'm' alone means months
            unit = "mo"
        totals[unit] += n

    if all(v == 0 for v in totals.values()):
        raise ValueError(
            f"Bad duration: {s!r}. At least one unit must be non-zero."
        )

    delta = relativedelta(
        years=totals["y"],
        months=totals["mo"],
        days=totals["d"],
        hours=totals["h"],
    )

    label_parts = []
    if totals["y"]:  label_parts.append(f"{totals['y']}y")
    if totals["mo"]: label_parts.append(f"{totals['mo']}mo")
    if totals["d"]:  label_parts.append(f"{totals['d']}d")
    if totals["h"]:  label_parts.append(f"{totals['h']}h")
    return delta, " ".join(label_parts)


# ---------- auth ----------

def get_spotipy_client():
    auth = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPES,
        cache_path=".spotify_token_cache",
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth)


def auth_headers(sp):
    """Return fresh auth headers, letting spotipy handle token refresh."""
    token = sp.auth_manager.get_access_token(as_dict=False)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ---------- raw Spotify API calls (post Feb 2026 migration) ----------
# spotipy still uses the deprecated /tracks endpoint, so we make raw calls
# to /items with the renamed fields.

def fetch_all_items(sp, playlist_id):
    """All items in a playlist. Uses /items endpoint; returns dicts with
    `added_at` and a track-like object under whichever of `item`/`track` is
    present (Feb 2026 renamed it to `item`)."""
    items = []
    fields = ("items(added_at,"
              "item(uri,name,artists(name),is_local),"
              "track(uri,name,artists(name),is_local)),next")
    url = (f"{API}/playlists/{playlist_id}/items"
           f"?limit=100&fields={requests.utils.quote(fields, safe='(),')}")
    while url:
        r = requests.get(url, headers=auth_headers(sp), timeout=30)
        if not r.ok:
            raise RuntimeError(f"GET {url} -> {r.status_code} {r.text}")
        data = r.json()
        items.extend(data["items"])
        url = data.get("next")
    return items


def select_old(items, cutoff):
    """Items older than cutoff. Skips local files and null tracks."""
    old = []
    for it in items:
        track = it.get("item") or it.get("track")  # Feb 2026 rename
        if not track or track.get("is_local") or not track.get("uri"):
            continue
        added_at = datetime.fromisoformat(it["added_at"].replace("Z", "+00:00"))
        if added_at < cutoff:
            old.append({
                "uri":      track["uri"],
                "name":     track["name"],
                "artist":   ", ".join(a["name"] for a in track["artists"]),
                "added_at": added_at,
            })
    return old


def add_to_playlist(sp, playlist_id, uris):
    for batch in chunked(uris, 100):
        r = requests.post(
            f"{API}/playlists/{playlist_id}/items",
            headers=auth_headers(sp),
            json={"uris": batch},
            timeout=30,
        )
        if not r.ok:
            raise RuntimeError(f"add -> {r.status_code} {r.text}")


def remove_from_playlist(sp, playlist_id, uris):
    for batch in chunked(uris, 100):
        r = requests.delete(
            f"{API}/playlists/{playlist_id}/items",
            headers=auth_headers(sp),
            # Feb 2026: body param renamed `tracks` -> `items`
            json={"items": [{"uri": uri} for uri in batch]},
            timeout=30,
        )
        if not r.ok:
            raise RuntimeError(f"remove -> {r.status_code} {r.text}")


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


# ---------- one rotation pass ----------

def rotate_between(sp, source_id, dest_id, threshold, label, apply_changes):
    """Move items older than `threshold` from source to dest. Returns count."""
    delta, human = parse_duration(threshold)
    print(f"\n=== {label} (threshold: {human}) ===")
    cutoff = datetime.now(timezone.utc) - delta
    print(f"Cutoff: tracks added before {cutoff.date()}.")

    source_items = fetch_all_items(sp, source_id)
    print(f"Source playlist has {len(source_items)} tracks.")

    old = select_old(source_items, cutoff)
    print(f"Found {len(old)} eligible tracks.")

    if not old:
        return 0

    for t in old[:15]:
        print(f"  {t['added_at'].date()}  {t['artist']} - {t['name']}")
    if len(old) > 15:
        print(f"  ... and {len(old) - 15} more")

    if not apply_changes:
        return len(old)

    # De-dupe URIs already present in the destination.
    existing = set()
    for it in fetch_all_items(sp, dest_id):
        track = it.get("item") or it.get("track")
        if track and track.get("uri"):
            existing.add(track["uri"])

    to_add    = [t["uri"] for t in old if t["uri"] not in existing]
    to_remove = [t["uri"] for t in old]

    print(f"Adding {len(to_add)} tracks to destination "
          f"({len(old) - len(to_add)} already there)...")
    add_to_playlist(sp, dest_id, to_add)

    print(f"Removing {len(to_remove)} tracks from source...")
    remove_from_playlist(sp, source_id, to_remove)

    return len(old)


# ---------- entry point ----------

def main():
    parser = argparse.ArgumentParser(
        description="Rotate Spotify playlist tracks both ways.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Durations combine units: Ny years, Nmo months, Nd days, "
                "Nh hours. Examples: '1y', '6mo', '180d', '1y 2mo 15d', '36h'."),
    )
    parser.add_argument("--apply", action="store_true",
                        help="Actually perform moves. Without this it's a dry run.")
    parser.add_argument("--only", choices=["rotate", "revive"],
                        help="Run only one direction. Default runs both.")
    parser.add_argument("--rotate", default=ROTATE_THRESHOLD, metavar="DURATION",
                        help=f"Time in primary before rotating out "
                             f"(default {ROTATE_THRESHOLD}).")
    parser.add_argument("--revive", default=REVIVE_THRESHOLD, metavar="DURATION",
                        help=f"Time in holding before reviving "
                             f"(default {REVIVE_THRESHOLD}).")
    args = parser.parse_args()

    if "YOUR_" in (CLIENT_ID + CLIENT_SECRET
                   + PRIMARY_PLAYLIST_ID + HOLDING_PLAYLIST_ID):
        sys.exit("Fill in the CONFIG section at the top of the script first.")

    # Validate durations up front so we fail before opening a browser.
    try:
        parse_duration(args.rotate)
        parse_duration(args.revive)
    except ValueError as e:
        sys.exit(str(e))

    sp = get_spotipy_client()

    if args.only != "revive":
        rotate_between(
            sp,
            source_id=PRIMARY_PLAYLIST_ID,
            dest_id=HOLDING_PLAYLIST_ID,
            threshold=args.rotate,
            label="ROTATE OUT (primary -> holding)",
            apply_changes=args.apply,
        )

    if args.only != "rotate":
        rotate_between(
            sp,
            source_id=HOLDING_PLAYLIST_ID,
            dest_id=PRIMARY_PLAYLIST_ID,
            threshold=args.revive,
            label="REVIVE (holding -> primary)",
            apply_changes=args.apply,
        )

    if not args.apply:
        print("\nDry run. Re-run with --apply to actually move tracks.")
    else:
        print("\nDone.")


if __name__ == "__main__":
    main()
