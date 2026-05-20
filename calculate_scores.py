"""
calculate_scores.py
World Cup 2026 Charity Buster — Automated Score Calculator
-----------------------------------------------------------
Fetches live match results from football-data.org and calculates
points for each participant based on their team picks.

Scoring rules:
  Match result  : Win = +3, Draw = +1 (group stage only), Loss = 0
  Stage bonus   : +3 for each round a team advances through
  Tournament win: +10 for winning the Final

Run by GitHub Actions every 2 hours during the tournament.
Output: scores.json (committed back to the repo automatically)
"""

import os
import json
import requests
from datetime import datetime, timezone

# ── API CONFIG ────────────────────────────────────────────────────────────────
API_KEY  = os.environ["FD_API_KEY"]          # Set in GitHub Secrets
BASE_URL = "https://api.football-data.org/v4"
HEADERS  = {"X-Auth-Token": API_KEY}
SEASON   = 2026

# ── PARTICIPANT PICKS ─────────────────────────────────────────────────────────
# Team names must match the normalised names in TEAM_NAME_MAP below.
# Add a new dict entry for each additional participant.
PARTICIPANTS = [
    {
        "name": "Denis O Connell",
        "teams": ["Spain", "Morocco", "Scotland", "Czech Republic", "France"]
    },
    {
        "name": "Jack O'Shea",
        "teams": ["France", "Morocco", "Norway", "Sweden", "France"]
    },
    {
        "name": "Tiger Woods",
        "teams": ["France", "South Korea", "Uzbekistan", "Turkey", "France"]
    },
]

# ── TEAM NAME NORMALISATION ───────────────────────────────────────────────────
# Maps football-data.org API team names → names used in PARTICIPANTS above.
# Extend this if the API returns unexpected names.
TEAM_NAME_MAP = {
    "United States": "United States",
    "USA":           "United States",
    "Côte d'Ivoire": "Ivory Coast",
    "Korea Republic":"South Korea",
    "Czechia":       "Czech Republic",
    "Bosnia and Herzegovina": "Bosnia and Herzegovina",
    "DR Congo":      "DR Congo",
    "Congo DR":      "DR Congo",
    "Cabo Verde":    "Cape Verde",
    "England":       "England",      # API uses "England" not "Great Britain"
}

def normalise(name: str) -> str:
    """Return a canonical team name, or the original if not in the map."""
    return TEAM_NAME_MAP.get(name, name)


# ── STAGE ORDERING ────────────────────────────────────────────────────────────
# Maps football-data.org stage strings to a numeric rank (higher = further).
STAGE_RANK = {
    "GROUP_STAGE":    1,
    "ROUND_OF_32":    2,
    "ROUND_OF_16":    3,
    "QUARTER_FINALS": 4,
    "SEMI_FINALS":    5,
    "FINAL":          6,
}

# Stage bonus points awarded when a team REACHES each stage
# (i.e. they won the previous round)
STAGE_BONUS = {
    "ROUND_OF_32":    3,   # qualified from group stage
    "ROUND_OF_16":    3,   # won Round of 32
    "QUARTER_FINALS": 3,   # won Round of 16
    "SEMI_FINALS":    3,   # won Quarter-Final
    "FINAL":          3,   # won Semi-Final
}
WINNER_BONUS = 10          # awarded to team that wins the Final


