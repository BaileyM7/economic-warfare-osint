"""seed_mock_data() must never wipe existing data.

Regression guard for the data-loss bug: prod ran EMISSARY_MOCK_DATA=1 and the
old seed DELETEd coas/briefings/activity_log on every boot, so analyst-created
COAs vanished on each deploy. Seeding is now seed-only-if-empty (force=True to
override for local/staging).
"""

from __future__ import annotations

import src.db as db


def test_seed_seeds_a_fresh_db(app_module):
    db.init_db()
    conn = db.get_db()
    conn.execute("DELETE FROM coas")
    conn.execute("DELETE FROM briefings")
    conn.commit()

    db.seed_mock_data()  # empty DB → seeds demo data
    assert conn.execute("SELECT COUNT(*) FROM coas").fetchone()[0] > 0


def test_seed_is_noop_when_data_exists(app_module):
    db.init_db()
    conn = db.get_db()
    conn.execute("DELETE FROM coas")
    conn.commit()
    db.seed_mock_data()  # fresh → seeds

    # Simulate an analyst-created COA, then redeploy (another seed call).
    conn.execute("INSERT INTO coas (id, name, status) VALUES ('sentinel', 'Real COA', 'draft')")
    conn.commit()
    db.seed_mock_data()  # must be a no-op now

    survived = conn.execute("SELECT COUNT(*) FROM coas WHERE id = 'sentinel'").fetchone()[0]
    assert survived == 1, "seed_mock_data wiped a real COA — data-loss regression"


def test_force_reseed_wipes(app_module):
    db.init_db()
    conn = db.get_db()
    conn.execute("INSERT INTO coas (id, name, status) VALUES ('sentinel2', 'Real COA', 'draft')")
    conn.commit()

    db.seed_mock_data(force=True)  # explicit wipe + reseed

    gone = conn.execute("SELECT COUNT(*) FROM coas WHERE id = 'sentinel2'").fetchone()[0]
    assert gone == 0
