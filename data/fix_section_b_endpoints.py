"""Align Section B's alignment to Pathweb's documented endpoints:
    MP 14.753 at lat 35.611516, lng -83.424458 (route 0010S START)
    MP 20.902 at lat 35.598489, lng -83.402369 (route 0010S END)

Current state shows v0 is 1,091 ft west of Pathweb MP 14.753 (alignment
extends ~0.21 mi past the actual section start) and vN is 158 ft from
Pathweb MP 20.902. mp_start = 14.64, mp_end = 20.90 — both slightly off.

Strategy:
  1. Project both Pathweb endpoints onto the current alignment to find
     where on the polyline each documented MP actually falls.
  2. Clip the polyline to that station window with interpolated boundary
     vertices, then snap v0 and last-vertex to Pathweb's exact coords.
  3. Update mp_start to 14.753 and mp_end to 20.902.
  4. Re-project every pin onto the new (trimmed) alignment. Lat/lng
     untouched; sta_ft / sta / attrs.mp_start / attrs.mp_end recomputed.
     Any pin physically outside [Pathweb MP 14.753, MP 20.902] will
     project to the clamped endpoint sta — reported in the diagnostics
     so the user can decide whether to relocate or delete.
  5. Re-station per-section feature GeoJSONs (nps-*-section-B.geojson).
"""
import datetime
import glob
import json
import math
import os

DATA = os.path.dirname(os.path.abspath(__file__))
R_FT = 20902231.0

PW_START_LNGLAT = [-83.424458, 35.611516]   # MP 14.753
PW_END_LNGLAT   = [-83.402369, 35.598489]   # MP 20.902
PW_MP_START = 14.753
PW_MP_END   = 20.902
NEW_NAME    = 'Newfound Gap Rd NC #1 (MP 14.753-20.902)'