def fetch_matches() -> list:
    """Fetch all WC 2026 matches from football-data.org."""
    url    = f"{BASE_URL}/competitions/WC/matches"
    params = {"season": SEASON}
    resp   = requests.get(url, headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("matches", [])


def build_team_stats(matches: list) -> dict:
    """
    Build a per-team stats dict from match results.

    Returns:
        {
          "France": {
              "matchPts": 12,
              "gf": 8,
              "ga": 3,
              "stages": {"GROUP_STAGE", "ROUND_OF_32", "ROUND_OF_16"},
              "won_final": False
          }, ...
        }
    """
    stats = {}

    def ensure(team):
        if team not in stats:
            stats[team] = {"matchPts": 0, "gf": 0, "ga": 0,
                           "stages": set(), "won_final": False}

    for m in matches:
        stage   = m.get("stage", "")
        status  = m.get("status", "")
        home    = normalise(m["homeTeam"]["name"])
        away    = normalise(m["awayTeam"]["name"])
        score   = m.get("score", {})
        ft      = score.get("fullTime", {})
        hg      = ft.get("home")   # None if not played yet
        ag      = ft.get("away")

        ensure(home)
        ensure(away)

        # Record that both teams competed at this stage
        stats[home]["stages"].add(stage)
        stats[away]["stages"].add(stage)

        # Only score finished matches
        if status != "FINISHED" or hg is None or ag is None:
            continue

        # Goals
        stats[home]["gf"] += hg
        stats[home]["ga"] += ag
        stats[away]["gf"] += ag
        stats[away]["ga"] += hg

        # Match points
        is_knockout = stage != "GROUP_STAGE"

        if hg > ag:
            stats[home]["matchPts"] += 3
        elif ag > hg:
            stats[away]["matchPts"] += 3
        else:
            # Draw — only award point in group stage
            if not is_knockout:
                stats[home]["matchPts"] += 1
                stats[away]["matchPts"] += 1
            # In knockout, check extra time / penalties winner
            winner = score.get("winner")   # "HOME_TEAM" or "AWAY_TEAM"
            if winner == "HOME_TEAM":
                stats[home]["matchPts"] += 3
            elif winner == "AWAY_TEAM":
                stats[away]["matchPts"] += 3

        # Final winner bonus
        if stage == "FINAL":
            winner = score.get("winner")
            if winner == "HOME_TEAM":
                stats[home]["won_final"] = True
            elif winner == "AWAY_TEAM":
                stats[away]["won_final"] = True

    return stats


def calc_stage_points(team_stages: set, won_final: bool) -> int:
    """Calculate bonus points for stage progression."""
    pts = 0
    for stage, bonus in STAGE_BONUS.items():
        if stage in team_stages:
            pts += bonus
    if won_final:
        pts += WINNER_BONUS
    return pts


def calculate_participant_scores(team_stats: dict) -> list:
    """Calculate total scores for each participant."""
    results = []

    for p in PARTICIPANTS:
        total_match = 0
        total_stage = 0
        total_gf    = 0
        total_ga    = 0

        # Deduplicate teams (e.g. if wildcard == pot1 pick)
        seen = set()
        for team in p["teams"]:
            if team in seen:
                continue
            seen.add(team)

            s = team_stats.get(team)
            if not s:
                continue   # team hasn't played yet or name mismatch

            total_match += s["matchPts"]
            total_gf    += s["gf"]
            total_ga    += s["ga"]
            total_stage += calc_stage_points(s["stages"], s["won_final"])

        results.append({
            "name":      p["name"],
            "matchPts":  total_match,
            "stagePts":  total_stage,
            "gf":        total_gf,
            "ga":        total_ga,
        })

    return results


def main():
    print("⚽ Fetching WC 2026 match data...")
    try:
        matches = fetch_matches()
        print(f"   Found {len(matches)} matches")
    except requests.HTTPError as e:
        print(f"   ❌ API error: {e}")
        raise

    print("📊 Building team stats...")
    team_stats = build_team_stats(matches)

    print("🧮 Calculating participant scores...")
    scores = calculate_participant_scores(team_stats)

    output = {
        "lastUpdated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "participants": scores
    }

    with open("scores.json", "w") as f:
        json.dump(output, f, indent=2)

    print("✅ scores.json written:")
    for p in scores:
        total = p["matchPts"] + p["stagePts"]
        gd    = p["gf"] - p["ga"]
        print(f"   {p['name']:20s} | Match: {p['matchPts']:3d} | Stage: {p['stagePts']:3d} "
              f"| Total: {total:3d} | GD: {gd:+d}")


if __name__ == "__main__":
    main()
