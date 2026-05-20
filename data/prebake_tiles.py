"""Pre-bake basemap tiles into a RoadWalk cache file you can import via the
in-app Tile Cache Manager (Import Cache File button).

Builds a NDJSON file matching the v2 format _tcmExport writes:
  line 1: { "format": "roadwalk-tiles", "version": 2, "exported": ISO, "count": N }
  line 2..N+1: { "k": "<layer>/<z>/<x>/<y>", "v": "data:image/jpeg;base64,...",
                 "layer": "<layer>", "bytes": <int> }

Usage:
  python data/prebake_tiles.py --section E --layer esri_aerial --min-z 14 --max-z 19
  python data/prebake_tiles.py --section E --layer usgs_hydro  --min-z 14 --max-z 17
  python data/prebake_tiles.py --bbox 35.50,-83.31,35.52,-83.30 --layer esri_aerial ...

Coverage is a route CORRIDOR — for each alignment segment we buffer it by
--buffer-ft and collect every tile whose pixel-bbox intersects the buffered
segment. Same idea the in-app "Route corridor" uses.
"""
import argparse, base64, datetime, json, math, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import urllib.request
except ImportError:
    print('urllib missing — Python install is broken', file=sys.stderr)
    sys.exit(1)

DATA = os.path.dirname(os.path.abspath(__file__))

# Tile-URL templates per layer (matches BASEMAPS in roadwalk.html).
LAYER_URLS = {
    'esri_aerial': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    'esri_street': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
    'usgs_imagery': 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}',
    'usgs_topo':    'https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}',
    'usgs_imagerytopo': 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}',
    'usgs_hydro':   'https://basemap.nationalmap.gov/arcgis/rest/services/USGSHydroCached/MapServer/tile/{z}/{y}/{x}',
    'osm':          'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
}

SECTION_FILES = {
    'A': 'section-A-gatlinburg-bypass.geojson',
    'B': 'section-B-newfound-gap-NC-1.geojson',
    'C': 'section-C-newfound-gap-NC-2.geojson',
    'D': 'section-D-newfound-gap-TN.geojson',
    'E': 'section-E-newfound-gap-NC-3.geojson',
}


def deg2tile(lng, lat, z):
    """XYZ tile indices for a lng/lat at zoom z (slippy-map standard)."""
    n = 2 ** z
    x = int((lng + 180.0) / 360.0 * n)
    lat_r = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n)
    return x, y


def tile_bounds(x, y, z):
    """Return (lng_min, lat_min, lng_max, lat_max) for tile (x, y, z)."""
    n = 2 ** z

    def t2lng(xx): return xx / n * 360.0 - 180.0
    def t2lat(yy):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))
    return t2lng(x), t2lat(y + 1), t2lng(x + 1), t2lat(y)


def haversine_m(a, b):
    R = 6371000.0
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    dp = math.radians(b[1] - a[1])
    dl = math.radians(b[0] - a[0])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def load_section_alignment(section_id):
    fn = SECTION_FILES.get(section_id)
    if not fn:
        raise SystemExit('Unknown section: %s (choices: %s)' %
                         (section_id, ', '.join(SECTION_FILES)))
    path = os.path.join(DATA, fn)
    if not os.path.exists(path):
        raise SystemExit('Missing alignment file: %s' % path)
    return json.load(open(path, encoding='utf-8'))['features'][0]['geometry']['coordinates']


def parse_bbox(s):
    try:
        a, b, c, d = [float(p) for p in s.split(',')]
        return min(b, d), min(a, c), max(b, d), max(a, c)   # → lng_min, lat_min, lng_max, lat_max
    except Exception:
        raise SystemExit('--bbox needs "lat1,lng1,lat2,lng2"')


