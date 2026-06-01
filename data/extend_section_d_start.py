"""Extend Section D (Newfound Gap Rd TN MP 1.86-6.50) backwards at the
start so STA 0+00 lands at NPS Pathweb's MP 1.858 control coordinate.

Pathweb's documented start for route 0010N is at:
    MP 1.858 — lat 35.68326, lng -83.5342
The current bundle alignment's v0 sits ~112 ft downroad of that point,
which made every saved STA value ~112 ft too low. The user reported it
as "off by about 100' ".

Fix:
  1. Insert a new vertex at the Pathweb start coords as the new v0 of
     Section D's alignment. The straight segment from new-v0 to old-v0
     replaces the missing front-end. (At this scale the road is almost
     straight, so the chord is within a foot of the true road
     centreline; if a curvier fit is ever needed we'd splice in
     intermediate vertices from grsm-roads.geojson instead.)
  2. Re-project every pin in Section D onto the new alignment. Each
     pin's lat/lng is untouched — only its sta_ft / sta / mp_start /
     mp_end derived values shift forward by the extension length.
  3. Set mp_start = 1.858 (Pathweb-exact) so the MP↔STA mapping at
     STA 0+00 reads correctly. mp_end is left at 6.50 because the
     bundle intentionally extends past Pathweb's MP 6.132 end (see
     section.pathweb_refs[0].note).
  4. Re-station the per-section feature geojsons
     (data/nps-*-section-D.geojson) so the _sta_ft / _sta properties
     match the new alignment.

Reads:
  data/prewalk-bundle.json
  data/nps-*-section-D.geojson
Writes (in place):
  data/prewalk-bundle.json
  data/nps-*-section-D.geojson
"""
import datetime
import glob
import json
import math
import os

DATA = os.path.dirname(os.path.abspath(__file__))
SEC_ID = 'D'
# Pathweb's documented MP 1.858 control point for route 0010N (start).
PATHWEB_START_LAT = 35.68326
PATHWEB_START_LNG = -83.5342
PATHWEB_START_MP  = 1.858
R_FT = 20902231.0


