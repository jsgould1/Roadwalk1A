"""Three-step bundle update:

  1. Re-trim the CURRENT Section D (TN, 0010N) end from MP 6.50 down to
     Pathweb's authoritative MP 6.132 at (35.637327, -83.495585). Drops
     ~1,945 ft off the south end; pins past the new endpoint clamp to it
     (reported so the user can decide to keep or delete).

  2. Swap the section IDs of D and E:
       OLD D (TN content) → NEW E
       OLD E (NC NB content) → NEW D
     Every pin id is rewritten to match its new section letter.
     ULIDs stay untouched — that's the canonical identity that lets any
     inspection records (when they exist) keep tracking the right asset.

  3. Rename:
       C → "Newfound Gap Rd NC #2 SB (MP 30.00-31.96)"
       D → "Newfound Gap Rd NC #2 NB (MP 30.45-31.59)"   (was E)
       E → "Newfound Gap Rd TN (MP 1.86-6.13)"           (was D, trimmed)

The user confirmed no field data has been entered on the current D or E
yet, so an IDB migration is unnecessary.
"""
import datetime
import glob
import json
import math
import os
import shutil

DATA = os.path.dirname(os.path.abspath(__file__))
BUNDLE_PATH = os.path.join(DATA, 'prewalk-bundle.json')
R_FT = 20902231.0

# Pathweb-authoritative south endpoint of Section D (TN, 0010N).
TN_NEW_END_LNGLAT = [-83.495585, 35.637327]
TN_NEW_MP_END     = 6.132

NEW_NAME_C = 'Newfound Gap Rd NC #2 SB (MP 30.00-31.96)'
NEW_NAME_D = 'Newfound Gap Rd NC #2 NB (MP 30.45-31.59)'   # was E
NEW_NAME_E = 'Newfound Gap Rd TN (MP 1.86-6.13)'           # was D, trimmed


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


# ─────────────────────────────────────────────────────────────────────────
# Load bundle
# ─────────────────────────────────────────────────────────────────────────
print(f'Loading {BUNDLE_PATH}')
bundle = json.load(open(BUNDLE_PATH, encoding='utf-8'))
sections_by_id = {s['id']: s for s in bundle['sections']}

old_D = sections_by_id['D']
old_E = sections_by_id['E']
sec_C = sections_by_id['C']

print(f"\nBefore:")
print(f"  C: {sec_C['name']!r}  ({len(sec_C.get('pins', []))} pins)")
print(f"  D: {old_D['name']!r}  ({len(old_D.get('pins', []))} pins)")
print(f"  E: {old_E['name']!r}  ({len(old_E.get('pins', []))} pins)")

# ─────────────────────────────────────────────────────────────────────────
# Step 1: Re-trim old Section D (TN) end from MP 6.50 → 6.132.
# ─────────────────────────────────────────────────────────────────────────
print(f'\n── Step 1: trim TN to MP {TN_NEW_MP_END} ──')

old_align = list(old_D['alignment'])
old_total = line_len_ft(old_align)
old_mp_start = old_D['mp_start']
old_mp_end   = old_D['mp_end']

sta_end_trim, dist_end = project_onto(TN_NEW_END_LNGLAT, old_align)
print(f'  Pathweb MP {TN_NEW_MP_END} projects onto current alignment at '
      f'sta {sta_end_trim:.1f} ft, {dist_end:.1f} ft off the polyline')
print(f'  Trimming {old_total - sta_end_trim:.1f} ft off the south end')

clipped = clip_polyline(old_align, 0.0, sta_end_trim)
# Snap last vertex onto Pathweb coord exactly
if hav_ft(clipped[-1], TN_NEW_END_LNGLAT) > 0.5:
    clipped[-1] = list(TN_NEW_END_LNGLAT)

new_total = line_len_ft(clipped)
print(f'  New alignment: {len(clipped)} verts, {new_total:.1f} ft')

# Re-station pins. Lat/lng unchanged; sta_ft + attrs.mp_* recomputed.
def sta_to_mp(sta_ft):
    return old_mp_start + (sta_ft / new_total) * (TN_NEW_MP_END - old_mp_start)

restationed = 0
clamped_to_end = 0
SLOP_FT = 1.0
for p in old_D.get('pins', []):
    g = p.get('geometry'); pt = first_point(g)
    if pt is None: continue
    sta_ft, _ = project_onto(pt, clipped)
    p['sta_ft'] = round(sta_ft, 1)
    p['sta']    = fmt_sta(sta_ft)
    a = p.setdefault('attrs', {})
    if 'mp_start' in a:
        a['mp_start'] = round(sta_to_mp(sta_ft), 6)
    end_pt = last_point(g)
    if end_pt is not None and 'mp_end' in a:
        end_sta, _ = project_onto(end_pt, clipped)
        a['mp_end'] = round(sta_to_mp(end_sta), 6)
    if sta_ft > new_total - SLOP_FT:
        clamped_to_end += 1
    restationed += 1

