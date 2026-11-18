"""Sample data for the static demo: real Steam games, made-up players.

Nothing here comes from a real account. Hours, achievements, reviews and
comments are invented for the demo; only the Steam app IDs, names and genres
are real, so the covers load from Steam. Times are stored as "this long ago"
so the demo always looks recent.
"""
import random
from datetime import datetime, timedelta, timezone

from app.replay import build_replay

USERS = ["Jacob", "Mara", "Theo", "Sam", "Nico", "Jules", "Ava", "Kai"]  # Jacob is the demo player
# Made-up @handles and bios for the made-up players.
HANDLES = ["jacob", "mara_plays", "theo_speedruns", "samjrpg", "nico_tactics", "jules", "ava_100pct", "kai_after_dark"]
BIOS = ["Demo player. Stylish action, Metal Gear and Final Fantasy. SSS or nothing.",
        "Platinum hunter. If it has a trophy list, I am finishing it.",
        "Speedrunner. Character action, mostly Devil May Cry.", "JRPGs and long stories. Eorzea is home.",
        "Tactics and strategy. Turn order is everything.", "Cozy games and co-op.",
        "Achievement completionist.", "Horror at night, puzzles by day."]
# The demo player's pinned games, by name. The first wears the crown.
FAVORITES = ["Devil May Cry 3 Special Edition", "METAL GEAR SOLID 3: Snake Eater - Master Collection Version",
             "FINAL FANTASY XIV Online", "FINAL FANTASY VII REMAKE INTERGRADE"]

GAMES = [
    (6550, "Devil May Cry 3 Special Edition", "Action"),
    (39210, "FINAL FANTASY XIV Online", "Massively Multiplayer,RPG"),
    (287700, "METAL GEAR SOLID V: THE PHANTOM PAIN", "Action,Adventure"),
    (601150, "Devil May Cry 5", "Action"),
    (2131650, "METAL GEAR SOLID 3: Snake Eater - Master Collection Version", "Action"),
    (1462040, "FINAL FANTASY VII REMAKE INTERGRADE", "Action,Adventure,RPG"),
    (2515020, "FINAL FANTASY XVI", "Action,RPG"),
    (359870, "FINAL FANTASY X/X-2 HD Remaster", "RPG"),
    (2909400, "FINAL FANTASY VII REBIRTH", "Action,Adventure,RPG"),
    (631510, "Devil May Cry HD Collection", "Action"),
    (2131640, "METAL GEAR SOLID 2: Sons of Liberty - Master Collection Version", "Action"),
    (235460, "METAL GEAR RISING: REVENGEANCE", "Action"),
    (2131630, "METAL GEAR SOLID - Master Collection Version", "Action"),
    (1245620, "ELDEN RING", "Action,RPG"),
    (814380, "Sekiro: Shadows Die Twice", "Action,Adventure"),
    (524220, "NieR:Automata", "Action,RPG"),
    (460790, "Bayonetta", "Action,Adventure"),
    (2552430, "KINGDOM HEARTS -HD 1.5+2.5 ReMIX-", "Action,Adventure,RPG"),
    (377840, "FINAL FANTASY IX", "RPG"),
    (637650, "FINAL FANTASY XV WINDOWS EDITION", "RPG"),
    (2417610, "METAL GEAR SOLID Delta: SNAKE EATER", "Action,Adventure"),
    (2050650, "Resident Evil 4", "Action,Adventure"),
    (1364780, "Street Fighter 6", "Action,Sports"),
    (1778820, "TEKKEN 8", "Action,Sports"),
    (1687950, "Persona 5 Royal", "RPG"),
    (2358720, "Black Myth: Wukong", "Action,Adventure,RPG"),
    (1026680, "FINAL FANTASY VIII - REMASTERED", "Adventure,RPG"),
    # Games other players review. Two are on Jacob's backlog in the demo.
    (582010, "Monster Hunter: World", "Action"),
    (883710, "Resident Evil 2", "Action"),
    (1086940, "Baldur's Gate 3", "Adventure,RPG,Strategy"),
    (1091500, "Cyberpunk 2077", "RPG"),
    (292030, "The Witcher 3: Wild Hunt", "RPG"),
    (1174180, "Red Dead Redemption 2", "Action,Adventure"),
    (1196590, "Resident Evil Village", "Action"),
    (1888160, "ARMORED CORE VI FIRES OF RUBICON", "Action"),
    (1627720, "Lies of P", "Action,Adventure,RPG"),
    (1145360, "Hades", "Action,Indie,RPG"),
    (367520, "Hollow Knight", "Action,Adventure,Indie"),
    (730, "Counter-Strike 2", "Action,Free To Play"),
]
SOFTWARE = [
    (365670, "Blender", "Animation & Modeling,Design & Illustration"),
    (431960, "Wallpaper Engine", "Casual,Indie"),
    (431730, "Aseprite", "Animation & Modeling,Design & Illustration"),
]

