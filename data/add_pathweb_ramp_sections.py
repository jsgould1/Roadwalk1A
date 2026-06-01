"""Add four new linear sections to the prewalk bundle, using centerline
features pulled from the user's GRSM_ROADS.geojson where available, and
a straight-line chord for the BLRI segment that isn't in any GRSM file.

New sections:
  F: BLRI 0001DC      — Blue Ridge Parkway MP 470.15 - 470.20
  G: 0163BZ            — Campbell Lead Rd Ramp MP 0.00 - 0.05
  H: 0012AZ            — Gatlinburg Bypass Ramp AZ MP 0.02 - 0.21
  I: 0012BZ            — Gatlinburg Bypass Ramp BZ MP 0.00 - 0.08

The ramp matches were found by comparing Pathweb's documented start/end
coordinates against the endpoints of every LineString in the user's
GRSM_ROADS.geojson (~/Downloads). Three of the four matched a single
feature with strong confidence; BLRI 0001DC is not in the GRSM-only
dataset (BLRI is a separate park) and uses a 2-vertex chord between
the Pathweb coords.

Reads:
  ~/Downloads/GRSM_ROADS.geojson  (authoritative NPS data, user-supplied)
  data/prewalk-bundle.json
  data/grsm-roads.geojson         (working copy)
Writes:
  data/prewalk-bundle.json        (4 new sections appended)
  data/grsm-roads.geojson         (4 new features appended w/ ramp ROUTEIDs)
"""
import copy
import datetime
import json
import math
import os

DATA = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS = os.path.expandvars(r'%USERPROFILE%\Downloads')
R_FT = 20902231.0


