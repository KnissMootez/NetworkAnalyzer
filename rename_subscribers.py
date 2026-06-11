"""
rename_subscribers.py
One-shot migration: replace all real-world names in operator_new.db
with Avatar: The Last Airbender fictional names.
Also updates email addresses to match.
Run once, then discard.
"""

import sqlite3
import random
import os

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "operator_new.db")

# ── Avatar-world name pool ────────────────────────────────────────────────────
# Earth Kingdom (Chinese-inspired)
EK_FIRST = [
    "Toph", "Bumi", "Haru", "Jet", "Kuei", "Long", "Xin", "Song", "Meng",
    "Pao", "Yung", "Wei", "Jin", "Lee", "Liling", "Joo", "Ying", "Fong",
    "Shao", "Zhen", "Bo", "Chan", "Dao", "Fan", "Guo", "Han", "Hua", "Jian",
    "Kai", "Lan", "Min", "Ping", "Qian", "Rong", "Shan", "Tai", "Wan", "Xiao",
    "Yan", "Zhi", "Mei", "Lian", "Feng", "Hong", "Jing", "Kang", "Lu", "Nuo",
]
EK_LAST = [
    "Beifong", "Li", "Chen", "Fong", "Zhang", "Xin", "Long", "Yao", "Bei",
    "Dao", "Han", "Hua", "Jian", "Kai", "Lan", "Min", "Ping", "Qian", "Shan",
    "Tan", "Wan", "Xiao", "Yan", "Zhi", "Bao", "Cai", "Deng", "Fu", "Gao",
]

# Fire Nation (Japanese-inspired)
FN_FIRST = [
    "Zuko", "Azula", "Iroh", "Ozai", "Sozin", "Mai", "Ty", "Chan", "Zhao",
    "Jeong", "Piandao", "Lo", "Ukano", "Ukyo", "Shoji", "Kori", "Ryo",
    "Naomi", "Kei", "Sora", "Taro", "Yuki", "Hiro", "Ren", "Sae", "Taka",
    "Mako", "Nori", "Koji", "Rei", "Saki", "Tomo", "Yami", "Hana", "Ito",
    "Jiro", "Kenji", "Liko", "Masa", "Nami", "Osamu", "Riku", "Sho", "Toru",
]
FN_LAST = [
    "Zhao", "Piandao", "Shoji", "Ukano", "Lo", "Li", "Mako", "Nori", "Koji",
    "Rei", "Sato", "Tanaka", "Yamamoto", "Ito", "Watanabe", "Kobayashi", "Abe",
    "Nakamura", "Suzuki", "Kato", "Yoshida", "Yamada", "Sasaki", "Yamaguchi",
]

# Water Tribe (Inuit-inspired)
WT_FIRST = [
    "Katara", "Sokka", "Hakoda", "Pakku", "Yue", "Arnook", "Hama", "Bato",
    "Kya", "Tonraq", "Senna", "Malina", "Tulok", "Nini", "Iqaluk", "Siku",
    "Nauja", "Pinga", "Kiviuq", "Sedna", "Nanuq", "Atanarjuat", "Pitseolak",
    "Aklaq", "Amaruq", "Imiq", "Kalluk", "Nanuq", "Sikku", "Taqtu", "Ulva",
]
WT_LAST = [
    "of the Southern Tribe", "of the Northern Tribe", "Hakoda", "Arnook",
    "Kuruk", "Pakku", "Tonraq", "Malina", "Tulok", "Siku", "Nauja", "Pinga",
]

# Air Nomads (Tibetan-inspired)
AN_FIRST = [
    "Aang", "Gyatso", "Tenzin", "Pema", "Dchen", "Pasang", "Sonam", "Karma",
    "Dorje", "Pemba", "Rinzin", "Sangmo", "Tashi", "Wangmo", "Dawa", "Jigme",
    "Kelsang", "Lobsang", "Namgyal", "Nyima", "Palden", "Rigzin", "Sherab",
    "Thubten", "Tsering", "Ugyen", "Wangchuk", "Yeshe", "Zangpo", "Choden",
]
AN_LAST = [
    "Gyatso", "Tenzin", "Pasang", "Sonam", "Karma", "Dorje", "Pemba",
    "Rinzin", "Sangmo", "Tashi", "Wangmo", "Dawa", "Kelsang", "Lobsang",
]

# Fallback (generic Avatar-world)
GENERIC_FIRST = EK_FIRST + FN_FIRST + WT_FIRST + AN_FIRST
GENERIC_LAST  = EK_LAST  + FN_LAST  + WT_LAST  + AN_LAST

NAMES_BY_NATION = {
    "Earth Kingdom": (EK_FIRST, EK_LAST),
    "Fire Nation":   (FN_FIRST, FN_LAST),
    "Water Tribe":   (WT_FIRST, WT_LAST),
    "Air Nomads":    (AN_FIRST, AN_LAST),
}


def _gen_name(nation: str | None) -> tuple[str, str]:
    pool = NAMES_BY_NATION.get(nation, (GENERIC_FIRST, GENERIC_LAST))
    return random.choice(pool[0]), random.choice(pool[1])


def run():
    conn = sqlite3.connect(DB)
    # Pull nation from NetworkAnalyzer_new.db so names match the region
    sc_db = os.path.join(os.path.dirname(DB), "NetworkAnalyzer_new.db")
    conn.execute(f"ATTACH DATABASE '{sc_db}' AS sc")

    msisdns = conn.execute(
        "SELECT c.msisdn, s.nation FROM customers c "
        "LEFT JOIN sc.subscribers s ON c.msisdn = s.msisdn"
    ).fetchall()

    print(f"Renaming {len(msisdns):,} subscribers...")

    updates = []
    for msisdn, nation in msisdns:
        fname, lname = _gen_name(nation)
        full_name = f"{fname} {lname}"
        email = f"{fname.lower().replace(' ', '')}.{lname.lower().replace(' ', '')}{random.randint(1, 999)}@avatar.net"
        updates.append((full_name, email, msisdn))

    conn.executemany(
        "UPDATE customers SET full_name=?, email=? WHERE msisdn=?",
        updates
    )
    conn.commit()
    conn.close()
    print("Done.")

    # Spot-check
    conn2 = sqlite3.connect(DB)
    samples = conn2.execute("SELECT full_name, email FROM customers ORDER BY RANDOM() LIMIT 8").fetchall()
    conn2.close()
    print("\nSample names:")
    for name, email in samples:
        print(f"  {name} — {email}")


if __name__ == "__main__":
    run()
