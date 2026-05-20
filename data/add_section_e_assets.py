"""Add Section E (Newfound Gap Rd NC #3, MP 30.00-31.25) assets to the
prewalk bundle WITHOUT re-running bundle_prewalk.py. Pulls signs, gates,
mile_markers, parking, culverts and ngs_monuments from the park-wide
GRSM sources, keeps only features whose nearest section is E (under
the corridor tolerance), and writes both:

  data/nps-{kind}-section-E.geojson  (per-section clip files, mirroring A-D)
  data/prewalk-bundle.json           (Section E pins appended in place)

Re-runnable: existing E pins are replaced; A-D are untouched.
"""
import json, math, os, secrets, datetime
from collections import Counter

DATA = os.path.dirname(os.path.abspath(__file__))
R_FT = 20902231.0
FT_PER_M = 3.28084

# Corridor tolerances. Road-adjacent kinds use the same 300 ft the existing
# filter_datasets_to_sections.py uses. Monuments use 200 m (~656 ft) matching
# bundle_prewalk.py's NEW_KIND_CORRIDOR_M['ngs_monument'].
TOL_FT = {
    'sign':         300.0,
    'mile_marker':  300.0,
    'gate':         300.0,
    'parking':      300.0,
    'culvert':      300.0,
    'ngs_monument': 200.0 * FT_PER_M,
}

ID_PREFIX = {
    'sign': 'SIG', 'mile_marker': 'MIL', 'gate': 'GAT',
    'parking': 'PAR', 'culvert': 'CUL', 'ngs_monument': 'NGS',
}

# --- ULID ----------------------------------------------------------------
_ULID = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
def make_ulid():
    t = int(datetime.datetime.utcnow().timestamp() * 1000)
    s = ''
    for _ in range(10):
        s = _ULID[t % 32] + s
        t //= 32
    rb = secrets.token_bytes(16)
    return s + ''.join(_ULID[b % 32] for b in rb)

# --- Geometry helpers ----------------------------------------------------
def hav_ft(a, b):
    la1, lo1 = math.radians(a[0]), math.radians(a[1])
    la2, lo2 = math.radians(b[0]), math.radians(b[1])
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R_FT * math.asin(math.sqrt(h))

def sta_on_polyline(pt, coords_latlng):
    """Return (cum_ft, dist_ft) for pt projected onto the latlng polyline."""
    lat0 = pt[0]
    cos_lat = math.cos(math.radians(lat0))
    def xy(p):
        return ((p[1] - pt[1]) * cos_lat * 364000.0, (p[0] - pt[0]) * 364000.0)
    best_d = float('inf')
    best_cum = 0.0
    cum = 0.0
    for i in range(len(coords_latlng) - 1):
        a, b = coords_latlng[i], coords_latlng[i + 1]
        seg_len = hav_ft(a, b)
        ax, ay = xy(a)
        bx, by = xy(b)
        vx, vy = bx - ax, by - ay
        denom = vx * vx + vy * vy
        if denom < 1e-9:
            cum += seg_len
            continue
        t = max(0.0, min(1.0, (-ax * vx + -ay * vy) / denom))
        proj = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        d = hav_ft(pt, proj)
        if d < best_d:
            best_d = d
            best_cum = cum + t * seg_len
        cum += seg_len
    return best_cum, best_d

