"""Look for a documented sequence using player-level events and game time."""

from bisect import bisect_right


def _observations(row, action):
    kind, _, item = action.partition(":")
    if kind == "item":
        times = [e["time"] for e in row.get("purchases", []) if e.get("key") == item]
        times += [e["time"] for e in row.get("timeline", [])
                  if e.get("kind") == "item" and e.get("key") == item]
        return sorted(set(times)), bool(row.get("purchase_log_available"))
    return (sorted(e["time"] for e in row.get("timeline", []) if e.get("kind") == kind),
            bool((row.get("event_coverage") or {}).get(kind)))


def find_order(rows, before, after, max_gap=None):
    """A partial replay can confirm presence, never absence. Ties have no order."""
    found, no_sequence, unknown = [], [], []
    for row in rows:
        earlier, complete_a = _observations(row, before)
        later, complete_b = _observations(row, after)
        pair = None
        for t in earlier:
            idx = bisect_right(later, t)
            if idx < len(later) and (max_gap is None or later[idx] - t <= max_gap):
                pair = (t, later[idx])
                break
        if pair is not None:
            found.append({"match_id": row["match_id"], "hero": row.get("hero_name") or row.get("hero_id"),
                          "before_time": pair[0], "after_time": pair[1], "gap": pair[1] - pair[0],
                          "source": row.get("source_url") or f'https://www.opendota.com/matches/{row["match_id"]}'})
        elif complete_a and complete_b:
            no_sequence.append(row["match_id"])
        else:
            unknown.append(row["match_id"])
    return {"total": len(rows), "found": found, "absent": no_sequence, "unknown": unknown}
