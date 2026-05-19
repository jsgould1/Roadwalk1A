# -*- coding: utf-8 -*-
"""One-off bundle transform — Gatlinburg Bypass (Section A).

Shifts every NPS GIP/WIP guardrail and retaining-wall line +290 ft
up-station and re-contours it to the roadway centerline:

  * the line's perpendicular offset from the centerline is measured
    (median of its current vertices) and its recorded side is kept;
  * a new polyline is sampled along the section A centerline from
    (old sta_ft + 290) for the line's length_ft, held at that offset;
  * sta_ft / sta / attrs.mp_start / attrs.mp_end are updated to match.

Run from the data/ directory:  python shift_gip_wip_lines_section_a.py
Re-runnable note: each run shifts another +290 ft, so run it ONCE.
"""
import json, math, statistics

BUNDLE   = 'prewalk-bundle.json'
SHIFT_FT = 290.0
R_FT     = 20902231.0          # earth radius, feet
DEG_FT   = 364000.0            # ~feet per degree (equirectangular helper)

def hav_ft(a, b):              # a, b are [lng, lat]
    la1, lo1 = math.radians(a[1]), math.radians(a[0])
    la2, lo2 = math.radians(b[1]), math.radians(b[0])
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * R_FT * math.asin(math.sqrt(h))

def cum_sta(line):
    c = [0.0]
    for i in range(len(line) - 1):
        c.append(c[-1] + hav_ft(line[i], line[i + 1]))
    return c

def project(pt, line, cum):
    """Nearest point on `line` to `pt` — returns (station_ft, perp_dist_ft)."""
    best_d, best_sta = float('inf'), 0.0
    cos_lat = math.cos(math.radians(pt[1]))
    for i in range(len(line) - 1):
        a, b = line[i], line[i + 1]
        ax = (a[0] - pt[0]) * cos_lat * DEG_FT
        ay = (a[1] - pt[1]) * DEG_FT
        bx = (b[0] - pt[0]) * cos_lat * DEG_FT
        by = (b[1] - pt[1]) * DEG_FT
        vx, vy = bx - ax, by - ay
        denom = vx * vx + vy * vy
        if denom < 1e-9:
            continue
        t = max(0.0, min(1.0, (-ax * vx - ay * vy) / denom))
        px, py = ax + t * vx, ay + t * vy
        d = math.hypot(px, py)
        if d < best_d:
            best_d = d
            best_sta = cum[i] + t * math.hypot(vx, vy)
    return best_sta, best_d

def point_at(line, cum, sta):
    if sta <= 0:        return list(line[0])
    if sta >= cum[-1]:  return list(line[-1])
    for i in range(len(line) - 1):
        if cum[i + 1] >= sta:
            seg = cum[i + 1] - cum[i]
            t = (sta - cum[i]) / seg if seg > 0 else 0.0
            return [line[i][0] + t * (line[i + 1][0] - line[i][0]),
                    line[i][1] + t * (line[i + 1][1] - line[i][1])]
    return list(line[-1])

def bearing_at(line, cum, sta):
    for i in range(len(line) - 1):
        if cum[i + 1] >= sta or i == len(line) - 2:
            a, b = line[i], line[i + 1]
            la1, la2 = math.radians(a[1]), math.radians(b[1])
            dlon = math.radians(b[0] - a[0])
            x = math.sin(dlon) * math.cos(la2)
            y = (math.cos(la1) * math.sin(la2)
                 - math.sin(la1) * math.cos(la2) * math.cos(dlon))
            return math.atan2(x, y)
    return 0.0

def offset_pt(pt, bearing, dist):     # pt [lng,lat] -> [lng,lat]
    la1, lo1 = math.radians(pt[1]), math.radians(pt[0])
    dR = dist / R_FT
    la2 = math.asin(math.sin(la1) * math.cos(dR)
                    + math.cos(la1) * math.sin(dR) * math.cos(bearing))
    lo2 = lo1 + math.atan2(math.sin(bearing) * math.sin(dR) * math.cos(la1),
                           math.cos(dR) - math.sin(la1) * math.sin(la2))
    return [math.degrees(lo2), math.degrees(la2)]

def retrace(line, cum, start_sta, length, perp, side):
    """Sample `line` from start_sta for `length`, offset `perp` ft on `side`."""
    total = cum[-1]
    s0 = max(0.0, min(total, start_sta))
    s1 = max(0.0, min(total, s0 + length))
    stations = [s0]
    for c in cum:
        if s0 + 1 < c < s1 - 1:
            stations.append(c)
    stations.append(s1)
    sign = 1 if side == 'R' else -1     # R = right of travel, L = left
    out = []
    for s in stations:
        base = point_at(line, cum, s)
        if perp < 1.0:
            out.append([round(base[0], 7), round(base[1], 7)])
        else:
            o = offset_pt(base, bearing_at(line, cum, s) + sign * math.pi / 2, perp)
            out.append([round(o[0], 7), round(o[1], 7)])
    return out, s1 - s0

def fmt_sta(sta):
    s = int(round(sta))
    return '%d+%02d' % (s // 100, s % 100)

# --- run -------------------------------------------------------------------
bundle = json.load(open(BUNDLE, encoding='utf-8'))
A = next(s for s in bundle['sections'] if s['id'] == 'A')
align = A['alignment']
cum = cum_sta(align)
print('Section A — %s' % A['name'])
print('  centerline length: %.0f ft  (%d vertices)' % (cum[-1], len(align)))

shifted = 0
for p in A['pins']:
    if p.get('kind') not in ('guardrail', 'wall'):
        continue
    attrs = p.get('attrs') or {}
    coords = (p.get('geometry') or {}).get('coordinates') or []
    length = float(attrs.get('length_ft') or 0)
    if len(coords) < 2 or length < 1:
        print('  SKIP %s — no geometry/length' % p.get('id'))
        continue

    old_sta = float(p.get('sta_ft') or 0)
    # measure the line's offset from the centerline (median of its vertices)
    perps = [project(c, align, cum)[1] for c in coords]
    perp = statistics.median(perps) if perps else 0.0
    side = (attrs.get('side') or 'R').upper()
    if side not in ('L', 'R'):
        side = 'R'

    new_sta = old_sta + SHIFT_FT
    new_coords, eff_len = retrace(align, cum, new_sta, length, perp, side)

    p['geometry'] = {'type': 'LineString', 'coordinates': new_coords}
    p['sta_ft'] = round(new_sta, 1)
    p['sta'] = fmt_sta(new_sta)
    attrs['mp_start'] = round(new_sta / 5280.0, 4)
    attrs['mp_end'] = round((new_sta + length) / 5280.0, 4)
    p['attrs'] = attrs

    clamp = '  (clamped, %.0f ft fits)' % eff_len if eff_len < length - 1 else ''
    print('  %-12s %-9s sta %5.0f -> %5.0f  off %4.1f ft %s  len %4.0f%s'
          % (p['id'], p.get('source'), old_sta, new_sta, perp, side, length, clamp))
    shifted += 1

json.dump(bundle, open(BUNDLE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('\nShifted %d GIP/WIP guardrail/wall lines +%.0f ft up-station.' % (shifted, SHIFT_FT))
print('Saved %s' % BUNDLE)