def sta_fmt(sta_ft):
    whole = int(sta_ft // 100)
    rem = sta_ft - whole * 100
    return "%d+%05.2f" % (whole, rem)

def first_point_lnglat(geom):
    if not geom:
        return None
    t = geom.get('type')
    c = geom.get('coordinates')
    if t == 'Point': return c[:2]
    if t == 'LineString': return c[0][:2]
    if t == 'MultiLineString': return c[0][0][:2]
    if t == 'Polygon': return c[0][0][:2]
    if t == 'MultiPolygon': return c[0][0][0][:2]
    return None

# --- Load section alignments (latlng) -----------------------------------
SECTION_FILES = {
    'A': 'section-A-gatlinburg-bypass.geojson',
    'B': 'section-B-newfound-gap-NC-1.geojson',
    'C': 'section-C-newfound-gap-NC-2.geojson',
    'D': 'section-D-newfound-gap-TN.geojson',
    'E': 'section-E-newfound-gap-NC-3.geojson',
}
sec_alignments = {}
for lab, fn in SECTION_FILES.items():
    p = os.path.join(DATA, fn)
    if not os.path.exists(p):
        print('  ! missing %s; skipping' % fn)
        continue
    coords_lnglat = json.load(open(p, encoding='utf-8'))['features'][0]['geometry']['coordinates']
    sec_alignments[lab] = [(c[1], c[0]) for c in coords_lnglat]
print('Loaded alignments for: %s' % sorted(sec_alignments))

# --- Compact attrs (subset of bundle_prewalk.py compact_pin field map) ---
ATTR_MAP = [
    ('LOC_NAME', 'loc_name'), ('ROAD', 'road'),
    ('NAME', 'name'), ('SHORTNAME', 'short_name'),
    ('MILE_LABEL', 'mile_label'),
    ('TYPE', 'type'),
    ('CULVERTMATERIAL', 'material'), ('FMSS_CULVERT_TYPE', 'fmss_type'),
    ('FMSS_LOC', 'fmss_loc'), ('FMSS_ASSET', 'fmss_asset'),
    ('KEY_', 'key_code'), ('NOTES', 'notes'),
    ('MATERIAL', 'material'),
    # parking specifics
    ('PARKING_SPOTS', 'parking_spots'), ('DESCRIPTION', 'description'),
    # monument specifics
    ('PID', 'pid'), ('MARKER', 'marker'), ('STABILITY', 'stability'),
    ('SETTING', 'setting'), ('STAMPING', 'stamping'), ('COUNTY', 'county'),
    ('ORTHO_HT', 'ortho_ht'), ('VERT_DATUM', 'vert_datum'),
    ('LAST_RECV', 'last_recv'), ('LAST_COND', 'last_cond'),
    ('STATE', 'state'), ('GlobalID', 'global_id'),
]

def compact_attrs(props):
    out = {}
    for src, dst in ATTR_MAP:
        v = props.get(src)
        if v in (None, '', 'None'):
            continue
        if isinstance(v, str):
            v = v.strip()
        if v in (None, '', 'None'):
            continue
        out[dst] = v
    return out

# --- Per-source assignment ----------------------------------------------
SOURCES = [
    # (kind,            file,                                source_label)
    ('sign',         'grsm-signs.geojson',                'nps-gis'),
    ('mile_marker',  'grsm-mile-markers.geojson',         'nps-gis'),
    ('gate',         'grsm-gates.geojson',                'nps-gis'),
    ('parking',      'grsm-parking.geojson',              'nps-gis'),
    ('culvert',      'grsm-road-culverts.geojson',        'nps-gis'),
    # Monuments come from the Reports/ NGS_MONUMENTS geojson when present.
    ('ngs_monument', 'Reports/NGS_MONUMENTS (1).geojson', 'ngs'),
]

e_pins = []
kind_counter = {}

for kind, src_path, source_label in SOURCES:
    full = os.path.join(DATA, src_path)
    if not os.path.exists(full):
        print('  ! missing source: %s -- %s skipped' % (src_path, kind))
        continue
    src = json.load(open(full, encoding='utf-8'))
    tol = TOL_FT[kind]
    feats_to_e = []
    for ft in src.get('features', []):
        g = ft.get('geometry')
        c = first_point_lnglat(g) if g else None
        if c is None:
            continue
        lng, lat = c
        pt = (lat, lng)
        # Find nearest section among A-E (within tolerance)
        best_lab, best_d, best_sta = None, tol + 1, None
        for lab, coords in sec_alignments.items():
            sta, d = sta_on_polyline(pt, coords)
            if d <= tol and d < best_d:
                best_lab, best_d, best_sta = lab, d, sta
        if best_lab != 'E':
            continue   # only claim features whose closest section is E
        # Build per-section feature (for the geojson file)
        props = dict(ft.get('properties') or {})
        props['_section'] = 'E'
        props['_sta_ft'] = round(best_sta, 1)
        props['_sta'] = sta_fmt(best_sta)
        props['_dist_from_alignment_ft'] = round(best_d, 1)
        props['_reported_in'] = ['NPS-GIS'] if source_label != 'ngs' else ['NGS']
        props['_report_cross_refs'] = {}
        feats_to_e.append({'type': 'Feature', 'properties': props, 'geometry': g})
        # Build the bundle pin
        kind_counter[kind] = kind_counter.get(kind, 0) + 1
        pin_id = "E-%s-%03d" % (ID_PREFIX[kind], kind_counter[kind])
        pin = {
            'id': pin_id,
            'ulid': make_ulid(),
            'kind': kind,
            'source': source_label,
            'status': 'pending',
            'geometry': g,
            'sta_ft': round(best_sta, 1),
            'sta':    sta_fmt(best_sta),
            'reported_in': props['_reported_in'],
            'report_refs': {},
        }
        attrs = compact_attrs(props)
        attrs['_dist_from_alignment_ft'] = round(best_d, 1)
        if attrs:
            pin['attrs'] = attrs
        e_pins.append(pin)
    feats_to_e.sort(key=lambda f: f['properties']['_sta_ft'])
    # Match the plural file-naming used for sections A-D in PIN_SOURCES.
    file_kind = {
        'sign':         'signs',
        'gate':         'gates',
        'culvert':      'culverts',
        'mile_marker':  'mile-markers',
        'parking':      'parking',
        'ngs_monument': 'monuments',
    }.get(kind, kind)
    out_name = 'nps-%s-section-E.geojson' % file_kind
    out_path = os.path.join(DATA, out_name)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({
            'type': 'FeatureCollection',
            'metadata': {
                'dataset': file_kind, 'section': 'E', 'count': len(feats_to_e),
                'tolerance_ft': round(tol, 1),
                'source': src_path,
            },
            'features': feats_to_e,
        }, f, indent=2)
    print('  %-14s -> %s  (%d features)' % (kind, out_name, len(feats_to_e)))

# --- Sort + splice pins into the bundle ---------------------------------
e_pins.sort(key=lambda p: (p.get('sta_ft', 0), p['id']))

bundle = json.load(open(os.path.join(DATA, 'prewalk-bundle.json'), encoding='utf-8'))
sec_e = next((s for s in bundle['sections'] if s['id'] == 'E'), None)
if not sec_e:
    raise SystemExit('Section E not in bundle; run the splice step first.')
sec_e['pins'] = e_pins

# Dedupe: any pin whose (kind, first-point) matches one of E's pins is
# removed from A-D. That happens at the C/E boundary where features sit
# almost equidistant from both alignments — we let E claim them (its
# alignment is now nearer) and drop the stale copy from C.
def first_pt_key(p):
    g = p.get('geometry') or {}
    c = g.get('coordinates')
    if c is None:
        return None
    cur = c
    while isinstance(cur, list) and len(cur) > 0 and isinstance(cur[0], list):
        cur = cur[0]
    if not isinstance(cur, list) or len(cur) < 2:
        return None
    return (p['kind'], round(cur[0], 6), round(cur[1], 6))

e_keys = {first_pt_key(p) for p in e_pins if first_pt_key(p) is not None}
removed = []
for s in bundle['sections']:
    if s['id'] == 'E':
        continue
    keep = []
    for p in s['pins']:
        k = first_pt_key(p)
        if k is not None and k in e_keys:
            removed.append((s['id'], p['id']))
            continue
        keep.append(p)
    s['pins'] = keep
if removed:
    print('De-duplicated %d pin(s) moved from other sections into E:' % len(removed))
    for sid, pid in removed:
        print('  removed %s from %s (now in Section E)' % (pid, sid))

bundle['generated_at'] = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
json.dump(bundle, open(os.path.join(DATA, 'prewalk-bundle.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)

# --- Summary ------------------------------------------------------------
counts = Counter(p['kind'] for p in e_pins)
print()
print('Section E now has %d pins:' % len(e_pins))
for k, n in counts.most_common():
    print('  %-14s %d' % (k, n))
print('Bundle saved -> data/prewalk-bundle.json')
