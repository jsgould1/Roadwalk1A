"""Disambiguate which Avenza layer holds culvert pins.

For each candidate feature table, prints:
  - how many photos in avenza_media link to that layer's features
  - the first 8 features' names + descriptions, so we can tell from the
    naming convention (e.g. "C43" + "Stone head wall" → culverts)

Usage:
    python inspect_gpkg_layers.py "C:\\path\\to\\your.gpkg"

Paste the output back to me — I'll write the preprocessor against
whichever table(s) carry the culvert data.
"""
import os
import sqlite3
import sys

if len(sys.argv) < 2:
    print('Usage: python inspect_gpkg_layers.py "C:\\path\\to\\file.gpkg"')
    sys.exit(1)

gpkg_path = sys.argv[1]
if not os.path.exists(gpkg_path):
    print(f'File not found: {gpkg_path}')
    sys.exit(1)

# Candidate feature tables — the four that have non-zero row counts from
# the earlier inspect_gpkg.py run. Add to this list if you have other
# tables you want to spot-check.
TABLES = [
    'FHWA _ EFL RFP _ GRSM Pave Pres 20251',
    'Limits',
    'Structures',
    'Structures1',
    'Pull Out Parkings',
    'Pull_off Parkings',
    'Pull_out Parkings',
]

conn = sqlite3.connect(gpkg_path)
cur  = conn.cursor()

# Photo distribution across all layers (useful overview)
print('── PHOTO DISTRIBUTION (avenza_media) ──')
cur.execute('SELECT layer_id, COUNT(*) FROM avenza_media GROUP BY layer_id ORDER BY layer_id;')
for layer_id, n in cur.fetchall():
    print(f'  layer_id={layer_id:>3}: {n:>4} photo(s)')
print()

for t in TABLES:
    try:
        cur.execute(f'SELECT COUNT(*) FROM "{t}";')
        rc = cur.fetchone()[0]
    except sqlite3.Error as e:
        print(f'═══ {t} ═══  (table missing or error: {e})')
        continue
    print(f'═══ {t} ═══  ({rc} feature row(s))')
    if rc == 0:
        print('  (empty — skipping)')
        print()
        continue

    # Which layer_id is this table associated with? avenza_layer_id is
    # stored on each row; usually all rows in a table share one layer_id.
    cur.execute(f'SELECT DISTINCT avenza_layer_id FROM "{t}" ORDER BY avenza_layer_id;')
    layer_ids = [r[0] for r in cur.fetchall()]
    print(f'  avenza_layer_id values used: {layer_ids}')

    # How many photos are linked to features in this table?
    photo_count = 0
    if layer_ids:
        placeholders = ','.join('?' * len(layer_ids))
        cur.execute(
            f'SELECT COUNT(*) FROM avenza_media WHERE layer_id IN ({placeholders})',
            layer_ids
        )
        photo_count = cur.fetchone()[0]
    print(f'  photos linked to these layer_ids: {photo_count}')

    # First 8 features' names + descriptions
    cur.execute(f'SELECT avenza_name, avenza_description FROM "{t}" LIMIT 8;')
    print(f'  first 8 (name | description):')
    for n, d in cur.fetchall():
        n_s = (n or '').strip() or '(blank)'
        d_s = (d or '').strip() or '(blank)'
        if len(n_s) > 30: n_s = n_s[:27] + '...'
        if len(d_s) > 60: d_s = d_s[:57] + '...'
        print(f'    {n_s:<32} | {d_s}')
    print()

conn.close()
print('── DONE ── Paste this output back to me.')