# Jacob's library: (name, hours, achievements unlocked, achievements total). None means no achievements.
LIBRARY = [
    ("Devil May Cry 3 Special Edition", 612.4, None, None), ("FINAL FANTASY XIV Online", 488.7, None, None),
    ("METAL GEAR SOLID V: THE PHANTOM PAIN", 241.3, 38, 43), ("Devil May Cry 5", 198.6, 49, 53),
    ("METAL GEAR SOLID 3: Snake Eater - Master Collection Version", 156.2, 37, 44),
    ("FINAL FANTASY VII REMAKE INTERGRADE", 132.5, 54, 58), ("FINAL FANTASY XVI", 118.9, 46, 51),
    ("FINAL FANTASY X/X-2 HD Remaster", 104.0, 52, 71), ("FINAL FANTASY VII REBIRTH", 96.4, 33, 57),
    ("Devil May Cry HD Collection", 88.1, 31, 40), ("KINGDOM HEARTS -HD 1.5+2.5 ReMIX-", 77.3, 40, 67),
    ("METAL GEAR SOLID 2: Sons of Liberty - Master Collection Version", 72.6, 31, 40),
    ("METAL GEAR RISING: REVENGEANCE", 69.8, 41, 48), ("ELDEN RING", 64.0, 25, 42),
    ("FINAL FANTASY IX", 61.0, 19, 48), ("NieR:Automata", 58.7, 30, 46), ("FINAL FANTASY XV WINDOWS EDITION", 54.4, 31, 42),
    ("METAL GEAR SOLID - Master Collection Version", 51.2, 22, 26), ("Sekiro: Shadows Die Twice", 45.5, 20, 34),
    ("Bayonetta", 41.2, 35, 52), ("METAL GEAR SOLID Delta: SNAKE EATER", 38.6, 20, 41),
    ("Resident Evil 4", 33.1, 28, 40), ("Street Fighter 6", 27.9, 18, 50), ("TEKKEN 8", 19.4, 12, 46),
    ("Persona 5 Royal", 0.0, 0, 52), ("Black Myth: Wukong", 0.0, 0, 81), ("FINAL FANTASY VIII - REMASTERED", 0.0, 0, 39),
    ("Blender", 36.0, None, None), ("Wallpaper Engine", 412.0, None, None),
]

# (author, game, stars, text, days ago). Jacob's own reviews are included.
REVIEWS = [
    ("Theo", "Devil May Cry 3 Special Edition", 5.0, "Still the best combat system in the genre. Trickster plus a jump cancel and the whole game opens up. Dante Must Die is the real game.", 1),
    ("Mara", "METAL GEAR SOLID 3: Snake Eater - Master Collection Version", 5.0, "The ladder. The Sorrow. The End dying of old age if you save and wait a week. Nothing else does this.", 2),
    ("Sam", "FINAL FANTASY XIV Online", 5.0, "Came for the free trial, stayed for Shadowbringers. The writing in the expansions is some of the best in the series.", 3),
    ("Nico", "FINAL FANTASY VII REMAKE INTERGRADE", 4.5, "The combat mixes action and ATB better than I thought anyone could. Hard mode turns it into a puzzle.", 4),
    ("Jules", "NieR:Automata", 5.0, "Play past the first ending. Then keep going. Trust me.", 5),
    ("Theo", "Bayonetta", 4.5, "Witch Time is the most satisfying dodge in any game. Pure style.", 6),
    ("Mara", "METAL GEAR SOLID V: THE PHANTOM PAIN", 4.5, "The best sandbox stealth ever made, with half a story. I'll take it.", 7),
    ("Ava", "ELDEN RING", 4.5, None, 8),
    ("Sam", "FINAL FANTASY X/X-2 HD Remaster", 4.0, "Blitzball is still a crime. Everything else holds up, and the ending still gets me.", 9),
    ("Jules", "KINGDOM HEARTS -HD 1.5+2.5 ReMIX-", 4.0, "The story makes no sense and I love every minute of it. KH2 Final Mix combat is a treat.", 10),
    ("Kai", "Resident Evil 4", 4.5, "The remake keeps the best parts and fixes the rest. Parrying a chainsaw never gets old.", 11),
    ("Ava", "METAL GEAR RISING: REVENGEANCE", 4.5, "Rules of Nature plays and you are not in control of yourself anymore. Short, loud, perfect.", 12),
    ("Nico", "FINAL FANTASY XVI", 4.0, "Eikon fights are the biggest spectacle I've played. Side quests drag, the main path doesn't.", 13),
    ("Sam", "FINAL FANTASY VII REBIRTH", 4.5, "The open world is packed and the party banter is wonderful. Queen's Blood ate twenty hours.", 14),
    ("Theo", "Devil May Cry 5", 5.0, "V is a weird delight, Nero is fun, and Dante's toolbox is endless. Bury the Light still hits.", 16),
    ("Kai", "Resident Evil 2", 4.5, "Mr. X following you around the station is peak horror design.", 17),
    ("Mara", "METAL GEAR SOLID Delta: SNAKE EATER", 4.0, "A faithful remake that looks incredible. I still prefer the original camera, but this is lovely.", 19),
    ("Nico", "Sekiro: Shadows Die Twice", 4.5, "Deflect, deflect, deflect. When it clicks it feels like a rhythm game with a sword.", 21),
    ("Ava", "FINAL FANTASY IX", 4.5, "The most charming Final Fantasy. Vivi deserved better and so did all of us.", 23),
    ("Kai", "Monster Hunter: World", 4.0, "Built a whole weapon around one Rathalos fight. The loop is dangerously good.", 26),
    ("Jules", "Hades", 4.5, "Not usually my kind of game, but the writing kept me going for every run.", 28),
    ("Jacob", "Devil May Cry 3 Special Edition", 5.0, "Six hundred hours in and I'm still finding new cancels. Vergil 3 is the best boss fight ever made. SSS every time.", 15),
    ("Jacob", "METAL GEAR SOLID 3: Snake Eater - Master Collection Version", 5.0, "The best Metal Gear. The final fight in the flowers is perfect, and the ladder climb still gets me.", 30),
]

