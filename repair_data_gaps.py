"""
One-off repair for the data gaps left by two silent-failure bugs.

Background — both bugs shared a root cause: db_simulator's retry helpers caught
sqlite3.OperationalError, retried three times and returned quietly, so a malformed
statement failed forever without ever printing anything.

  1. customer_value gained churn_risk_score/churn_label, but three INSERTs still
     supplied 5 values positionally. Every insert failed -> the table froze at 2026-04.
  2. backfill_monthly_op anchors its 12-month window on MAX(month) FROM customer_value,
     so billing froze too, at 2026-05.
  3. network_alarms has never been coupled to KPIs by anything: alarms were placed on
     random cells (62% of which serve nobody) and no backfill ever generated them from
     the counters. Historical alarms therefore carry no relationship to cell health.

This script closes all three. It is idempotent: re-running inserts nothing new.

    python repair_data_gaps.py            # do it
    python repair_data_gaps.py --dry-run  # just report what it would do
"""

import argparse
import random
import sqlite3
from datetime import datetime, timedelta

import db_simulator as S

ALARM_HISTORY_DAYS = 30


def _months_between(start_month: str, end_month: str):
    """Inclusive list of 'YYYY-MM' strings after start_month up to end_month."""
    cur = datetime.strptime(start_month, "%Y-%m")
    end = datetime.strptime(end_month, "%Y-%m")
    out = []
    while True:
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)  # +1 calendar month
        if cur > end:
            return out
        out.append(cur.strftime("%Y-%m"))


def _segment_for(arpu, month=None):
    """Delegates to the simulator's single definition — no second copy of the rule."""
    return S.classify_value(arpu, month)


def reclassify_segments(dry_run=False):
    """Rewrite value_segment/is_hvc for every month against that month's own ARPU
    distribution. The old fixed cutoffs (platinum at 80 Yuan) predated update_arpu.py's
    rescaling, so 88.5% of the base was flagged high-value and the label meant nothing."""
    months = [r[0] for r in S.op_query("SELECT DISTINCT month FROM customer_value ORDER BY 1")]
    print(f"  {len(months)} months to reclassify")
    total = changed = hvc_now = 0
    for month in months:
        th = S.value_thresholds(month, refresh=True)
        rows = S.op_query("SELECT msisdn, arpu, value_segment, is_hvc FROM customer_value "
                          "WHERE month=?", (month,))
        updates = []
        for msisdn, arpu, seg_old, hvc_old in rows:
            seg, hvc = S.classify_value(arpu, thresholds=th)
            total += 1
            hvc_now += hvc
            if seg != seg_old or hvc != hvc_old:
                changed += 1
                updates.append((seg, hvc, msisdn, month))
        if updates and not dry_run:
            S.op_write_many("UPDATE customer_value SET value_segment=?, is_hvc=? "
                            "WHERE msisdn=? AND month=?", updates)
    print(f"  {total:,} rows, {changed:,} reclassified, {hvc_now:,} now HVC "
          f"({100.0*hvc_now/total:.1f}%)" if total else "  nothing to do")


def regenerate_nps(dry_run=False):
    """Rebuild every nps_scores row from THAT MONTH's segment, with the skewed
    within-band score distribution.

    Why: _gen_nps derives sentiment from value_segment, and the segment thresholds were
    recalibrated (platinum went from 67.6% of the base to the top 10%). Months generated
    before that used the old mix, months after used the new one, which put a cliff in the
    series at the boundary — promoters dropped 5,092 -> 3,705 between April and May with
    nothing in the fictional world causing it. Any NPS trend question narrated that as
    'growing customer dissatisfaction'.

    Respondents stay exactly who they were; only their score is redrawn. Seeded per
    (msisdn, month) so this is reproducible and re-running changes nothing."""
    months = [r[0] for r in S.op_query("SELECT DISTINCT month FROM nps_scores ORDER BY 1")]
    print(f"  {len(months)} months to regenerate")
    total = 0
    for month in months:
        seg = dict(S.op_query(
            "SELECT msisdn, value_segment FROM customer_value WHERE month=?", (month,)))
        respondents = S.op_query("SELECT msisdn FROM nps_scores WHERE month=?", (month,))
        updates = []
        for (msisdn,) in respondents:
            rng = random.Random(f"{msisdn}|{month}")
            _, _, score, cat = S._gen_nps(msisdn, month, seg.get(msisdn, "bronze"), rng=rng)
            updates.append((score, cat, msisdn, month))
        total += len(updates)
        if updates and not dry_run:
            S.op_write_many("UPDATE nps_scores SET nps_score=?, nps_category=? "
                            "WHERE msisdn=? AND month=?", updates)
    print(f"  {total:,} rows regenerated")