def hav_ft(a, b):
    la1, lo1 = math.radians(a[1]), math.radians(a[0])
    la2, lo2 = math.radians(b[1]), math.radians(b[0])
    h = (math.sin((la2 - la1) / 2) ** 2 +
         math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * R_FT * math.asin(math.sqrt(h))


def line_len_ft(coords):
    return sum(hav_ft(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


# ── New-section specs ─────────────────────────────────────────────────────
# pathweb_start/end are documented values (lat first in the XLS — we
# normalize to [lng, lat] here). mp_start ≤ mp_end always; for BLRI that
# means swapping Pathweb's "START" (higher MP) and "END" (lower MP).
SPECS = [
    {
        'id': 'F',
        'name': 'Blue Ridge Parkway (MP 470.15-470.20)',
        'project_code': 'NC NP BLRI 1DC',
        'mp_start': 470.151,
        'mp_end':   470.197,
        # Source: this segment is NOT in the GRSM_ROADS file
        # (different park). Falls back to a 2-vertex chord between the
        # two Pathweb-documented coords.
        'source': 'chord',
        # v0 at lower MP, last vertex at higher MP
        'chord': [
            [-83.300601, 35.505881],   # MP 470.151 (Pathweb "END")
            [-83.301022, 35.505364],   # MP 470.197 (Pathweb "START")
        ],
        'pathweb_ref': {
            'id': 12611, 'role': 'mainline',
            'mp_start': 470.151, 'mp_end': 470.197,
            'url': 'https://pathweb.pathwayservices.com/rip/sections/12611/locations/9756',
        },
        'new_routeid': 'BLRI-0001DC',
        'aerial_choice': 'esri_aerial',
    },
    {
        'id': 'G',
        'name': 'Campbell Lead Rd Ramp (MP 0.00-0.05)',
        'project_code': 'TN NP GRSM 163BZ',
        'mp_start': 0.000,
        'mp_end':   0.049,
        # Feature 53 in user's GRSM_ROADS.geojson, ROUTEID GRSM-0163.
        # Direction is reversed vs Pathweb so we flip the vertex order
        # before saving (Pathweb start should land near alignment v0).
        'source': 'grsm_roads_feature',
        'src_index': 53,
        'reverse': True,
        'pathweb_start': [-83.527830, 35.713972],   # MP 0.000
        'pathweb_end':   [-83.527227, 35.714434],   # MP 0.049
        'pathweb_ref': {
            'id': 11145, 'role': 'access_ramp',
            'mp_start': 0.0, 'mp_end': 0.049,
            'url': 'https://pathweb.pathwayservices.com/rip/sections/11145/locations/2',
        },
        'new_routeid': 'GRSM-0163BZ',
        'aerial_choice': 'esri_aerial',
    },
    {
        'id': 'H',
        'name': 'Gatlinburg Bypass Ramp 0012AZ (MP 0.02-0.21)',
        'project_code': 'TN NP GRSM 12AZ',
        'mp_start': 0.015,
        'mp_end':   0.205,
        # Feature 40 in user's GRSM_ROADS.geojson, ROUTEID GRSM-0012.
        # Reversed vs Pathweb.
        'source': 'grsm_roads_feature',
        'src_index': 40,
        'reverse': True,
        'pathweb_start': [-83.514701, 35.724854],   # MP 0.015
        'pathweb_end':   [-83.516846, 35.726919],   # MP 0.205
        'pathweb_ref': {
            'id': 11143, 'role': 'bypass_ramp_AZ',
            'mp_start': 0.015, 'mp_end': 0.205,
            'url': 'https://pathweb.pathwayservices.com/rip/sections/11143/locations/11',
        },
        'new_routeid': 'GRSM-0012AZ',
        'aerial_choice': 'esri_aerial',
    },
    {
        'id': 'I',
        'name': 'Gatlinburg Bypass Ramp 0012BZ (MP 0.00-0.08)',
        'project_code': 'TN NP GRSM 12BZ',
        'mp_start': 0.000,
        'mp_end':   0.075,
        # Feature 50 in user's GRSM_ROADS.geojson, ROUTEID GRSM-0012.
        # Already in correct direction vs Pathweb.
        'source': 'grsm_roads_feature',
        'src_index': 50,
        'reverse': False,
        'pathweb_start': [-83.514834, 35.725123],   # MP 0.000
        'pathweb_end':   [-83.514016, 35.725810],   # MP 0.075
        'pathweb_ref': {
            'id': 11147, 'role': 'bypass_ramp_BZ',
            'mp_start': 0.0, 'mp_end': 0.075,
            'url': 'https://pathweb.pathwayservices.com/rip/sections/11147/locations/6',
        },
        'new_routeid': 'GRSM-0012BZ',
        'aerial_choice': 'esri_aerial',
    },
]


# ── Load source files ─────────────────────────────────────────────────────
nps_path = os.path.join(DOWNLOADS, 'GRSM_ROADS.geojson')
nps = json.load(open(nps_path, encoding='utf-8'))
nps_feats = nps['features']

bundle_path = os.path.join(DATA, 'prewalk-bundle.json')
bundle = json.load(open(bundle_path, encoding='utf-8'))
existing_ids = {s['id'] for s in bundle['sections']}

local_path = os.path.join(DATA, 'grsm-roads.geojson')
local = json.load(open(local_path, encoding='utf-8'))

# Track new features to add to data/grsm-roads.geojson
new_road_features = []


def extract_line(feat):
    """Return a list of [lng, lat] vertices, picking the longest line from
    a LineString or MultiLineString feature."""
    geom = feat.get('geometry') or {}
    gt = geom.get('type')
    coords = geom.get('coordinates') or []
    if gt == 'LineString':
        return [v[:2] for v in coords]
    if gt == 'MultiLineString' and coords:
        # Pick the line whose total length is greatest
        best = max(coords, key=line_len_ft)
        return [v[:2] for v in best]
    raise ValueError(f'Unsupported geometry type: {gt}')


# ── Build each section ────────────────────────────────────────────────────
print('Building new sections:')
print()
for spec in SPECS:
    sid = spec['id']
    if sid in existing_ids:
        print(f'  [{sid}] {spec["name"]}  -- SKIP, already in bundle')
        continue

    # Resolve alignment + the feature we'll add to grsm-roads
    if spec['source'] == 'chord':
        alignment = [list(p) for p in spec['chord']]
        new_feat = {
            'type': 'Feature',
            'properties': {
                'ROUTEID': spec['new_routeid'],
                'RDNAME': spec['name'].split(' (')[0],
                'NOTES': ('chord approximation between two Pathweb '
                          'control points — replace when authoritative '
                          'geometry is available'),
                'PARK_FUNCT': 'Park Roads',
                '_source': 'pathweb-chord',
                '_pathweb_section_id': spec['pathweb_ref']['id'],
            },
            'geometry': {'type': 'LineString',
                         'coordinates': [list(v) for v in alignment]},
        }
    else:
        src = nps_feats[spec['src_index']]
        line = extract_line(src)
        if spec.get('reverse'):
            line = list(reversed(line))
        alignment = [list(v) for v in line]
        # Copy the source feature's properties, override ROUTEID, and
        # note the provenance.
        new_props = copy.deepcopy(src.get('properties') or {})
        new_props['ROUTEID'] = spec['new_routeid']
        new_props['_source'] = ('GRSM_ROADS.geojson feature '
                                f'{spec["src_index"]} ('
                                f'{"reversed" if spec.get("reverse") else "as-is"})')
        new_props['_pathweb_section_id'] = spec['pathweb_ref']['id']
        new_feat = {
            'type': 'Feature',
            'properties': new_props,
            'geometry': {'type': 'LineString',
                         'coordinates': [list(v) for v in alignment]},
        }

    # Sanity: report distance from Pathweb start/end to alignment endpoints
    aln_len = line_len_ft(alignment)
    ps = spec.get('pathweb_start') or spec.get('chord', [None])[0]
    pe = spec.get('pathweb_end')   or spec.get('chord', [None, None])[1]
    d_start = hav_ft(ps, alignment[0])  if ps else None
    d_end   = hav_ft(pe, alignment[-1]) if pe else None

    nominal_ft = (spec['mp_end'] - spec['mp_start']) * 5280

    print(f'  [{sid}] {spec["name"]}')
    print(f'        source       : {spec["source"]}'
          + (f' (idx {spec["src_index"]}'
             + (", reversed" if spec.get('reverse') else '')
             + ')' if spec['source'] == 'grsm_roads_feature' else ''))
    print(f'        vertices     : {len(alignment)}')
    print(f'        align length : {aln_len:.1f} ft  '
          f'(nominal MP-range = {nominal_ft:.1f} ft, diff = {aln_len-nominal_ft:+.1f})')
    if d_start is not None:
        print(f'        v0 to Pathweb start : {d_start:.1f} ft')
    if d_end is not None:
        print(f'        vN to Pathweb end   : {d_end:.1f} ft')

    new_section = {
        'id': sid,
        'name': spec['name'],
        'type': 'linear',
        'project_code': spec['project_code'],
        'mp_start': spec['mp_start'],
        'mp_end':   spec['mp_end'],
        'alignment': alignment,
        'pathweb_refs': [spec['pathweb_ref']],
        'sub_alignments': [],
        'pins': [],
        '_aerial_choice': spec['aerial_choice'],
    }
    bundle['sections'].append(new_section)
    new_road_features.append(new_feat)
    print()


# ── Persist outputs ───────────────────────────────────────────────────────
bundle['generated_at'] = (datetime.datetime.now(datetime.UTC)
                          .strftime('%Y-%m-%dT%H:%M:%SZ'))
json.dump(bundle, open(bundle_path, 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
print(f'Wrote {bundle_path}  ({len(bundle["sections"])} sections total)')

# Append the new features into data/grsm-roads.geojson so future bundles
# can pick them up by ROUTEID lookup.
local['features'].extend(new_road_features)
json.dump(local, open(local_path, 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
print(f'Wrote {local_path}  '
      f'({len(local["features"])} features total, +{len(new_road_features)} new)')

print('\nDone.')
