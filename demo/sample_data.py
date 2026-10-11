"""Sample data for the static demo: real Steam games, made-up players.

Nothing here comes from a real account. Hours, achievements, reviews and
comments are invented for the demo; only the Steam app IDs, names and genres
are real, so the covers load from Steam. Times are stored as "this long ago"
so the demo always looks recent.
"""
import random

USERS = ["Jacob", "Mara", "Theo", "Sam", "Nico", "Jules", "Ava", "Kai"]  # Jacob is the demo player

GAMES = [
    (1145360, "Hades", "Action,Indie,RPG"),
    (1245620, "ELDEN RING", "Action,RPG"),
    (1086940, "Baldur's Gate 3", "Adventure,RPG,Strategy"),
    (367520, "Hollow Knight", "Action,Adventure,Indie"),
    (413150, "Stardew Valley", "Indie,RPG,Simulation"),
    (504230, "Celeste", "Action,Adventure,Indie"),
    (620, "Portal 2", "Action,Adventure"),
    (105600, "Terraria", "Action,Adventure,Indie,RPG"),
    (632470, "Disco Elysium - The Final Cut", "RPG"),
    (646570, "Slay the Spire", "Indie,Strategy"),
    (753640, "Outer Wilds", "Action,Adventure"),
    (1091500, "Cyberpunk 2077", "RPG"),
    (292030, "The Witcher 3: Wild Hunt", "RPG"),
    (588650, "Dead Cells", "Action,Indie"),
    (294100, "RimWorld", "Indie,Simulation,Strategy"),
    (427520, "Factorio", "Casual,Indie,Simulation,Strategy"),
    (1794680, "Vampire Survivors", "Action,Casual,Indie,RPG"),
    (2379780, "Balatro", "Casual,Indie,Strategy"),
    (814380, "Sekiro: Shadows Die Twice", "Action,Adventure"),
    (1174180, "Red Dead Redemption 2", "Action,Adventure"),
    (548430, "Deep Rock Galactic", "Action,Indie"),
    (1092790, "Inscryption", "Adventure,Indie,RPG,Strategy"),
    (1817230, "Hi-Fi RUSH", "Action"),
    (1687950, "Persona 5 Royal", "RPG"),
    (1966720, "Lethal Company", "Action,Adventure,Indie"),
    (553850, "HELLDIVERS 2", "Action"),
    (730, "Counter-Strike 2", "Action,Free To Play"),
    (632360, "Risk of Rain 2", "Action,Indie"),
    (264710, "Subnautica", "Adventure,Indie"),
    (268910, "Cuphead", "Action,Indie"),
    (391540, "Undertale", "Indie,RPG"),
    (1671210, "DELTARUNE", "Indie,RPG"),
    (553420, "TUNIC", "Action,Adventure,Indie"),
    (2231450, "Pizza Tower", "Action,Indie"),
    (1627720, "Lies of P", "Action,Adventure,RPG"),
    (1888160, "ARMORED CORE VI FIRES OF RUBICON", "Action"),
    (1364780, "Street Fighter 6", "Action,Sports"),
    (1778820, "TEKKEN 8", "Action,Sports"),
    (945360, "Among Us", "Casual"),
    (250900, "The Binding of Isaac: Rebirth", "Action,Indie"),
    (220, "Half-Life 2", "Action"),
    (252950, "Rocket League", "Action,Free To Play,Racing,Sports"),
]
SOFTWARE = [
    (365670, "Blender", "Animation & Modeling,Design & Illustration"),
    (431960, "Wallpaper Engine", "Casual,Indie"),
    (431730, "Aseprite", "Animation & Modeling,Design & Illustration"),
]

# Jacob's library: (name, hours, achievements unlocked, achievements total). None means no achievements.
LIBRARY = [
    ("Hades", 214.5, 41, 49), ("ELDEN RING", 168.2, 33, 42), ("Baldur's Gate 3", 141.0, 28, 54),
    ("Hollow Knight", 96.4, 47, 63), ("Stardew Valley", 88.1, 20, 40), ("Terraria", 74.9, 51, 115),
    ("Celeste", 52.3, 31, 31), ("Slay the Spire", 61.7, 29, 46), ("Factorio", 120.6, None, None),
    ("Deep Rock Galactic", 47.2, 44, 102), ("Dead Cells", 45.2, 19, 58), ("Outer Wilds", 26.8, 22, 31),
    ("Disco Elysium - The Final Cut", 38.5, 21, 45), ("Balatro", 33.0, 15, 30), ("Inscryption", 21.4, 18, 23),
    ("Vampire Survivors", 29.9, 140, 233), ("Cyberpunk 2077", 58.3, 30, 57), ("Risk of Rain 2", 40.6, 52, 70),
    ("Hi-Fi RUSH", 18.7, 25, 50), ("TUNIC", 16.2, 14, 23), ("DELTARUNE", 22.6, None, None),
    ("Counter-Strike 2", 63.1, 1, 1), ("Lies of P", 0.0, 0, 50), ("Persona 5 Royal", 0.0, 0, 52),
    ("Sekiro: Shadows Die Twice", 0.0, 0, 34), ("Pizza Tower", 9.4, 12, 48), ("Undertale", 14.8, None, None),
    ("Blender", 36.0, None, None), ("Wallpaper Engine", 412.0, None, None),
]

