"""Fill a database with synthetic demo data.

Purpose: a public demo instance that shows what PlayGraph does without any
real person's Steam account or reviews being involved. Every user, game,
review and comment below is invented. Playtime is written as append-only
snapshots, the same way the worker writes them, so the verified-hours badge
and the row_number() latest-snapshot query behave exactly as they do in
production.

Run against an EMPTY database that has already been migrated:

    python -m app.migrations upgrade
    python -m scripts.seed_demo

Refuses to run if the database already has users, so it can never scribble
over real data. It also creates the fixed demo player that POST /demo/login
signs in as (see app/demo.py).

The random generator has a fixed seed, so the same code always writes the
same games, ratings, review text and comments. Timestamps are placed relative
to the moment the seed runs (the last 60 days), so those differ by run.
"""

import random
import sys
from datetime import timedelta

from app.database import SessionLocal
from app.demo import DEMO_NAME, DEMO_STEAM_ID
from app.models import (
    Comment, Game, LinkedAccount, Platform, PlaytimeSnapshot, Review, SnapshotSource, User, utcnow,
)

SEED = 20260916

# Synthetic Steam app ids, deliberately outside the real range so demo rows can
# never be confused with a real catalog entry. The last two entries are
# software, kept so the Apps view and the software filter have something in them.
# Position matters: REVIEWS, DEMO_LIBRARY and the rest refer to games by index.
GAMES = [
    (9000001, "Hollow Ascent", "Action,Adventure,Metroidvania",
     "A lantern-lit climb through a flooded cathedral, with a map that opens up as you learn its moves."),
    (9000002, "Orbital Freight Co.", "Simulation,Strategy,Management",
     "Run a shipping firm between the rings of a gas giant. Routes, contracts and crew rotas all depend on each other."),
    (9000003, "Mire", "Horror,Survival",
     "Night falls over a drowned village and the fog starts drifting toward your camp. Supplies are short."),
    (9000004, "Duskfall Tactics", "Strategy,RPG,Turn-Based",
     "Squad tactics on hex maps where turn order is the real puzzle. Campaigns branch on who survives."),
    (9000005, "Neon Vector", "Action,Racing,Arcade",
     "Arcade racing on floating tracks, with time-trial ghosts and a soundtrack that keeps the speed up."),
    (9000006, "Quarry", "Simulation,Building,Casual",
     "Build a mining town one conveyor at a time. Balance ore, housing and a council that always wants more."),
    (9000007, "The Long Signal", "Adventure,Mystery,Story Rich",
     "A radio operator picks up a transmission that repeats every eleven minutes. Slow, deliberate, and patient."),
    (9000008, "Ironline", "Action,Shooter,Multiplayer",
     "Four-player co-op on a frontier railway, defending every station on the line from the raiders."),
    (9000009, "Garden of Forks", "Puzzle,Indie",
     "A set of puzzle gardens where the paths branch, loop back, and sometimes meet themselves."),
    (9000010, "Cold Harbor", "RPG,Open World,Survival",
     "Survive a northern port town through a long winter. Scavenge by day and keep the stove lit at night."),
    (9000013, "Lanternfall", "Action,Roguelike,Indie",
     "Short roguelike runs down a collapsing tower. Each floor hands you one odd item and one bad choice."),
    (9000014, "Paper Harbour", "Simulation,Casual,Indie",
     "A cozy harbour town you map by hand, one street and one ferry route at a time."),
    (9000015, "Static Choir", "Rhythm,Music,Indie",
     "A rhythm game where the beats come from a choir recorded in an empty church."),
    (9000016, "Tidewright", "Puzzle,Strategy,Indie",
     "Puzzles built around tide timing. The walls open and close with the water, and so should your plan."),
    (9000017, "Glasswing Rally", "Racing,Sports,Arcade",
     "Rally racing across glass-flat salt pans, where grip disappears without warning."),
    (9000018, "Bramble and Bone", "Adventure,Cozy,Indie",
     "A gentle walk through an overgrown kingdom, where the side quests are the main road."),
    (9000019, "Vault Keeper", "Strategy,Tower Defense,Casual",
     "Tower defense around a bank vault, on a budget that is never quite enough."),
    (9000020, "Summit Lines", "Sports,Simulation,Co-op",
     "Co-op mountaineering. Rope up with friends, pick a line, and watch the weather window."),
    (9000011, "Blender", "Animation & Modeling,Design & Illustration",
     "3D modelling, sculpting and animation software."),
    (9000012, "ShareX", "Utilities,Photo Editing",
     "Screen capture and file sharing utility."),
]

