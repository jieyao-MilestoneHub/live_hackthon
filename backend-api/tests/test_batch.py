"""WS6: S3 batch model — independent projects grouped by a shared batch_id, source
nested under batch={id}/, queryable via GET /batches/{id}. Each member still runs its
own parallel analysis; this only covers the grouping + key layout."""
from __future__ import annotations


def test_batch_groups_projects_and_nests_source(client) -> None:
    bid = "batch-20260716T101530"
    r1 = client.post("/projects", json={"target_duration_ms": 30000, "batch_id": bid, "title": "a"})
    r2 = client.post("/projects", json={"target_duration_ms": 30000, "batch_id": bid, "title": "b"})
    assert r1.status_code == 201 and r2.status_code == 201

    # Source keys nest under the shared batch prefix (files sit together in S3).
    assert f"/batch={bid}/" in r1.json()["source_key"]
    assert f"/batch={bid}/" in r2.json()["source_key"]

    view = client.get(f"/batches/{bid}").json()
    assert view["batch_id"] == bid
    assert view["count"] == 2
    assert {m["project_id"] for m in view["members"]} == {
        r1.json()["project_id"], r2.json()["project_id"]
    }
    assert {m["status"] for m in view["members"]} == {"CREATED"}


def test_non_batch_project_keeps_flat_key(client) -> None:
    r = client.post("/projects", json={"target_duration_ms": 30000})
    assert r.status_code == 201
    assert "/batch=" not in r.json()["source_key"]


def test_empty_batch_returns_zero(client) -> None:
    view = client.get("/batches/does-not-exist").json()
    assert view["count"] == 0 and view["members"] == []
