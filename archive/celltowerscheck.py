import gzip
import csv

INPUT  = r'D:\Dev\NetworkAnalyzerLLM\cell_towers.csv.gz'
OUTPUT = r'D:\Dev\NetworkAnalyzerLLM\tunisia_all_towers.csv'

# Tunisia bounding box
LAT_MIN, LAT_MAX = 30.2, 37.5
LON_MIN, LON_MAX = 7.5,  11.6

kept  = 0
total = 0

with gzip.open(INPUT, 'rt', encoding='utf-8') as f_in, \
     open(OUTPUT, 'w', newline='', encoding='utf-8') as f_out:

    reader = csv.DictReader(f_in)
    writer = None

    for row in reader:
        total += 1

        if total % 100_000 == 0:
            print(f"  Processed {total:,} rows — {kept} Tunisia LTE/NR towers found so far")

        # Filter: MCC=605 (Tunisia), NET=2 (Ooredoo), radio LTE or NR
        if row.get('mcc') != '605':
            continue
        if row.get('radio') not in ('LTE', 'NR'):
            continue

        # Validate coordinates
        try:
            lat = float(row['lat'])
            lon = float(row['lon'])
        except (ValueError, KeyError):
            continue

        if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
            continue

        # Write header on first match
        if writer is None:
            writer = csv.DictWriter(f_out, fieldnames=reader.fieldnames)
            writer.writeheader()

        writer.writerow(row)
        kept += 1

print(f"\nDone. Scanned {total:,} rows total.")
print(f"Kept {kept} Tunisia LTE/NR towers (all operators) → {OUTPUT}")