# (author, game, stars, text, days ago). Jacob's own reviews are included.
REVIEWS = [
    ("Mara", "Hades", 5.0, "Every run teaches you something. I stopped counting deaths around 60 and started counting what each god's boons did together. The writing keeps paying off long after the credits.", 1),
    ("Theo", "Hollow Knight", 4.5, "Hard in the fair way. The map is half the fun once you stop fighting it.", 2),
    ("Sam", "Baldur's Gate 3", 5.0, "Played a bard who talked his way out of three boss fights. It let me. That's the review.", 3),
    ("Nico", "Slay the Spire", 4.0, "Still my default one-more-run game. Silent is the best character and I will not be taking questions.", 4),
    ("Jules", "Outer Wilds", 5.0, "Go in blind. Don't read anything else, including this.", 5),
    ("Theo", "Celeste", 4.5, "The assist mode respect is real. B-sides broke me in a good way.", 6),
    ("Mara", "Balatro", 4.0, "I understand poker less now than before I started, and I've never had more fun losing.", 7),
    ("Ava", "ELDEN RING", 4.5, None, 8),
    ("Nico", "Stardew Valley", 3.5, "Cozy until you realize you've planned your whole week around a fishing festival.", 9),
    ("Jules", "Disco Elysium - The Final Cut", 5.0, "The best writing I've seen in a game. Failing a check is often better than passing it.", 10),
    ("Kai", "Factorio", 4.0, "Lost a weekend to a belt of green circuits. Would lose another.", 11),
    ("Ava", "Deep Rock Galactic", 4.5, "Rock and stone. Best co-op crew I've found online, and the dwarves are funnier than they have any right to be.", 12),
    ("Kai", "Risk of Rain 2", 4.0, "Starts slow, ends with the screen on fire. Play it with friends.", 13),
    ("Sam", "Cyberpunk 2077", 4.0, "Came back after the big update and it's the game I wanted at launch. Night City at 3am is something.", 14),
    ("Mara", "Inscryption", 4.5, "I can't tell you anything about it without ruining it. Just play the first hour.", 16),
    ("Theo", "Dead Cells", 4.0, "Tight, fast, and the boss cell ramp is perfect. I bounce off every other roguelite that tries this.", 17),
    ("Jules", "TUNIC", 4.5, "The manual pages are the best collectible in any game. It respects you enough to let you be confused.", 19),
    ("Nico", "Hi-Fi RUSH", 4.0, "Everything hits on the beat, including the enemies. It made me better at rhythm games by accident.", 21),
    ("Ava", "Vampire Survivors", 3.5, "Five dollars for a hundred hours of numbers going up. No regrets, some concern.", 23),
    ("Kai", "Terraria", 4.5, "Every update adds another hundred hours. The bosses still surprise me.", 26),
    ("Sam", "Lies of P", 4.0, "A soulslike that knows exactly what it is. The weapon assembly system is great.", 28),
    ("Jacob", "Celeste", 5.0, "Finished every B-side and a few C-sides. The soundtrack carried me through the bad nights.", 15),
    ("Jacob", "Dead Cells", 4.0, "Five boss cells done. The run where everything clicks is one of the best feelings in games.", 30),
]

# (review index above, author, text, hours after the review).
COMMENTS = [
    (0, "Theo", "Same. The side stories got me more than the main plot.", 5),
    (0, "Jacob", "What weapon did it click with for you? Rail was my turning point.", 7),
    (0, "Mara", "Shield. Rail too, eventually.", 9),
    (1, "Nico", "The map thing is so true. Buying the quill changed everything.", 4),
    (4, "Theo", "Seconding the go-in-blind part.", 6),
    (6, "Kai", "Plasma deck changed how I play it.", 3),
    (2, "Jules", "The bard route is the correct route.", 8),
    (21, "Mara", "C-sides are a different game. Congrats.", 11),
]

# For You in the demo: cards that describe what the real feed shows, not invented news.
# The feed drops repeated links, so each card points at its own README section.
NEWS = [
    {"title": "Steam announcements for the games people here play",
     "summary": "Sample card. In the real app, For you pulls developer announcements for the games most played on PlayGraph and refreshes them every half hour.",
     "image_appid": 1145360, "minutes_ago": 40, "anchor": "readme"},
    {"title": "Headlines from PC gaming sites, newest first",
     "summary": "Sample card. With a news API key set, the real feed mixes in the latest stories from PC Gamer, Eurogamer and Rock Paper Shotgun.",
     "image_appid": 1245620, "minutes_ago": 180, "anchor": "what-it-does"},
    {"title": "Release calendar and trending games are next",
     "summary": "Sample card. Both sit in the side column as in development. They are the next pieces of the For you page.",
     "image_appid": 1086940, "minutes_ago": 60 * 26, "anchor": "how-it-works"},
]


def export() -> dict:
    """Everything the stand-in API needs, as plain JSON-ready data."""
    rng = random.Random(7)
    games = []
    for index, (appid, name, genres) in enumerate(GAMES + SOFTWARE, 1):
        games.append({"id": index, "steam_appid": appid, "name": name, "genres": genres,
                      "content_kind": "software" if (appid, name, genres) in SOFTWARE else "game"})
    by_name = {game["name"]: game for game in games}
    users = [{"id": index, "display_name": name} for index, name in enumerate(USERS, 1)]
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
                        "verified_achievement_pct": round(pct, 1) if pct is not None else None})

    comments = []
    for index, (review_index, author, text, hours_after) in enumerate(COMMENTS, 1):
        review = reviews[review_index]
        comments.append({"id": index, "review_id": review["id"], "user_id": user_id[author], "body": text,
                         "minutes_ago": max(5, review["minutes_ago"] - hours_after * 60)})

    return {"users": users, "games": games, "library": library, "reviews": reviews,
            "comments": comments, "news": NEWS}