def hav_ft(a, b):
    la1, lo1 = math.radians(a[1]), math.radians(a[0])
    la2, lo2 = math.radians(b[1]), math.radians(b[0])
    h = (math.sin((la2 - la1) / 2) ** 2 +
         math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * R_FT * math.asin(math.sqrt(h))


def line_len_ft(coords):
    return sum(hav_ft(coords[i], coords[i+1]) for i in range(len(coords) - 1))


def fmt_sta(sta_ft):
    if sta_ft is None or not math.isfinite(sta_ft):
        return None
    whole = int(sta_ft // 100)
    rem = sta_ft - whole * 100
    return '%d+%05.2f' % (whole, rem)


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


def project_onto(pt, coords):
    pt_lat = pt[1]
    cos_lat = math.cos(math.radians(pt_lat))
    def xy(p):
        return ((p[0] - pt[0]) * cos_lat * 364000.0,
                (p[1] - pt[1]) * 364000.0)
    best_d = float('inf'); best_cum = 0.0; cum = 0.0
    for i in range(len(coords) - 1):
        a = coords[i]; b = coords[i + 1]
        seg_len = hav_ft(a, b)
        ax, ay = xy(a); bx, by = xy(b)
        vx, vy = bx - ax, by - ay
        denom = vx * vx + vy * vy
        if denom < 1e-9:
            cum += seg_len; continue
        t = max(0.0, min(1.0, (-ax * vx + -ay * vy) / denom))
        cx, cy = ax + t * vx, ay + t * vy
        d = math.hypot(cx, cy)
        if d < best_d:
            best_d = d; best_cum = cum + t * seg_len
        cum += seg_len
    return best_cum, best_d


def clip_polyline(coords, sta_lo, sta_hi):
    out = []; cum = 0.0; started = False
    for i in range(len(coords) - 1):
        a = coords[i]; b = coords[i + 1]
        seg_len = hav_ft(a, b)
        seg_start = cum; seg_end = cum + seg_len
        if seg_end < sta_lo:
            cum = seg_end; continue
        if seg_start > sta_hi:
            break
        if not started:
            if seg_start <= sta_lo <= seg_end:
                t = (sta_lo - seg_start) / seg_len if seg_len > 0 else 0
                out.append([a[0] + t*(b[0]-a[0]), a[1] + t*(b[1]-a[1])])
            else:
                out.append(list(a))
            started = True
        if seg_end <= sta_hi:
            out.append(list(b))
        else:
            t = (sta_hi - seg_start) / seg_len if seg_len > 0 else 0
            out.append([a[0] + t*(b[0]-a[0]), a[1] + t*(b[1]-a[1])])
            break
        cum = seg_end
    return out


# ── Load bundle + Section B ──────────────────────────────────────────────
bundle = json.load(open(os.path.join(DATA, 'prewalk-bundle.json'),
                        encoding='utf-8'))
sec = next(s for s in bundle['sections'] if s['id'] == 'B')
old_align = list(sec['alignment'])
old_total = line_len_ft(old_align)

print(f"Before:")
print(f"  Name:     {sec.get('name')}")
print(f"  MP:       {sec.get('mp_start')} -> {sec.get('mp_end')}")
print(f"  Vertices: {len(old_align)}")
print(f"  Length:   {old_total:.1f} ft ({old_total/5280:.3f} mi)")
print(f"  Pins:     {len(sec.get('pins', []))}")
print()

# ── Find where Pathweb endpoints sit on the current polyline ─────────────
sta_start, dist_start = project_onto(PW_START_LNGLAT, old_align)
sta_end,   dist_end   = project_onto(PW_END_LNGLAT,   old_align)
print('Pathweb endpoints projected onto current alignment:')
print(f'  MP 14.753 (Pathweb start) -> sta {sta_start:8.1f} ft, '
      f'{dist_start:.1f} ft off the polyline')
print(f'  MP 20.902 (Pathweb end)   -> sta {sta_end:8.1f} ft, '
      f'{dist_end:.1f} ft off the polyline')

# Sanity: trim the front by sta_start, keep up to sta_end
trim_front_ft = sta_start
trim_back_ft  = old_total - sta_end
print(f'\nTrim summary:')
print(f'  Front trim: {trim_front_ft:.1f} ft (current v0 to new v0)')
print(f'  Back trim:  {trim_back_ft:.1f} ft (current vN to new vN)')
if trim_back_ft < 0:
    print(f'  -> Pathweb MP 20.902 is PAST current vN; will extend by '
          f'{-trim_back_ft:.1f} ft via chord to Pathweb coord')

# ── Build the trimmed alignment ──────────────────────────────────────────
clipped = clip_polyline(old_align, sta_start, sta_end)
# Snap exact Pathweb start onto v0
v0_dist = hav_ft(clipped[0], PW_START_LNGLAT)
if v0_dist > 0.5:
    clipped[0] = list(PW_START_LNGLAT)
# Snap exact Pathweb end onto last vertex
vN_dist = hav_ft(clipped[-1], PW_END_LNGLAT)
if vN_dist > 0.5:
    # If trim_back was negative, Pathweb end is past vN — extend by appending
    if trim_back_ft < 0:
        clipped.append(list(PW_END_LNGLAT))
    else:
        clipped[-1] = list(PW_END_LNGLAT)

new_total = line_len_ft(clipped)
nominal = (PW_MP_END - PW_MP_START) * 5280
print(f'\nNew alignment: {len(clipped)} vertices, {new_total:.1f} ft '
      f'(nominal MP-range = {nominal:.1f} ft, '
      f'diff = {new_total - nominal:+.1f} ft)')

# Verify endpoints snap to Pathweb coords
print(f'  v0 -> Pathweb MP 14.753: {hav_ft(clipped[0], PW_START_LNGLAT):.1f} ft')
print(f'  vN -> Pathweb MP 20.902: {hav_ft(clipped[-1], PW_END_LNGLAT):.1f} ft')

# ── Apply to bundle ──────────────────────────────────────────────────────
sec['alignment'] = clipped
sec['mp_start']  = PW_MP_START
sec['mp_end']    = PW_MP_END
sec['name']      = NEW_NAME


def sta_to_mp(sta_ft):
    if sta_ft is None or not math.isfinite(sta_ft):
        return None
    return PW_MP_START + (sta_ft / new_total) * (PW_MP_END - PW_MP_START)


# ── Re-project every pin ────────────────────────────────────────────────
restationed = 0
clamped_to_start = 0
clamped_to_end   = 0
SLOP_FT = 1.0   # consider sta_ft within this distance of an endpoint "clamped"
for p in sec.get('pins', []):
    g = p.get('geometry')
    pt = first_point(g)
    if pt is None: continue
    sta_ft, perp_ft = project_onto(pt, clipped)
    p['sta_ft'] = round(sta_ft, 1)
    p['sta']    = fmt_sta(sta_ft)
    a = p.setdefault('attrs', {})
    if 'mp_start' in a:
        a['mp_start'] = round(sta_to_mp(sta_ft), 6)
    end_pt = last_point(g)
    if end_pt is not None and 'mp_end' in a:
        end_sta, _ = project_onto(end_pt, clipped)
        a['mp_end'] = round(sta_to_mp(end_sta), 6)
    if sta_ft < SLOP_FT: clamped_to_start += 1
    elif sta_ft > new_total - SLOP_FT: clamped_to_end += 1
    restationed += 1

print(f'\nRe-stationed {restationed} pins.')
if clamped_to_start:
    print(f'  {clamped_to_start} pin(s) clamped to sta_ft=0 '
          f'(physically before Pathweb MP 14.753)')
if clamped_to_end:
    print(f'  {clamped_to_end} pin(s) clamped to sta_ft={new_total:.0f} '
          f'(physically past Pathweb MP 20.902)')

# Sort pins by sta_ft
sec['pins'].sort(key=lambda x: x.get('sta_ft', 0) or 0)

# ── Save bundle ──────────────────────────────────────────────────────────
bundle['generated_at'] = (datetime.datetime.now(datetime.UTC)
                          .strftime('%Y-%m-%dT%H:%M:%SZ'))
json.dump(bundle, open(os.path.join(DATA, 'prewalk-bundle.json'), 'w',
                       encoding='utf-8'),
          ensure_ascii=False, indent=1)
print(f"\nWrote prewalk-bundle.json")

# ── Re-station per-section feature geojsons ──────────────────────────────
shifted = 0
for fn in sorted(glob.glob(os.path.join(DATA, 'nps-*-section-B.geojson'))):
    g = json.load(open(fn, encoding='utf-8'))
    changed = False
    for ft in g.get('features', []):
        pt = first_point(ft.get('geometry'))
        if pt is None: continue
        sta_ft, dist = project_onto(pt, clipped)
        pp = ft.setdefault('properties', {})
        pp['_sta_ft'] = round(sta_ft, 1)
        pp['_sta']    = fmt_sta(sta_ft)
        pp['_dist_from_alignment_ft'] = round(dist, 1)
        changed = True
    if changed:
        g['features'].sort(
            key=lambda f: (f.get('properties') or {}).get('_sta_ft', 0))
        json.dump(g, open(fn, 'w', encoding='utf-8'), indent=2)
        shifted += 1
        print(f'  re-stationed: {os.path.basename(fn)}')

print(f'\nRe-stationed {shifted} per-section feature file(s).')
print('Done.')