# (steam id, display name). Eight invented players, then the demo player, who
# must stay last: REVIEWS and COMMENTS refer to users by position.
USERS = [
    (76561190000000101, "Ava Bello"),
    (76561190000000102, "Marcus Ilic"),
    (76561190000000103, "Priya Raman"),
    (76561190000000104, "Devon Locke"),
    (76561190000000105, "Noor Haddad"),
    (76561190000000106, "Theo Lindqvist"),
    (76561190000000107, "Rosa Okafor"),
    (76561190000000108, "Jun Park"),
    (int(DEMO_STEAM_ID), DEMO_NAME),
]
DEMO = len(USERS) - 1

# The demo player's library: (game index, minutes, achievements total, unlocked).
# Three titles are owned but unplayed, and the rest span a few hours to a few
# hundred hours. Games with no achievements leave both counts as None.
DEMO_LIBRARY = [
    (0, 1620, 50, 41),
    (3, 4310, 36, 22),
    (1, 2580, None, None),
    (4, 610, 24, 9),
    (6, 840, 12, 12),
    (9, 9120, 50, 17),
    (10, 95, 36, 3),
    (12, 0, 30, 0),
    (13, 0, None, None),
    (14, 1880, 20, 20),
    (15, 260, None, None),
    (16, 0, 18, 0),
]