def hav(a, b):
    """Haversine distance in feet for [lng, lat] points."""
    la1, lo1 = math.radians(a[1]), math.radians(a[0])
    la2, lo2 = math.radians(b[1]), math.radians(b[0])
    h = (math.sin((la2 - la1) / 2) ** 2 +
         math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * R_FT * math.asin(math.sqrt(h))


def fmt_sta(sta_ft):
    if sta_ft is None or not math.isfinite(sta_ft):
        return None
    whole = int(sta_ft // 100)
    rem = sta_ft - whole * 100
    return '%d+%05.2f' % (whole, rem)


def first_point(geom):
    """Pull a representative [lng, lat] from a GeoJSON-style geometry."""
    if not geom:
        return None
    t = geom.get('type')
    c = geom.get('coordinates')
    if c is None:
        return None
    if t == 'Point':
        return c[:2]
    if t == 'LineString' and c:
        return c[0][:2]
    if t == 'MultiLineString' and c and c[0]:
        return c[0][0][:2]
    if t == 'Polygon' and c and c[0]:
        return c[0][0][:2]
    if t == 'MultiPolygon' and c and c[0] and c[0][0]:
        return c[0][0][0][:2]
    return None


def last_point(geom):
    """Same as first_point but for the trailing end of LineStrings."""
    if not geom:
        return None
    t = geom.get('type')
    c = geom.get('coordinates')
    if c is None:
        return None
    if t == 'LineString' and c:
        return c[-1][:2]
    if t == 'MultiLineString' and c and c[-1]:
        return c[-1][-1][:2]
    return None


def project_onto(pt_lnglat, coords):
    """Returns (sta_ft, perp_ft) projecting [lng, lat] pt onto a
    [[lng, lat], ...] polyline. Mirrors roadwalk.html's
    projectOntoAlignment using equirectangular local XY math."""
    pt_lat = pt_lnglat[1]
    cos_lat = math.cos(math.radians(pt_lat))

    def xy(p):
        return ((p[0] - pt_lnglat[0]) * cos_lat * 364000.0,
                (p[1] - pt_lnglat[1]) * 364000.0)

    best_d = float('inf')
    best_cum = 0.0
    cum = 0.0
    for i in range(len(coords) - 1):
        a = coords[i]
        b = coords[i + 1]
        seg_len = hav(a, b)
        ax, ay = xy(a)
        bx, by = xy(b)
        vx, vy = bx - ax, by - ay
        denom = vx * vx + vy * vy
        if denom < 1e-9:
            cum += seg_len
            continue
        t = max(0.0, min(1.0, (-ax * vx + -ay * vy) / denom))
        cx, cy = ax + t * vx, ay + t * vy
        d = math.hypot(cx, cy)
        if d < best_d:
            best_d = d
            best_cum = cum + t * seg_len
        cum += seg_len
    return best_cum, best_d


# ── Load bundle + section ────────────────────────────────────────────────
bundle = json.load(open(os.path.join(DATA, 'prewalk-bundle.json'),
                        encoding='utf-8'))
sec = next(s for s in bundle['sections'] if s['id'] == SEC_ID)
old_align = list(sec['alignment'])
old_v0 = old_align[0]

# Distance from Pathweb start → current v0 (the gap we're filling).
ext_pt = [PATHWEB_START_LNG, PATHWEB_START_LAT]
ext_ft = hav(ext_pt, old_v0)
print(f'Section: {sec.get("name")}')
print(f'Current v0 : [{old_v0[0]:.6f}, {old_v0[1]:.6f}]')
print(f'Pathweb v0 : [{PATHWEB_START_LNG:.4f}, {PATHWEB_START_LAT:.5f}]')
print(f'Gap        : {ext_ft:.1f} ft')
print(f'Old mp_start: {sec.get("mp_start")}  ->  new: {PATHWEB_START_MP}')
print(f'mp_end stays: {sec.get("mp_end")}  (per pathweb_refs note)')

# ── Build the new alignment with Pathweb's start coord as v0 ────────────
new_align = [ext_pt] + old_align
sec['alignment'] = new_align

# Sanity check: the new total length should be old_len + ext_ft.
old_total = sum(hav(old_align[i], old_align[i + 1])
                for i in range(len(old_align) - 1))
new_total = sum(hav(new_align[i], new_align[i + 1])
                for i in range(len(new_align) - 1))
print(f'\nAlignment length: {old_total:.1f} ft  ->  {new_total:.1f} ft '
      f'(+{new_total - old_total:.1f} ft)')

# ── Update section MP bounds ────────────────────────────────────────────
sec['mp_start'] = PATHWEB_START_MP
# mp_end stays put.

# ── Re-project every pin onto the new alignment ─────────────────────────
section_len_ft = (sec['mp_end'] - sec['mp_start']) * 5280

def sta_to_mp(sta_ft):
    if sta_ft is None or not math.isfinite(sta_ft):
        return None
    # Linear interpolation across the bundle's MP range. Note: this is
    # the same convention used elsewhere in the bundle (mp_start at
    # STA 0+00, mp_end at the alignment's tail).
    return sec['mp_start'] + (sta_ft / new_total) * (sec['mp_end'] - sec['mp_start'])

restationed = 0
for p in sec.get('pins', []):
    geom = p.get('geometry')
    pt = first_point(geom)
    if pt is None:
        continue
    sta_ft, perp_ft = project_onto(pt, new_align)
    p['sta_ft'] = round(sta_ft, 1)
    p['sta'] = fmt_sta(sta_ft)
    if p.get('attrs') is None:
        p['attrs'] = {}
    a = p['attrs']
    if 'mp_start' in a:
        a['mp_start'] = round(sta_to_mp(sta_ft), 6)
    # For line features, mp_end follows the line's tail vertex
    end_pt = last_point(geom)
    if end_pt is not None and 'mp_end' in a:
        end_sta, _ = project_onto(end_pt, new_align)
        a['mp_end'] = round(sta_to_mp(end_sta), 6)
    restationed += 1

print(f'\nRe-stationed {restationed} pin(s) in Section D')

# ── Save bundle ──────────────────────────────────────────────────────────
bundle_path = os.path.join(DATA, 'prewalk-bundle.json')
bundle['generated_at'] = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
json.dump(bundle, open(bundle_path, 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
print(f'Wrote {bundle_path}')

# ── Re-station per-section feature geojsons ─────────────────────────────
shifted = 0
for fn in sorted(glob.glob(os.path.join(DATA, f'nps-*-section-{SEC_ID}.geojson'))):
    g = json.load(open(fn, encoding='utf-8'))
    changed = False
    for ft in g.get('features', []):
        pt = first_point(ft.get('geometry'))
        if pt is None:
            continue
        new_sta, dist = project_onto(pt, new_align)
        pp = ft.setdefault('properties', {})
        pp['_sta_ft'] = round(new_sta, 1)
        pp['_sta'] = fmt_sta(new_sta)
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
