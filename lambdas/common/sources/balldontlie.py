"""
NBA source adapter, reading balldontlie.

The only sport in the corpus behind a credential. Every keyless route was
checked and closed: `stats.nba.com` hangs for non-browser clients even with full
browser headers, `cdn.nba.com` returns 403, and Basketball-Reference prohibits
scraping and licenses through Sportradar. balldontlie's free tier is sufficient
and reaches back to 1946.

The key is read from SSM at `/today-in-sports/balldontlie/api-key`, with an
env-var override for local runs. It is deliberately not a Terraform variable:
an unset `TF_VAR_` resolves to an empty string, which plan and validate accept
silently and the AWS API then rejects at apply.

Why the NBA matters more than its raw volume suggests: it plays October to June,
which covers the exact weeks that remain empty after baseball, hockey, football
and motorsport. Coverage, not breadth.

Free-tier rate limits are low, so ingestion fetches a **season at a time**
through the cursor rather than a request per date, and throttles hard.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.balldontlie.io/v1"
UA = "today-in-sports/0.1 (dominickj.giordano@gmail.com)"
SOURCE_NAME = "balldontlie"

SSM_KEY_PATH = "/today-in-sports/balldontlie/api-key"

# The free tier allows FIVE requests per minute — confirmed from the response
# headers (`x-ratelimit-limit: 5`), not guessed. That is roughly one every
# twelve seconds, so a season (~1,320 games at 100 per page, ~14 requests) takes
# about three minutes and the full 1946-2025 backfill takes a few hours.
#
# That is fine: this is a one-time extraction of immutable history. It is not
# fine to discover the limit by hammering, which is why 429s are handled by
# honouring the server's own Retry-After rather than by backing off blindly.
THROTTLE_SECONDS = 12.5
MAX_RETRIES = 6
PER_PAGE = 100

# Ceiling on how long to obey a Retry-After, so a pathological value cannot
# stall a backfill indefinitely.
MAX_RETRY_AFTER_SECONDS = 120

POSTSEASON_LABEL = "Playoffs"


class SourceError(Exception):
    pass


class MissingCredentialError(SourceError):
    """Raised when no API key is configured — the one thing Claude cannot self-serve."""


_api_key = None


def api_key():
    """Env var first (local runs), then SSM (deployed and CI)."""
    global _api_key
    if _api_key:
        return _api_key

    env = os.environ.get("BALLDONTLIE_API_KEY")
    if env:
        _api_key = env.strip()
        return _api_key

    try:
        import boto3
        ssm = boto3.client("ssm")
        resp = ssm.get_parameter(Name=SSM_KEY_PATH, WithDecryption=True)
        _api_key = resp["Parameter"]["Value"].strip()
        return _api_key
    except Exception as e:
        raise MissingCredentialError(
            "no balldontlie API key. Set BALLDONTLIE_API_KEY, or store it at "
            f"{SSM_KEY_PATH} with:\n"
            f"  aws ssm put-parameter --name {SSM_KEY_PATH} "
            f"--type SecureString --value 'YOUR_KEY'"
        ) from e


def _get(path, params=None):
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)

    delay = 2.0
    for attempt in range(MAX_RETRIES):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Authorization": api_key(),
        })
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                data = json.load(r)
            time.sleep(THROTTLE_SECONDS)
            return data
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise MissingCredentialError(
                    "balldontlie rejected the API key (401)") from e

            if e.code == 429 and attempt < MAX_RETRIES - 1:
                # The server states exactly how long to wait. Obey it rather
                # than guessing — blind exponential backoff either wastes time
                # or keeps tripping the same limit.
                try:
                    wait = float(e.headers.get("retry-after", delay))
                except (TypeError, ValueError):
                    wait = delay
                time.sleep(min(wait, MAX_RETRY_AFTER_SECONDS) + 1)
                continue

            if e.code in (500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                time.sleep(delay)
                delay *= 2
                continue

            raise SourceError(f"HTTP {e.code} for {url}") from e
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise SourceError(f"{type(e).__name__} for {url}") from e
    raise SourceError(f"exhausted retries for {url}")


def normalize(game):
    home, away = game.get("home_team") or {}, game.get("visitor_team") or {}
    h_score, a_score = game.get("home_team_score"), game.get("visitor_team_score")
    postseason = bool(game.get("postseason"))

    def full_name(t):
        # The API splits city and nickname; some historical rows carry only one.
        name = (t.get("full_name") or "").strip()
        if name:
            return name
        parts = [t.get("city"), t.get("name")]
        return " ".join(p for p in parts if p).strip() or None

    def side(t, score, opp):
        return {
            "team": full_name(t),
            "teamId": t.get("abbreviation"),
            "league": "NBA",
            "leagueId": "NBA",
            "score": score,
            "isWinner": (score is not None and opp is not None and score > opp),
        }

    return {
        "sport": "nba",
        "gameId": game.get("id"),
        # balldontlie's `date` is an ISO timestamp; the local game date is the
        # leading date part, which is what "on this date" means.
        "gameDate": (game.get("date") or "")[:10],
        "season": game.get("season"),
        "gameType": POSTSEASON_LABEL if postseason else "Regular Season",
        "seriesDescription": POSTSEASON_LABEL if postseason else "Regular Season",
        "isPlayoff": postseason,
        "status": game.get("status"),
        "combinedPoints": (h_score + a_score)
                          if (h_score is not None and a_score is not None) else None,
        "margin": abs(h_score - a_score)
                  if (h_score is not None and a_score is not None) else None,
        "away": side(away, a_score, h_score),
        "home": side(home, h_score, a_score),
        "sourceName": SOURCE_NAME,
        "sourceDatasetRef": f"{API}/games/{game.get('id')}",
    }


def fetch_season(season, progress=None):
    """
    Every game in a season, following the cursor.

    `season` is the starting year: 1995 is the 1995-96 season.
    """
    out = []
    cursor = None

    while True:
        params = {"seasons[]": season, "per_page": PER_PAGE}
        if cursor:
            params["cursor"] = cursor

        payload = _get("games", params)
        for g in payload.get("data", []):
            game = normalize(g)
            if game["gameDate"] and game["home"]["score"] is not None:
                out.append(game)

        cursor = (payload.get("meta") or {}).get("next_cursor")
        if progress:
            progress(f"    season {season}: {len(out)} games")
        if not cursor:
            break

    return out


def is_final(game):
    status = (game.get("status") or "").strip().lower()
    return status in ("final", "") or status.startswith("final")
