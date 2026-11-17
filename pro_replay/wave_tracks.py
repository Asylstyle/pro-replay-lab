"""Identify reviewable early-wave trajectories in Source 2 frames. Coordinates alone do not identify aggro."""

from math import dist, hypot, isfinite


def coordinates(data):
    if not isinstance(data, dict):
        return None
    xy = data.get("x"), data.get("y")
    return xy if all(type(n) in (float, int) and isfinite(n) for n in xy) else None


def _sample(frame, slot, hero_id):
    units = frame.get("wave_units")
    heroes = frame.get("heroes")
    if not isinstance(units, list) or not isinstance(heroes, dict):
        return None
    hero = heroes.get(str(slot))
    actor = coordinates(hero)
    if not actor or hero.get("hero_id") != hero_id or hero.get("alive") is not True:
        return None
    team = 2 if slot < 128 else 3
    other_side = 5 - team
    friends, enemies, neutrals, towers = {}, {}, {}, {}
    groups = {("lane", team): friends, ("lane", other_side): enemies, ("neutral", 4): neutrals}
    for unit in units:
        pos = coordinates(unit)
        if not pos or type(unit.get("id")) is not int:
            continue
        group = groups.get((unit.get("kind"), unit.get("team")))
        if group is not None:
            group[unit["id"]] = pos
        elif unit.get("kind") == "tower" and unit.get("team") == other_side:
            towers[unit.get("name")] = pos
    return actor, friends, enemies, neutrals, towers


def _friendly_pull(start, meeting, finish):
    actor, friends, _, neutrals, _ = start
    for handle, origin in neutrals.items():
        mid = meeting[3].get(handle)
        end = finish[3].get(handle)
        if not mid or not end or dist(actor, origin) > 420 or dist(mid, origin) < 160:
            continue
        if dist(meeting[0], mid) > 550 or dist(end, origin) + 80 > dist(mid, origin):
            continue
        following = 0
        for uid, first in friends.items():
            second, third = meeting[1].get(uid), finish[1].get(uid)
            if (second and third and dist(first, origin) > 540 and dist(second, mid) < 340
                    and dist(third, end) < 330 and dist(third, origin) + 140 < dist(second, origin)):
                following += 1
        if following >= 2:
            return following
    return 0


def _lane_coordinates(point, front, rear):
    dx, dy = rear[0] - front[0], rear[1] - front[1]
    length = hypot(dx, dy)
    if length < 1:
        return None
    x, y = point[0] - front[0], point[1] - front[1]
    return (x * dx + y * dy) / (length * length), (x * dy - y * dx) / length


def _enemy_drag(start, meeting, finish):
    actor, _, creeps, _, towers = start
    for name, t1 in towers.items():
        if not isinstance(name, str) or "_tower1_" not in name:
            continue
        t2 = towers.get(name.replace("_tower1_", "_tower2_"))
        if not t2 or t1 == t2:
            continue
        along, side = _lane_coordinates(actor, t1, t2)
        _, side_end = _lane_coordinates(finish[0], t1, t2)
        if not (0.05 <= along <= 1 and abs(side) <= 500 and abs(side_end) >= abs(side) + 340):
            continue
        moved = 0
        for uid, first in creeps.items():
            mid, end = meeting[2].get(uid), finish[2].get(uid)
            if not mid or not end or dist(first, actor) > 550 or dist(mid, meeting[0]) > 520 or dist(end, finish[0]) > 520:
                continue
            at_start, side_start = _lane_coordinates(first, t1, t2)
            _, new_side = _lane_coordinates(end, t1, t2)
            if (0.0 <= at_start <= 1.05 and abs(side_start) < 480 and abs(new_side) > abs(side_start) + 260
                    and (new_side - side_start) * (side_end - side) > 0 and dist(first, end) > 430):
                moved += 1
        if moved >= 2:
            return moved
    return 0


def trajectory_windows(timeline, slot, hero_id, duration):
    if not timeline or type(hero_id) is not int or hero_id <= 0:
        return []
    last = min(duration, 480)
    provider = timeline.get("frame_window")
    if callable(provider):
        # Old decoder caches have no creep positions. Avoid reading eight minutes of hero states for those.
        probe = provider(0, min(8, last))
        if probe and all("wave_units" not in row for row in probe if isinstance(row, dict)):
            return []
        batches = (provider(t, min(t + 29, last)) for t in range(0, last + 1, 30))
    else:
        batches = (timeline.get("frames") or [],)
    samples = {}
    for frames in batches:
        for frame in frames:
            if not isinstance(frame, dict) or type(frame.get("tick")) is not int:
                continue
            t = frame.get("time")
            if type(t) is int and 0 <= t <= last and t % 2 == 0:
                samples[t] = _sample(frame, slot, hero_id)
    found = []
    last_seen = {}
    for t in sorted(samples):
        steps = samples[t], samples.get(t + 4), samples.get(t + 8)
        if not all(steps):
            continue
        for kind, detect in (("camp", _friendly_pull), ("drag", _enemy_drag)):
            if t - last_seen.get(kind, -30) < 24:
                continue
            count = detect(*steps)
            if count:
                found.append({"time": t, "end": t + 8, "kind": kind, "creeps": count})
                last_seen[kind] = t
    return found