print(f'  Re-stationed {restationed} pins; {clamped_to_end} clamped to '
      f'the new south endpoint (were past MP {TN_NEW_MP_END})')

# Apply trim to old_D in-place. Section IDENTITY (id=D) still — the swap
# in step 2 is the one that flips D to E.
old_D['alignment'] = clipped
old_D['mp_end']    = TN_NEW_MP_END
old_D['pins'].sort(key=lambda x: x.get('sta_ft', 0) or 0)

# ─────────────────────────────────────────────────────────────────────────
# Step 2: Swap D ↔ E (id field + every "D-…" or "E-…" pin id).
# ─────────────────────────────────────────────────────────────────────────
print(f'\n── Step 2: swap D ↔ E ──')

def reletter_pin_ids(section, old_letter, new_letter):
    """Rewrite '<old_letter>-XXX-NNN' → '<new_letter>-XXX-NNN' on every pin
    in this section. Also update attrs.name when it still matches the
    auto-generated pattern (so user-edited names are preserved)."""
    autoName = ('-')
    n_changed = 0
    for p in section.get('pins', []):
        pid = p.get('id')
        if pid and pid.startswith(old_letter + '-'):
            new_id = new_letter + pid[1:]
            # If attrs.name still matches the auto label, rename it too.
            a = p.setdefault('attrs', {})
            if a.get('name') == pid:
                a['name'] = new_id
            p['id'] = new_id
            n_changed += 1
    return n_changed

# Phase A: rename pins on each side BEFORE swapping ids so we don't
# accidentally double-prefix when iterating.
n_d_to_e = reletter_pin_ids(old_D, 'D', 'E')
n_e_to_d = reletter_pin_ids(old_E, 'E', 'D')
print(f'  Renamed {n_d_to_e} pin id(s) D-… → E-… on the TN section')
print(f'  Renamed {n_e_to_d} pin id(s) E-… → D-… on the NB section')

# Phase B: flip the section.id field.
old_D['id'] = 'E'   # the TN section is now Section E
old_E['id'] = 'D'   # the NC NB section is now Section D

# ─────────────────────────────────────────────────────────────────────────
# Step 3: Rename C, new D, new E.
# ─────────────────────────────────────────────────────────────────────────
print(f'\n── Step 3: rename ──')

sec_C['name'] = NEW_NAME_C
# old_E is now the NC NB section with id="D"
old_E['name'] = NEW_NAME_D
# old_D is now the TN section with id="E"
old_D['name'] = NEW_NAME_E

print(f'  C → {sec_C["name"]!r}')
print(f'  D → {old_E["name"]!r}')
print(f'  E → {old_D["name"]!r}')

# Sort sections back to alphabetical order (A,B,C,D,E,…) so the in-file
# order matches the section letter.
bundle['sections'].sort(key=lambda s: s['id'])

# ─────────────────────────────────────────────────────────────────────────
# Save
# ─────────────────────────────────────────────────────────────────────────
bundle['generated_at'] = (datetime.datetime.now(datetime.UTC)
                          .strftime('%Y-%m-%dT%H:%M:%SZ'))

# Backup before overwriting
backup_path = BUNDLE_PATH + '.bak'
shutil.copy(BUNDLE_PATH, backup_path)
print(f'\nBacked up old bundle to {os.path.basename(backup_path)}')

with open(BUNDLE_PATH, 'w', encoding='utf-8') as f:
    json.dump(bundle, f, ensure_ascii=False, indent=1)

print(f'Wrote updated {os.path.basename(BUNDLE_PATH)}')
print('\nFinal section list:')
for s in bundle['sections']:
    pins = len(s.get('pins', []))
    print(f"  {s['id']}: {s['name']!r}  ({pins} pins)")

# ─────────────────────────────────────────────────────────────────────────
# Rename per-section feature GeoJSON files (nps-*-section-D.* ↔ -E.*)
# ─────────────────────────────────────────────────────────────────────────
print(f'\n── Renaming per-section GeoJSON files ──')

# We need to atomically swap D ↔ E filenames. Do it via temp suffix.
d_files = glob.glob(os.path.join(DATA, 'nps-*-section-D*'))
e_files = glob.glob(os.path.join(DATA, 'nps-*-section-E*'))

# Stash D files under a temp suffix
for fp in d_files:
    tmp = fp.replace('-section-D', '-section-D__SWAP')
    os.rename(fp, tmp)
# Rename E → D
for fp in e_files:
    new = fp.replace('-section-E', '-section-D')
    os.rename(fp, new)
    print(f'  E → D: {os.path.basename(new)}')
# Rename temp (was D) → E
for fp in glob.glob(os.path.join(DATA, 'nps-*-section-D__SWAP*')):
    new = fp.replace('-section-D__SWAP', '-section-E')
    os.rename(fp, new)
    print(f'  D → E: {os.path.basename(new)}')

print('\nDone.')