def fill_customer_value_gap(dry_run=False):
    """Carry each customer's ARPU forward month by month from the last month they have,
    with the same drift simulate_arpu_update applies, instead of re-randomising it."""
    latest = S.op_query("SELECT MAX(month) FROM customer_value")[0][0]
    today_month = datetime.now().strftime("%Y-%m")
    months = _months_between(latest, today_month)
    print(f"  customer_value latest month = {latest}, today = {today_month}")
    if not months:
        print("  nothing to fill — already current")
        return
    print(f"  months to create: {', '.join(months)}")

    # last known state per customer
    state = {m: (a, seg, hvc) for m, a, seg, hvc in S.op_query(
        "SELECT msisdn, arpu, value_segment, is_hvc FROM customer_value WHERE month=?", (latest,))}
    actives = [r[0] for r in S.op_query("SELECT msisdn FROM customers WHERE is_active=1")]
    print(f"  {len(actives):,} active customers, {len(state):,} with a {latest} row")

    rows = []
    for month in months:
        for msisdn in actives:
            prev = state.get(msisdn)
            arpu = round(prev[0] * random.uniform(0.97, 1.05), 2) if prev \
                else round(random.uniform(5, 150), 2)
            arpu = max(2.0, arpu)
            seg, hvc = _segment_for(arpu, month)
            state[msisdn] = (arpu, seg, hvc)
            rows.append((msisdn, month, arpu, seg, hvc))

    print(f"  -> {len(rows):,} customer_value rows")
    if dry_run:
        return
    S.op_write_many("INSERT OR IGNORE INTO customer_value "
                    "(msisdn, month, arpu, value_segment, is_hvc) VALUES(?,?,?,?,?)", rows)
    print("  customer_value filled")


def unfreeze_billing(dry_run=False):
    """backfill_monthly_op re-anchors on MAX(month) FROM customer_value, so once the gap
    above is closed this simply fills the months billing is missing."""
    before = S.op_query("SELECT MAX(billing_month), COUNT(*) FROM billing")[0]
    print(f"  billing currently: max={before[0]}, {before[1]:,} rows")
    if dry_run:
        print("  -> would run backfill_monthly_op() to re-anchor and fill")
        return
    S.backfill_monthly_op()
    after = S.op_query("SELECT MAX(billing_month), COUNT(*) FROM billing")[0]
    print(f"  billing now: max={after[0]}, {after[1]:,} rows (+{after[1]-before[1]:,})")


def retire_phantom_alarms(dry_run=False):
    """Close active alarms sitting on cells that serve nobody. These are the legacy rows
    from when simulate_alarm_trigger picked cells at random; they can never correlate
    with customer impact because there are no customers on them."""
    n = S.sc_query("""
        SELECT COUNT(*) FROM network_alarms WHERE is_active=1 AND cell_id NOT IN
          (SELECT DISTINCT st.current_cell_id FROM subscriber_technology st
           JOIN subscribers s ON st.msisdn=s.msisdn
           WHERE s.is_active=1 AND st.current_cell_id IS NOT NULL)
    """)[0][0]
    print(f"  active alarms on cells serving nobody: {n:,}")
    if dry_run or not n:
        return
    now = datetime.now().isoformat()
    S.sc_write("""
        UPDATE network_alarms SET is_active=0, clear_time=?
        WHERE is_active=1 AND cell_id NOT IN
          (SELECT DISTINCT st.current_cell_id FROM subscriber_technology st
           JOIN subscribers s ON st.msisdn=s.msisdn
           WHERE s.is_active=1 AND st.current_cell_id IS NOT NULL)
    """, (now,))
    print(f"  retired {n:,} phantom alarms (rows kept, is_active=0)")


def retire_uncoupled_alarms(dry_run=False):
    """Close the remaining legacy alarms: still active, raised before today, and not
    written by the threshold-crossing detector. They sit on cells that do serve customers,
    but they were placed at random by the old simulate_alarm_trigger, so they carry no
    relationship to their cell's health and only dilute queries against active alarms.

    Alarms raised today are kept whichever path created them — the live loop now degrades
    the cell it alarms, so those are coupled by construction."""
    today = datetime.now().strftime("%Y-%m-%d")
    where = ("is_active=1 AND description NOT LIKE '%threshold crossing%' "
             "AND substr(trigger_time,1,10) < ?")
    n = S.sc_query(f"SELECT COUNT(*) FROM network_alarms WHERE {where}", (today,))[0][0]
    print(f"  legacy alarms still active on real cells: {n:,}")
    if dry_run or not n:
        return
    S.sc_write(f"UPDATE network_alarms SET is_active=0, clear_time=? WHERE {where}",
               (datetime.now().isoformat(), today))
    print(f"  retired {n:,} uncoupled alarms (rows kept, is_active=0)")