def collect_corridor_tiles(coords_lnglat, buffer_ft, z):
    """Walk each segment, collect every tile within buffer_ft of the segment.

    For each segment, we buffer the segment as a thick polyline and take the
    tile bbox that intersects. A tile is included if its centre is within
    buffer_ft of the line, OR the segment passes through the tile."""
    buffer_m = buffer_ft / 3.28084

    # First, simple bbox of all coords + buffer in degrees (~big enough)
    lngs = [c[0] for c in coords_lnglat]
    lats = [c[1] for c in coords_lnglat]
    # Buffer in degrees — over-estimate is fine, we cull later
    lat_avg = sum(lats) / len(lats)
    deg_per_ft_lat = 1.0 / 364000.0
    deg_per_ft_lng = 1.0 / (364000.0 * math.cos(math.radians(lat_avg)))
    pad_lat = buffer_ft * deg_per_ft_lat * 1.2
    pad_lng = buffer_ft * deg_per_ft_lng * 1.2
    bbox = (min(lngs) - pad_lng, min(lats) - pad_lat,
            max(lngs) + pad_lng, max(lats) + pad_lat)
    x_min, y_max_ = deg2tile(bbox[0], bbox[1], z)
    x_max, y_min_ = deg2tile(bbox[2], bbox[3], z)

    candidates = []
    for x in range(min(x_min, x_max), max(x_min, x_max) + 1):
        for y in range(min(y_min_, y_max_), max(y_min_, y_max_) + 1):
            candidates.append((x, y))

    # Cull: a tile is in if its centre is within buffer_m of the polyline.
    def tile_centre(x, y, z):
        lng1, lat1, lng2, lat2 = tile_bounds(x, y, z)
        return ((lng1 + lng2) / 2, (lat1 + lat2) / 2)

    # min distance from point to polyline (great-circle)
    def point_to_polyline_m(pt, coords):
        # Approximate: take min distance to nearest segment via planar
        # equirectangular projection — good enough for this scale.
        best = float('inf')
        lat0 = pt[1]
        cos_lat = math.cos(math.radians(lat0))
        for i in range(len(coords) - 1):
            a = coords[i]
            b = coords[i + 1]
            ax = (a[0] - pt[0]) * cos_lat * 111319.488   # m per degree at equator
            ay = (a[1] - pt[1]) * 111319.488
            bx = (b[0] - pt[0]) * cos_lat * 111319.488
            by = (b[1] - pt[1]) * 111319.488
            vx = bx - ax
            vy = by - ay
            denom = vx * vx + vy * vy
            if denom < 1e-9:
                d = math.hypot(ax, ay)
            else:
                t = max(0.0, min(1.0, (-ax * vx + -ay * vy) / denom))
                cx = ax + t * vx
                cy = ay + t * vy
                d = math.hypot(cx, cy)
            if d < best:
                best = d
        return best

    # Tile diagonal half-length in metres (so we include tiles the line crosses
    # even if the centre is just past the buffer):
    if candidates:
        # All same zoom -> all same tile size; sample first
        x0, y0 = candidates[0]
        lng1, lat1, lng2, lat2 = tile_bounds(x0, y0, z)
        diag_m = math.hypot(
            (lng2 - lng1) * 111319.488 * math.cos(math.radians((lat1 + lat2) / 2)),
            (lat2 - lat1) * 111319.488,
        )
        margin = diag_m / 2.0
    else:
        margin = 0

    keep = []
    for (x, y) in candidates:
        ce = tile_centre(x, y, z)
        d = point_to_polyline_m(ce, coords_lnglat)
        if d <= buffer_m + margin:
            keep.append((x, y))
    return keep


def fetch_tile(url, timeout=20):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'RoadWalk/pre-bake (https://github.com/jsgould1/roadwalk)',
        'Accept': 'image/*,*/*;q=0.8',
        'Referer': 'https://server.arcgisonline.com/',
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        ctype = r.headers.get('Content-Type', 'image/jpeg').split(';')[0].strip()
        return data, ctype


