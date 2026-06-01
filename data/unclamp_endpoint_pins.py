"""Restore true off-section stationing for pins that got clamped to
section endpoints by prior trim scripts.

When a pin's lat/lng sits beyond Pathweb's documented section start or
end, project_onto() returns the closest point on the *clipped* polyline,
which is the endpoint itself. That gave us pin.sta_ft = 0 for upstream
features and pin.sta_ft = totalFt for downstream features — even when
the pin was physically 100s of feet outside the section.

This script extrapolates the first segment (alignment[0] → alignment[1])
backward and the last segment forward, then projects each clamped pin
onto whichever extension is closest. Pins legitimately at the endpoint
(geometry within SLOP_FT of alignment[0]/alignment[-1]) are skipped.

Result: pins land at their true station (negative for pre-start, > totalFt
for post-end). The SLD's new half-window gutter (commit 96c095b) renders
them in the empty space outside the centerline range, where they belong.
Pins beyond the gutter (>500 ft past either endpoint at 1000' zoom) stay
in the bundle but won't be visible until the zoom is widened further.
"""
import datetime
import json
import math
import os

DATA = os.path.dirname(os.path.abspath(__file__))
BUNDLE_PATH = os.path.join(DATA, 'prewalk-bundle.json')
R_FT = 20902231.0

# A pin is treated as "clamped" if sta_ft sits within CLAMP_SLOP_FT of an
# endpoint AND the pin's geometry is more than NEAR_EP_FT from that
# endpoint. The latter check protects pins that are *legitimately* at the
# endpoint (e.g. a sign right at MP 0.011) from being re-projected.
CLAMP_SLOP_FT = 1.5    # sta_ft must be within this much of 0 or totalFt
NEAR_EP_FT    = 25.0   # geometry must be at least this far from the EP vertex