# (review index above, author, text, hours after the review).
COMMENTS = [
    (0, "Nico", "Dante Must Die broke me. Respect.", 5),
    (0, "Jacob", "Which style did it click with for you? Royal Guard was my turning point.", 7),
    (0, "Theo", "Trickster all day. Royal Guard is for the brave.", 9),
    (1, "Sam", "The End segment is still the most unique boss in gaming.", 4),
    (4, "Mara", "Seconding this. Ending E made me cry.", 6),
    (6, "Kai", "Mission 43 still haunts me.", 3),
    (2, "Jules", "Shadowbringers is the one. Endwalker too.", 8),
    (21, "Mara", "Vergil 3 on DMD is a rite of passage. Congrats.", 11),
]

# For You in the demo: cards that describe what the real feed shows, not invented news.
# The feed drops repeated links, so each card points at its own README section.
NEWS = [
    {"title": "Steam announcements for the games people here play",
     "summary": "Sample card. In the real app, For you pulls developer announcements for the games most played on PlayGraph and refreshes them every half hour.",
     "image_appid": 6550, "minutes_ago": 40, "anchor": "readme"},
    {"title": "Headlines from PC gaming sites, newest first",
     "summary": "Sample card. With a news API key set, the real feed mixes in the latest stories from PC Gamer, Eurogamer and Rock Paper Shotgun.",
     "image_appid": 39210, "minutes_ago": 180, "anchor": "what-it-does"},
    {"title": "Release calendar and trending games are next",
     "summary": "Sample card. Both sit in the side column as in development. They are the next pieces of the For you page.",
     "image_appid": 287700, "minutes_ago": 60 * 26, "anchor": "how-it-works"},
]