# (user index, game index, rating in half stars, review text). Every review
# is written by someone who owns the game, so the library and the reviews agree.
REVIEWS = [
    (0, 0, 4.5, "The traversal clicks about four hours in and then never stops being fun. "
                "Bosses are readable without being easy. A couple of late-game rooms lean on "
                "precision more than the movement really supports."),
    (1, 0, 3.5, "Gorgeous, and the map design is smart, but the back half reuses enemies hard enough "
                "that I felt it."),
    (0, 3, 5.0, "Best turn-based combat I have played in years. Every fight is a puzzle with more than "
                "one right answer, and losing never feels arbitrary."),
    (2, 1, 4.0, "I did not expect a freight logistics sim to keep me up until 3am. The first hour is "
                "slow on purpose, and it pays off once the routes start to interlock."),
    (3, 2, 2.5, "Atmosphere is excellent for the first hour. Then it runs out of ideas and starts "
                "repeating scares it already spent."),
    (4, 4, 4.0, "Handling is arcade in the best way. The soundtrack carries the longer races."),
    (5, 5, 3.0, "Relaxing for an evening, but the building grid is fiddly and the cosmetic unlocks feel "
                "like a chore. Good for a weekend, not much longer."),
    (6, 6, 4.5, "Quiet, strange, and it trusts you to keep up. The ending recontextualises an early "
                "scene, and I sat there for a minute."),
    (7, 7, 4.0, "Tight netcode and maps that reward learning them. Matchmaking is thin off-peak, so plan "
                "around the evening queue."),
    (0, 8, 4.5, "Forty small puzzles, none of them padding. The last third is the best part."),
    (1, 9, 3.0, "The survival loop is solid, but the crafting menus fight you constantly."),
    (2, 10, 4.0, "Short, sharp, and it knows when to stop. I wanted one more floor."),
    (3, 11, 4.5, "Cozy in a way that never feels sugary. Mapping the harbour streets by hand is oddly "
                 "satisfying."),
    (4, 12, 3.5, "The rhythm mechanics are clever, though the song list is thin. The tutorial teaches the "
                 "wrong hand first."),
    (5, 13, 5.0, "Simple rules, deep consequences. I lost three evenings to one more turn and do not "
                 "regret any of them."),
    (6, 14, 3.0, "Nice handling and a good sense of speed. The damage model is too forgiving when you "
                 "clip a wall."),
    (7, 15, 4.0, "Charming writing and a lot of heart. The side quests are where it really shines."),
    (0, 16, 2.0, "The idea is clever, but the tower variety is shallow and the balance patches have not "
                 "caught up. I tried it twice and drifted off."),
    (1, 17, 3.5, "Co-op is the reason to play. Solo feels like a worse version of the same game."),
    (2, 0, 4.0, "Solid combat and a map that opens up slowly. The save points are a bit too sparse for "
                "my taste."),
    (3, 1, 4.5, "Every route decision matters a little, and the spreadsheet-brain moments are genuinely "
                "fun. Give it time."),
    (4, 5, 2.0, "Feels like a demo of a better game. The quarry resource loop gets dull quickly."),
    (5, 3, 4.0, "The campaign writing surprised me. Some missions drag, but the endgame setup is "
                "memorable."),
    (6, 9, 3.5, "Good survival hooks, but the open map feels empty between points of interest."),
    (7, 2, 4.5, "The lights go out at the wrong moments and it is wonderful. The sound design is doing "
                "half the job, and it does it well."),
    (0, 6, 3.0, "The mystery hooks in the first act, then the middle leans on too many notes. Still a "
                "nice listen-along, though."),
    (1, 4, 5.0, "Pure speed and very clean design. I can already tell this one will sit in my library "
                "for years."),
    (3, 12, 3.0, "The rhythm is fun but hard to recommend at full price with this few songs."),
    (4, 13, 4.0, "Clever puzzles that play with tide timing. A few levels are much harder than the ones "
                 "around them."),
    (5, 15, 4.5, "Every room has a small surprise. It is the kind of game I recommend to people who do not "
                 "usually play games."),
    (2, 17, 3.0, "Fun for an hour with friends, then the matchmaking waits get long on weekdays."),
    (6, 11, 4.0, "Good pacing and a generous map. The fishing mini-game is oddly good."),
    (7, 8, 3.5, "The forks are clever, and some of the puzzles lean on trial and error more than I liked."),
    # The demo player's own reviews. Each one is on a game in DEMO_LIBRARY.
    (DEMO, 0, 4.5, "It took a while to find the rhythm, but the movement feels great once it clicks. "
                   "Worth finishing."),
    (DEMO, 3, 5.0, "Turn order tricks made me rethink how I scout. Easily the best tactics game I have "
                   "played this year."),
    (DEMO, 6, 3.5, "An interesting story with a slow middle. I liked the ending more than the setup."),
]

# (review index, commenter user index, text). The commenter is never the
# review's author; the seed checks this in the tests.
COMMENTS = [
    (0, 1, "Agreed on the late-game rooms. The one before the third boss is rough."),
    (0, 2, "This is what pushed me to finally buy it."),
    (0, 6, "The movement took me a while too. Once it clicked I stopped noticing the cheap deaths."),
    (1, 0, "Fair. The reuse hit me harder than the difficulty did."),
    (2, 2, "Which class did you finish on? I bounced off until I tried the support build."),
    (2, 5, "Seconding this. The tutorial undersells the whole system."),
    (3, 3, "Kept telling myself one more route. Then it was 3am."),
    (3, 1, "Did you ever get the rail contract running? I could not balance the timetable."),
    (7, 0, "Went back and replayed the opening after finishing. Completely different scene."),
    (10, 6, "Winter starts earlier than you expect, and it punishes slow scavenging."),
    (10, 3, "Mostly agree. The shelter upgrades feel a bit late."),
    (13, 7, "The song list is the only real complaint I have."),
    (33, 3, "Same here. The first boss took me four tries."),
    (33, 1, "Your take on the movement is spot on. It took me far too long to learn the dash."),
]


def _final_numbers(total, achievements, unlocked):
    """The verified numbers a review freezes at write time, as the reviews router computes them."""
    pct = None
    if achievements and unlocked is not None:
        pct = 100 * unlocked / achievements
    return total, pct


