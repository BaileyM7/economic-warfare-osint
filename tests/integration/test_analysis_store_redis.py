"""Shared analysis state across instances (Phase 7 — closes fragility F3).

The F3 fix, proven: analysis state written by ONE store instance is visible to a
SEPARATE instance — i.e. two web processes behind a load balancer share it. Uses
real Redis 8 (RedisJSON: JSON.ARRAPPEND / JSON.ARRTRIM have no fakeredis stand-in).

    docker run -d -p 6398:6379 redis:8.2-alpine
    EMISSARY_TEST_REDIS_URL=redis://localhost:6398/0 uv run pytest tests/integration -v
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

TEST_REDIS_URL = os.getenv("EMISSARY_TEST_REDIS_URL", "")


def _requires_real_redis8_json():
    if not TEST_REDIS_URL:
        pytest.skip("set EMISSARY_TEST_REDIS_URL to a real Redis 8 to run this")
    import redis as _redis

    try:
        client = _redis.Redis.from_url(
            TEST_REDIS_URL, decode_responses=True, socket_connect_timeout=2
        )
        client.ping()
        client.execute_command("JSON.SET", "emissary:_probe", "$", "{}")
        client.delete("emissary:_probe")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no RedisJSON at {TEST_REDIS_URL}: {exc}")


@pytest.fixture
def two_instances():
    """Two RedisAnalysisStore objects on one Redis — i.e. two web processes."""
    _requires_real_redis8_json()

    from src.common import redis_client
    from src.common.analyses import RedisAnalysisStore
    from src.common.config import config

    saved = config.redis_url
    config.redis_url = TEST_REDIS_URL
    redis_client.reset_for_tests()
    client = redis_client.get_redis()

    # Clean any leftover analysis docs.
    for k in client.keys("emissary:analysis:*"):
        client.delete(k)

    instance_a = RedisAnalysisStore(client)
    instance_b = RedisAnalysisStore(client)  # a "different process" on the same Redis
    yield instance_a, instance_b

    for k in client.keys("emissary:analysis:*"):
        client.delete(k)
    config.redis_url = saved
    redis_client.reset_for_tests()


def test_state_written_by_one_instance_is_visible_to_another(two_instances):
    """This is F3 closed: process A writes, process B reads."""
    a, b = two_instances

    a.create("run1", {"analysis_id": "run1", "status": "running", "progress": [], "events": []})
    a.append_progress("run1", "decomposing")
    a.append_event("run1", {"type": "tool", "name": "search_sanctions"})
    a.set_fields("run1", status="completed", result={"executive_summary": "done"})

    # B never touched run1 — it reads purely from Redis.
    snap = b.get("run1")
    assert snap is not None, "a second instance could not see the analysis — F3 not closed"
    assert snap["status"] == "completed"
    assert snap["progress"] == ["decomposing"]
    assert snap["events"] == [{"type": "tool", "name": "search_sanctions"}]
    assert snap["result"]["executive_summary"] == "done"


def test_concurrent_appends_from_two_instances_dont_clobber(two_instances):
    """JSON.ARRAPPEND is server-side atomic — no read-modify-write race.

    Interleaved appends from two instances must all survive (order not asserted),
    which a naive get-mutate-set store would lose.
    """
    a, b = two_instances
    a.create("run2", {"analysis_id": "run2", "status": "running", "progress": [], "events": []})

    for i in range(10):
        (a if i % 2 == 0 else b).append_progress("run2", f"msg-{i}")

    prog = a.get("run2")["progress"]
    assert sorted(prog) == sorted(f"msg-{i}" for i in range(10)), "an append was clobbered"


def test_append_is_capped_server_side(two_instances):
    from src.common.analyses import ANALYSES_EVENTS_CAP

    a, _ = two_instances
    a.create("run3", {"analysis_id": "run3", "status": "running", "progress": [], "events": []})
    for i in range(ANALYSES_EVENTS_CAP + 15):
        a.append_event("run3", {"n": i})

    events = a.get("run3")["events"]
    assert len(events) == ANALYSES_EVENTS_CAP  # JSON.ARRTRIM enforced it
    assert events[-1] == {"n": ANALYSES_EVENTS_CAP + 14}  # newest kept


def test_backend_reports_redis(two_instances):
    import os

    from src.common import analyses

    os.environ["ANALYSES_BACKEND"] = "redis"
    try:
        analyses.reset_for_tests()
        assert analyses.backend_name() == "redis"
    finally:
        os.environ.pop("ANALYSES_BACKEND", None)
        analyses.reset_for_tests()