def hav_ft(a, b):
    la1, lo1 = math.radians(a[1]), math.radians(a[0])
    la2, lo2 = math.radians(b[1]), math.radians(b[0])
    h = (math.sin((la2 - la1) / 2) ** 2 +
         math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * R_FT * math.asin(math.sqrt(h))


def line_len_ft(coords):
    return sum(hav_ft(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def fmt_sta(sta_ft):
    """Standard STA string, plus a "-" prefix for negative stations."""
    if sta_ft is None or not math.isfinite(sta_ft):
        return None
    sign = '-' if sta_ft < 0 else ''
    s = abs(sta_ft)
    whole = int(s // 100)
    rem = s - whole * 100
    return '%s%d+%05.2f' % (sign, whole, rem)


def first_point(geom):
    if not geom: return None
    t = geom.get('type'); c = geom.get('coordinates')
    if c is None: return None
    if t == 'Point': return c[:2]
    if t == 'LineString' and c: return c[0][:2]
    if t == 'MultiLineString' and c and c[0]: return c[0][0][:2]
    if t == 'Polygon' and c and c[0]: return c[0][0][:2]
    return None


def last_point(geom):
    if not geom: return None
    t = geom.get('type'); c = geom.get('coordinates')
    if c is None: return None
    if t == 'LineString' and c: return c[-1][:2]
    if t == 'MultiLineString' and c and c[-1]: return c[-1][-1][:2]
    return None


def project_onto_line(pt, a, b):
    """Project pt onto the *infinite* line through a→b. Returns
    (signed_along_ft, perp_ft) where along is measured from a in the
    direction of b. Negative along = before a; along > |ab| = past b.
    Equirectangular flat-Earth math — fine for tens of feet at this lat."""
    pt_lat = pt[1]
    cos_lat = math.cos(math.radians(pt_lat))
    def xy(p):
        return ((p[0] - pt[0]) * cos_lat * 364000.0,
                (p[1] - pt[1]) * 364000.0)
    ax, ay = xy(a); bx, by = xy(b)
    vx, vy = bx - ax, by - ay
    denom = vx * vx + vy * vy
    if denom < 1e-9:
        return 0.0, math.hypot(ax, ay)
    # No clamp on t — allow projections off either end of the segment.
    t = (-ax * vx + -ay * vy) / denom
    seg_len = hav_ft(a, b)
    along_ft = t * seg_len
    cx, cy = ax + t * vx, ay + t * vy
    perp_ft = math.hypot(cx, cy)
    return along_ft, perp_ft


# ── Load bundle ──────────────────────────────────────────────────────────
print(f'Loading {BUNDLE_PATH}')
bundle = json.load(open(BUNDLE_PATH, encoding='utf-8'))

total_unclamped_start = 0
total_unclamped_end   = 0
total_skipped_legit   = 0
total_skipped_unfixable = 0

for sec in bundle['sections']:
    align = sec.get('alignment')
    pins  = sec.get('pins', [])
    if not align or len(align) < 2 or not pins:
        continue
    total_ft = line_len_ft(align)
    v0, v1 = align[0], align[1]
    vN, vN1 = align[-1], align[-2]
    mp_per_ft = (sec['mp_end'] - sec['mp_start']) / total_ft if total_ft > 0 else 0

    n_start_fixed = n_end_fixed = 0
    sample_start = sample_end = None

    for p in pins:
        sta_ft = p.get('sta_ft')
        if sta_ft is None: continue
        g = p.get('geometry')
        pt = first_point(g)
        if pt is None: continue

        # Clamped to start?
        if sta_ft <= CLAMP_SLOP_FT:
            dist_to_v0 = hav_ft(pt, v0)
            if dist_to_v0 < NEAR_EP_FT:
                # Legitimately at v0 — leave alone.
                total_skipped_legit += 1
                continue
            # Extend the first segment backwards: parameterize line v0→v1,
            # project pin onto it. negative along means before v0.
            along, perp = project_onto_line(pt, v0, v1)
            if along < 0:
                # Pin lies upstream of v0 along the first segment's bearing.
                new_sta = along       # signed, negative
                p['sta_ft'] = round(new_sta, 1)
                p['sta']    = fmt_sta(new_sta)
                a = p.setdefault('attrs', {})
                if 'mp_start' in a:
                    a['mp_start'] = round(sec['mp_start'] + new_sta * mp_per_ft, 6)
                end_pt = last_point(g)
                if end_pt is not None and 'mp_end' in a:
                    end_along, _ = project_onto_line(end_pt, v0, v1)
                    a['mp_end'] = round(sec['mp_start'] + end_along * mp_per_ft, 6)
                n_start_fixed += 1
                if sample_start is None:
                    sample_start = (p.get('id'), new_sta, perp)
            else:
                # Pin is off to the side, projection falls onto the first
                # segment. Leave at 0 — best we can do without more context.
                total_skipped_unfixable += 1

        # Clamped to end?
        elif sta_ft >= total_ft - CLAMP_SLOP_FT:
            dist_to_vN = hav_ft(pt, vN)
            if dist_to_vN < NEAR_EP_FT:
                total_skipped_legit += 1
                continue
            # Extend the last segment forwards: parameterize line vN1→vN,
            # project pin onto it. along > seg_len means past vN.
            along, perp = project_onto_line(pt, vN1, vN)
            seg_len = hav_ft(vN1, vN)
            if along > seg_len:
                # Pin lies downstream of vN — past-end station.
                # The total cumulative distance is (total_ft - seg_len) + along.
                new_sta = (total_ft - seg_len) + along
                p['sta_ft'] = round(new_sta, 1)
                p['sta']    = fmt_sta(new_sta)
                a = p.setdefault('attrs', {})
                if 'mp_start' in a:
                    a['mp_start'] = round(sec['mp_start'] + new_sta * mp_per_ft, 6)
                end_pt = last_point(g)
                if end_pt is not None and 'mp_end' in a:
                    end_along, _ = project_onto_line(end_pt, vN1, vN)
                    end_sta = (total_ft - seg_len) + end_along
                    a['mp_end'] = round(sec['mp_start'] + end_sta * mp_per_ft, 6)
                n_end_fixed += 1
                if sample_end is None:
                    sample_end = (p.get('id'), new_sta, perp)
            else:
                total_skipped_unfixable += 1

    if n_start_fixed or n_end_fixed:
        print(f'\nSection {sec["id"]} ({sec["name"]!r}):')
        if n_start_fixed:
            sid, ns, sperp = sample_start
            print(f'  {n_start_fixed} pin(s) unclamped at start. '
                  f'Sample: {sid} → STA {fmt_sta(ns)} ({sperp:.1f} ft off centerline)')
        if n_end_fixed:
            sid, ns, sperp = sample_end
            print(f'  {n_end_fixed} pin(s) unclamped at end. '
                  f'Sample: {sid} → STA {fmt_sta(ns)} ({sperp:.1f} ft off centerline)')
        # Re-sort by sta_ft (negatives first, past-end last) so downstream
        # consumers see pins in monotonic station order.
        pins.sort(key=lambda x: x.get('sta_ft') if x.get('sta_ft') is not None else 0)

    total_unclamped_start += n_start_fixed
    total_unclamped_end   += n_end_fixed

print(f'\n── Totals ──')
print(f'  Unclamped (negative sta):     {total_unclamped_start}')
print(f'  Unclamped (past-end sta):     {total_unclamped_end}')
print(f'  Skipped (legitimately at EP): {total_skipped_legit}')
print(f'  Skipped (perp foot on segment): {total_skipped_unfixable}')

if total_unclamped_start == 0 and total_unclamped_end == 0:
    print('\nNo pins needed unclamping. Bundle unchanged.')
else:
    bundle['generated_at'] = (datetime.datetime.now(datetime.UTC)
                              .strftime('%Y-%m-%dT%H:%M:%SZ'))
    with open(BUNDLE_PATH, 'w', encoding='utf-8') as f:
        json.dump(bundle, f, ensure_ascii=False, indent=1)
    print(f'\nWrote updated {os.path.basename(BUNDLE_PATH)}.')
print('Done.')
