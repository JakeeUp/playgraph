"""Yearly changes in synced counters, never lifetime totals passed off as activity."""
from collections import defaultdict
from datetime import datetime, timezone


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def counter_change(rows, field, start):
    before = [row for row in rows if utc(row["captured_at"]) < start]
    during = [row for row in rows if utc(row["captured_at"]) >= start]
    known = [row for row in during if row.get(field) is not None]
    months = [0] * 12
    if not known:
        return None, months
    baseline = before[-1].get(field) if before else None
    if baseline is None:
        baseline = known[0][field]
    last = known[-1][field]
    high = baseline
    for row in known:
        # Cap interim highs at the final counter so corrections cannot inflate the year.
        value = min(row[field], max(baseline, last))
        months[utc(row["captured_at"]).month - 1] += max(0, value - high)
        high = max(high, value)
    return max(0, last - baseline), months


def build_replay(year, snapshots, reviews=(), completed=(), *, now, first_sync=None):
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = min(datetime(year + 1, 1, 1, tzinfo=timezone.utc), utc(now))
    grouped = defaultdict(list)
    for row in snapshots:
        if row["game"].get("content_kind") != "software" and utc(row["captured_at"]) < end:
            grouped[(row["source"], row["game"]["id"])].append(row)
    sources = {}
    grades = ("bronze", "silver", "gold", "platinum")
    for (source, _), rows in sorted(grouped.items()):
        rows.sort(key=lambda row: (utc(row["captured_at"]), row["id"]))
        during = [row for row in rows if utc(row["captured_at"]) >= start]
        if not during:
            continue
        result = sources.setdefault(source, {"source": source, "total_minutes": None,
            "unknown_playtime_games": 0, "achievements_earned": None, "trophies_earned": None,
            "top_games": [], "genres": {}, "months": [0] * 12})
        minutes, monthly = counter_change(rows, "playtime_minutes", start)
        if any(row.get("playtime_minutes") is None for row in during):
            result["unknown_playtime_games"] += 1
        if minutes is not None:
            result["total_minutes"] = (result["total_minutes"] or 0) + minutes
            result["months"] = [a + b for a, b in zip(result["months"], monthly)]
            if minutes:
                game = rows[-1]["game"]
                result["top_games"].append({"game": game, "total_minutes": minutes})
                for genre in {g.strip() for g in (game.get("genres") or "").split(",") if g.strip()}:
                    result["genres"][genre] = result["genres"].get(genre, 0) + minutes
        if source == "steam":
            earned, _ = counter_change(rows, "achievements_unlocked", start)
            if earned is not None:
                result["achievements_earned"] = (result["achievements_earned"] or 0) + earned
        else:
            for grade in grades:
                earned, _ = counter_change(rows, "trophies_" + grade, start)
                if earned is not None:
                    result["trophies_earned"] = result["trophies_earned"] or dict.fromkeys(grades, None)
                    previous = result["trophies_earned"][grade]
                    result["trophies_earned"][grade] = (previous or 0) + earned
    for result in sources.values():
        result["top_games"] = sorted(result["top_games"], key=lambda row: (-row["total_minutes"], row["game"]["id"]))[:5]
        result["genres"] = [{"genre": name, "total_minutes": value}
            for name, value in sorted(result["genres"].items(), key=lambda item: (-item[1], item[0]))[:5]]
        result["months"] = [{"month": month, "total_minutes": value if result["total_minutes"] is not None
            and (year < utc(now).year or month <= utc(now).month) else None}
            for month, value in enumerate(result["months"], 1)]
    written = [row for row in reviews if start <= utc(row["created_at"]) < end
        and row["game"].get("content_kind") != "software"]
    finished = [row for row in completed if row.get("completed_at") and start <= utc(row["completed_at"]) < end
        and row["game"].get("content_kind") != "software"]
    top = min(written, key=lambda row: (-row["rating"], row["game"]["id"])) if written else None
    return {"year": year, "as_of": utc(now), "tracked_since": max(start, utc(first_sync)) if first_sync and utc(first_sync) < end else None,
        "partial_year": year == utc(now).year or first_sync is None or utc(first_sync) > start,
        "sources": list(sources.values()), "reviews": {"count": len(written),
            "average_rating": round(sum(row["rating"] for row in written) / len(written), 2) if written else None,
            "highest_rated": {"game": top["game"], "rating": top["rating"]} if top else None},
        "completed_count": len(finished), "has_data": bool(sources or written or finished)}