def main() -> int:
    db = SessionLocal()
    try:
        if db.query(User).count() > 0:
            print("Refusing to seed: this database already contains users.", file=sys.stderr)
            print("Point DATABASE_URL at an empty, migrated database.", file=sys.stderr)
            return 1

        rng = random.Random(SEED)
        now = utcnow()

        games = []
        for appid, name, genres, summary in GAMES:
            game = Game(steam_appid=appid, name=name, genres=genres, summary=summary, header_image_url=None)
            db.add(game)
            games.append(game)
        db.flush()

        users, links = [], []
        for steam_id, display_name in USERS:
            user = User(display_name=display_name)
            db.add(user)
            db.flush()
            link = LinkedAccount(
                user_id=user.id, platform=Platform.steam,
                platform_user_id=str(steam_id), last_synced_at=now,
            )
            db.add(link)
            db.flush()
            users.append(user)
            links.append(link)

        review_games = {}
        for user_idx, game_idx, _rating, _body in REVIEWS:
            review_games.setdefault(user_idx, set()).add(game_idx)

        # Each user's library: (game, minutes, achievements total, unlocked) per game.
        # The demo player gets its fixed list. Everyone else samples a few games
        # at random and always owns the games they reviewed.
        libraries = {}
        for idx in range(len(USERS)):
            if idx == DEMO:
                libraries[idx] = [(games[g], m, a, u) for g, m, a, u in DEMO_LIBRARY]
                continue
            picked = set(rng.sample(range(len(games)), rng.randint(6, 11))) | review_games.get(idx, set())
            entries = []
            for g in sorted(picked):
                total = rng.choice([0, 0, 45, 120, 340, 780, 1600, 3200, 12000])
                achievements, unlocked = None, None
                if total > 0 and rng.random() < 0.75:
                    achievements = rng.choice([12, 24, 36, 50])
                    unlocked = rng.randint(0, achievements)
                entries.append((games[g], total, achievements, unlocked))
            libraries[idx] = entries

        # Append-only snapshots, three per owned game, oldest first, so playtime
        # visibly grows and the latest-snapshot query has more than one row to pick from.
        final = {}
        for idx, user in enumerate(users):
            for game, total, achievements, unlocked in libraries[idx]:
                for step, weeks_ago in enumerate((6, 3, 0)):
                    fraction = (step + 1) / 3
                    db.add(PlaytimeSnapshot(
                        user_id=user.id, game_id=game.id, linked_account_id=links[idx].id,
                        source=SnapshotSource.steam.value,
                        playtime_minutes=int(total * fraction),
                        achievements_unlocked=None if unlocked is None else int(unlocked * fraction),
                        achievements_total=achievements,
                        captured_at=now - timedelta(weeks=weeks_ago),
                    ))
                final[(idx, game.id)] = (total, achievements, unlocked)
        db.flush()

        reviews = []
        for user_idx, game_idx, rating, body in REVIEWS:
            user, game = users[user_idx], games[game_idx]
            review = Review(
                user_id=user.id, game_id=game.id, rating=rating, body=body,
                created_at=now - timedelta(days=rng.randint(1, 60), hours=rng.randint(0, 23)),
            )
            # Same rule the reviews router uses: freeze the verified stats at write time.
            total, pct = _final_numbers(*final[(user_idx, game.id)])
            review.verified_playtime_minutes = total
            review.verified_achievement_pct = pct
            if total is not None or pct is not None:
                review.verified_source = SnapshotSource.steam.value
            db.add(review)
            reviews.append(review)
        db.flush()

        for review_idx, user_idx, body in COMMENTS:
            review = reviews[review_idx]
            db.add(Comment(
                review_id=review.id, user_id=users[user_idx].id, body=body,
                created_at=min(review.created_at + timedelta(hours=rng.randint(2, 96)), now),
            ))

        db.commit()
        print(f"Seeded {len(games)} games, {len(users)} users, "
              f"{db.query(PlaytimeSnapshot).count()} snapshots, "
              f"{len(reviews)} reviews, {len(COMMENTS)} comments.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