def synthesize_alarm_history(days=ALARM_HISTORY_DAYS, dry_run=False):
    """Walk recent kpis_daily and write the alarms those degraded cell-days SHOULD have
    raised, using the same detector the live loop uses. Only cells that serve subscribers
    are eligible. Everything before the most recent day is written already-cleared, so
    this builds a correlated history without inflating the active-alarm count."""
    latest = S.sc_query("SELECT MAX(date) FROM kpis_daily")[0][0]
    start = (datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    print(f"  scanning kpis_daily {start} .. {latest} on cells that serve subscribers")

    rows = S.sc_query("""
        SELECT k.cell_id, k.date, k.rsrp_avg, k.sinr_avg, k.dl_throughput_mbps,
               k.ul_throughput_mbps, k.dropped_call_rate, k.availability_pct,
               k.latency_ms, k.congestion_level, k.active_users_avg, c.technology
        FROM kpis_daily k
        JOIN cells c ON k.cell_id = c.cell_id
        WHERE k.date > ? AND k.date <= ? AND c.is_active = 1
          AND EXISTS (SELECT 1 FROM subscriber_technology st
                      JOIN subscribers s ON st.msisdn = s.msisdn
                      WHERE st.current_cell_id = k.cell_id AND s.is_active = 1)
    """, (start, latest))
    print(f"  {len(rows):,} cell-days to evaluate")

    # don't duplicate alarms this script already created on a previous run
    existing = {(r[0], (r[1] or "")[:10]) for r in S.sc_query(
        "SELECT cell_id, trigger_time FROM network_alarms WHERE trigger_time > ?", (start,))}

    new_rows = []
    for r in rows:
        hit = S._detect_threshold_alarm(r[11], r[:11])
        if not hit:
            continue
        cell_id, day = r[0], r[1]
        if (cell_id, day) in existing:
            continue
        atype, sev = hit
        trigger = f"{day}T{random.randint(0,23):02d}:{random.randint(0,59):02d}:00"
        if day == latest:
            active, clear = 1, None          # today's stay open
        else:
            active = 0
            clear = (datetime.strptime(trigger, "%Y-%m-%dT%H:%M:%S")
                     + timedelta(hours=random.randint(1, 20))).isoformat()
        new_rows.append((cell_id, atype, sev, trigger, clear, active,
                         f"{atype} detected on cell {cell_id} by threshold crossing at {trigger[:16]}"))

    still_open = sum(1 for r in new_rows if r[5] == 1)
    print(f"  -> {len(new_rows):,} alarms to write ({still_open} left active, rest pre-cleared)")
    if dry_run or not new_rows:
        return
    S.sc_write_many("""
        INSERT INTO network_alarms
            (cell_id, alarm_type, severity, trigger_time, clear_time, is_active, description)
        VALUES (?,?,?,?,?,?,?)
    """, new_rows)
    print("  alarm history written")


def report():
    print("\n  -- AFTER --------------------------------------")
    cv = S.op_query("SELECT MAX(month), COUNT(*) FROM customer_value")[0]
    bl = S.op_query("SELECT MAX(billing_month), COUNT(*) FROM billing")[0]
    al = S.sc_query("SELECT COUNT(*), SUM(is_active) FROM network_alarms")[0]
    arpu = S.op_query("SELECT ROUND(AVG(arpu),2) FROM customer_value "
                      "WHERE month=(SELECT MAX(month) FROM customer_value)")[0][0]
    print(f"  customer_value : max={cv[0]}  {cv[1]:,} rows   avg ARPU {arpu} Yuan")
    print(f"  billing        : max={bl[0]}  {bl[1]:,} rows")
    print(f"  network_alarms : {al[0]:,} total, {al[1]:,} active")
    print("  -----------------------------------------------\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    ap.add_argument("--alarm-days", type=int, default=ALARM_HISTORY_DAYS)
    args = ap.parse_args()

    mode = " (DRY RUN)" if args.dry_run else ""
    print(f"\n=== REPAIRING DATA GAPS{mode} ===\n")
    print("[1/7] customer_value gap")
    fill_customer_value_gap(args.dry_run)
    print("\n[2/7] billing (unfreezes once customer_value advances)")
    unfreeze_billing(args.dry_run)
    print("\n[3/7] retire phantom alarms (cells serving nobody)")
    retire_phantom_alarms(args.dry_run)
    print("\n[4/7] retire uncoupled legacy alarms (real cells, random placement)")
    retire_uncoupled_alarms(args.dry_run)
    print(f"\n[5/7] synthesise correlated alarm history ({args.alarm_days} days)")
    synthesize_alarm_history(args.alarm_days, args.dry_run)
    print("\n[6/7] reclassify value segments against the live ARPU distribution")
    reclassify_segments(args.dry_run)
    print("\n[7/7] regenerate NPS from each month's own segments")
    regenerate_nps(args.dry_run)
    if not args.dry_run:
        report()


if __name__ == "__main__":
    main()