def export() -> dict:
    """Everything the stand-in API needs, as plain JSON-ready data."""
    rng = random.Random(7)
    games = []
    for index, (appid, name, genres) in enumerate(GAMES + SOFTWARE, 1):
        games.append({"id": index, "steam_appid": appid, "name": name, "genres": genres,
                      "content_kind": "software" if (appid, name, genres) in SOFTWARE else "game"})
    by_name = {game["name"]: game for game in games}
    users = [{"id": index, "display_name": name, "handle": HANDLES[index - 1], "bio": BIOS[index - 1],
              "has_steam": name != "Jules"}
             for index, name in enumerate(USERS, 1)]
    user_id = {user["display_name"]: user["id"] for user in users}

    library = []
    for name, hours, unlocked, total in LIBRARY:
        library.append({"game_id": by_name[name]["id"], "playtime_minutes": round(hours * 60),
                        "achievements_unlocked": unlocked, "achievements_total": total})
    owned = {entry["game_id"]: entry for entry in library}

    reviews = []
    for index, (author, name, stars, text, days_ago) in enumerate(REVIEWS, 1):
        game = by_name[name]
        mine = owned.get(game["id"]) if author == "Jacob" else None
        if author == "Jacob" and mine:
            minutes = mine["playtime_minutes"]
            pct = (100 * mine["achievements_unlocked"] / mine["achievements_total"]
                   if mine["achievements_total"] else None)
        elif rng.random() < 0.85:
            minutes = rng.randint(8, 220) * 60 + rng.randint(0, 59)
            pct = float(rng.randint(12, 100)) if rng.random() < 0.8 else None
        else:
            minutes, pct = None, None
        reviews.append({"id": index, "user_id": user_id[author], "game_id": game["id"], "rating": stars, "body": text,
                        "minutes_ago": days_ago * 24 * 60 + rng.randint(0, 600),
                        "verified_playtime_minutes": minutes,
                        "verified_achievement_pct": round(pct, 1) if pct is not None else None,
                        # Synthetic, like everything here: how many other players liked it.
                        "like_count": rng.choice([0, 1, 2, 3, 5, 8, 12, 19, 27, 41, 66, 128, 1240])})

    # Synthetic receipts make each badge tier visible without storing a badge.
    for author, name, hours in (("Mara", "METAL GEAR SOLID 3: Snake Eater - Master Collection Version", 120),
                                ("Theo", "Devil May Cry 3 Special Edition", 1050),
                                ("Sam", "FINAL FANTASY XIV Online", 5050)):
        next(row for row in reviews if row["user_id"] == user_id[author]
             and row["game_id"] == by_name[name]["id"])["verified_playtime_minutes"] = hours * 60

    comments = []
    for index, (review_index, author, text, hours_after) in enumerate(COMMENTS, 1):
        review = reviews[review_index]
        comments.append({"id": index, "review_id": review["id"], "user_id": user_id[author], "body": text,
                         "minutes_ago": max(5, review["minutes_ago"] - hours_after * 60)})

    favorites = [by_name[name]["id"] for name in FAVORITES if name in by_name]
    now = datetime.now(timezone.utc)
    captures = [datetime(now.year - 2, 12, 31, tzinfo=timezone.utc)]
    captures += [datetime(year, month, 1, tzinfo=timezone.utc)
        for year in (now.year - 1, now.year) for month in range(1, 13)
        if datetime(year, month, 1, tzinfo=timezone.utc) < now]
    snapshots = []
    for entry in library:
        for step, captured in enumerate(captures, 1):
            fraction = step / len(captures)
            snapshots.append({"id": len(snapshots) + 1, "game": games[entry["game_id"] - 1], "source": "steam",
                "captured_at": captured, "playtime_minutes": round(entry["playtime_minutes"] * fraction),
                "achievements_unlocked": round(entry["achievements_unlocked"] * fraction)
                    if entry["achievements_unlocked"] is not None else None})
    replay_reviews = [{"game": games[row["game_id"] - 1], "rating": row["rating"],
        "created_at": now - timedelta(minutes=row["minutes_ago"])} for row in reviews if row["user_id"] == 1]
    replays = {}
    for year in (now.year, now.year - 1):
        report = build_replay(year, snapshots, replay_reviews, now=now, first_sync=captures[0])
        report["available_years"] = [now.year, now.year - 1]
        report["as_of"] = report["as_of"].isoformat()
        report["tracked_since"] = report["tracked_since"].isoformat() if report["tracked_since"] else None
        replays[str(year)] = report
    # Who follows whom among the made-up players: everyone follows Mara; Jacob follows three.
    follows = [[u["id"], 2] for u in users if u["id"] != 2] + [[1, 3], [1, 5]] + [[u["id"], 1] for u in users[2:6]]
    importable = [{"id": 9001, "igdb_id": 900001, "name": "Moonlit Harbor", "steam_appid": None,
        "catalog_source": "igdb", "content_kind": "game", "genres": "Adventure,Indie",
        "header_image_url": None, "cover_url": None, "hero_url": None,
        "first_release_date": "2025-08-12T00:00:00Z", "summary": "A synthetic game for trying the add-game flow. No IGDB request is made in this preview.",
        "platforms": [{"slug": "win", "name": "PC", "abbreviation": "PC"}, {"slug": "switch", "name": "Nintendo Switch", "abbreviation": "Switch"}]}]
    return {"users": users, "games": games, "importable_games": importable, "library": library, "reviews": reviews,
            "comments": comments, "news": NEWS, "favorites": favorites, "follows": follows, "replays": replays}