def main():
    ap = argparse.ArgumentParser(description='Pre-bake basemap tiles into a RoadWalk cache file.')
    ap.add_argument('--section', help='Section id (A-E) — uses its alignment as the corridor.')
    ap.add_argument('--bbox', help='Alternative: lat1,lng1,lat2,lng2 rectangle')
    ap.add_argument('--layer', default='esri_aerial', choices=sorted(LAYER_URLS),
                    help='Basemap layer to pre-bake')
    ap.add_argument('--min-z', type=int, default=14)
    ap.add_argument('--max-z', type=int, default=19)
    ap.add_argument('--buffer-ft', type=float, default=400.0,
                    help='Corridor buffer either side of the alignment (ft)')
    ap.add_argument('--workers', type=int, default=12)
    ap.add_argument('--out', default=None, help='Output filename')
    ap.add_argument('--dry-run', action='store_true', help='Count + size estimate only')
    args = ap.parse_args()

    if not args.section and not args.bbox:
        ap.error('--section or --bbox required')

    if args.section:
        coords = load_section_alignment(args.section)
        label = args.section
    else:
        lng_min, lat_min, lng_max, lat_max = parse_bbox(args.bbox)
        coords = [[lng_min, lat_min], [lng_max, lat_min],
                  [lng_max, lat_max], [lng_min, lat_max], [lng_min, lat_min]]
        label = 'bbox'

    # Collect tile (x,y) per zoom
    tiles_by_z = {}
    total = 0
    for z in range(args.min_z, args.max_z + 1):
        ts = collect_corridor_tiles(coords, args.buffer_ft, z)
        tiles_by_z[z] = ts
        total += len(ts)
        print('  z=%d: %d tiles' % (z, len(ts)))
    avg_kb = {14: 4, 15: 6, 16: 9, 17: 14, 18: 19, 19: 24}
    est_mb = sum(len(ts) * avg_kb.get(z, 15) / 1024.0 for z, ts in tiles_by_z.items())
    print('TOTAL %d tiles · est %.1f MB' % (total, est_mb))
    if args.dry_run:
        print('(--dry-run; no fetch)')
        return

    out_name = args.out or 'roadwalk-tiles-%s-%s-%s.json' % (
        label, args.layer, datetime.datetime.utcnow().strftime('%Y-%m-%d'))
    out_path = os.path.join(DATA, out_name)

    # Fetch tiles concurrently. Encode + write line-by-line so peak RAM stays modest.
    url_tmpl = LAYER_URLS[args.layer]
    fetched = 0
    failed = 0
    bytes_total = 0
    lock = threading.Lock()
    t0 = time.time()

    with open(out_path, 'w', encoding='utf-8') as f:
        # Header (count will be patched at the end)
        f.write(json.dumps({
            'format': 'roadwalk-tiles', 'version': 2,
            'exported': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            'count': total,
            'layer': args.layer,
            'section': label,
            'buffer_ft': args.buffer_ft,
        }) + '\n')

        def one(z, x, y):
            nonlocal fetched, failed, bytes_total
            url = url_tmpl.format(z=z, x=x, y=y)
            try:
                data, ctype = fetch_tile(url)
            except Exception as e:
                with lock:
                    failed += 1
                    if failed <= 5:
                        print('  ! fetch failed %s: %s' % (url, e))
                return None
            b64 = base64.b64encode(data).decode('ascii')
            data_url = 'data:%s;base64,%s' % (ctype, b64)
            rec = {
                'k': '%s/%d/%d/%d' % (args.layer, z, x, y),
                'v': data_url,
                'layer': args.layer,
                'bytes': len(data),
            }
            line = json.dumps(rec) + '\n'
            with lock:
                f.write(line)
                fetched += 1
                bytes_total += len(data)
                if fetched % 50 == 0 or fetched == total:
                    elapsed = time.time() - t0
                    rate = fetched / max(elapsed, 1e-6)
                    eta = (total - fetched) / max(rate, 1e-6)
                    print('  %d/%d tiles · %.1f MB · %.0fs elapsed · ETA %ds'
                          % (fetched, total, bytes_total / 1048576, elapsed, eta))
            return True

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = []
            for z, ts in tiles_by_z.items():
                for x, y in ts:
                    futures.append(pool.submit(one, z, x, y))
            for fut in as_completed(futures):
                pass

    print()
    print('DONE — fetched %d / %d tiles · %.1f MB · %d failed' %
          (fetched, total, bytes_total / 1048576, failed))
    print('Wrote %s' % out_path)
    print()
    print('To load on a device: open the in-app Tile Cache Manager -> "Import Cache File"')


if __name__ == '__main__':
    main()
