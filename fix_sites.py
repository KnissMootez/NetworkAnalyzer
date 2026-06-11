from PIL import Image
import numpy as np, sqlite3, random, math
random.seed(42)

img = Image.open('atla_map_countoured.png').convert('RGB')
arr = np.array(img)
IMG_W, IMG_H = img.size  # 4096x3072

TARGETS = {
    "Agna Qel'a":                   (255,0,0),
    'Ba Sing Se':                   (0,255,0),
    'Fire Nation Capital':          (0,0,255),
    'Omashu':                       (255,255,0),
    'Gaoling':                      (255,0,255),
    'Southern Water Tribe Capital': (0,255,255),
    'Northern Air Temple':          (255,128,0),
    'Western Air Temple':           (128,0,255),
    'Eastern Air Temple':           (0,128,255),
    'Southern Air Temple':          (0,255,128),
    'Kyoshi Island':                (255,0,128),
    'Ember Island':                 (128,255,0),
    "Serpent's Pass":               (255,128,128),
    'Si Wong Desert':               (128,128,255),
    'Gaipan Village':               (255,200,0),
    'Sunset City':                  (200,0,255),
    'The Boiling Rock':             (0,200,100),
    'FumaBu Town':                  (200,100,0),
    'Island of the Sun Warriors':   (100,200,255),
    'Crescent Island':              (255,100,200),
    "HeiBai's Forest":              (100,255,100),
}

MAX_SITES = {
    "Serpent's Pass":    3,
    'The Boiling Rock':  2,
    'Crescent Island':   3,
    'Ember Island':      4,
    'Kyoshi Island':     4,
}

from scipy import ndimage

conn = sqlite3.connect('NetworkAnalyzer_new.db')
updated = 0

for name, (tr,tg,tb) in TARGETS.items():
    # Find all pixels matching this color
    mask = (
        (np.abs(arr[:,:,0].astype(int)-tr)<20) &
        (np.abs(arr[:,:,1].astype(int)-tg)<20) &
        (np.abs(arr[:,:,2].astype(int)-tb)<20)
    )

    # Take only the largest connected component
    labeled, num = ndimage.label(mask)
    if num == 0:
        print(f'SKIP {name}')
        continue
    sizes = ndimage.sum(mask, labeled, range(1, num+1))
    best = np.argmax(sizes) + 1
    circle_pixels = labeled == best

    # Fill the interior of the circle so points inside the outline are valid
    filled = ndimage.binary_fill_holes(circle_pixels)

    # Get all valid (x, y) pixel positions inside the filled region
    valid_ys, valid_xs = np.where(filled)
    if len(valid_xs) == 0:
        print(f'SKIP {name} (no fill)')
        continue

    valid_coords = list(zip(valid_xs.tolist(), valid_ys.tolist()))

    site_ids = [r[0] for r in conn.execute(
        'SELECT site_id FROM sites WHERE region=? AND is_active=1', (name,)
    ).fetchall()]

    max_keep = MAX_SITES.get(name, len(site_ids))
    for sid in site_ids[max_keep:]:
        conn.execute('UPDATE sites SET is_active=0 WHERE site_id=?', (sid,))
    site_ids = site_ids[:max_keep]

    for sid in site_ids:
        # Pick a random pixel from inside the filled contour
        px, py = random.choice(valid_coords)
        lon = float(px)
        lat = float(IMG_H - py)  # convert image y to lat (y from bottom)
        conn.execute('UPDATE sites SET longitude=?,latitude=? WHERE site_id=?', (round(lon,1), round(lat,1), sid))
        updated += 1

    print(f'{name:35s} valid_pixels={len(valid_coords):6d} sites={len(site_ids)}')

conn.commit()
conn.close()
print(f'\nDone. Updated {updated} sites.')
