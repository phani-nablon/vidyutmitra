"""Neo4jGraphStore against a recording fake driver: checks the Cypher it sends and the row shapes it returns.
A live Neo4j is not needed (and not available in CI); `sr neo4j-check` covers the real server."""
import json

import pytest

from substation_retrieval.kg import store as S


class FakeTx:
    def __init__(self, log, rows):
        self.log, self.rows = log, rows

    def run(self, q, **p):
        self.log.append((" ".join(q.split()), p))
        out = self.rows.pop(0) if self.rows else []
        return FakeResult(out)


class FakeResult(list):
    def consume(self):
        return None


class FakeRec:
    def __init__(self, d):
        self.d = d

    def data(self):
        return self.d


class FakeSession:
    def __init__(self, log, rows):
        self.log, self.rows = log, rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute_read(self, fn):
        return fn(FakeTx(self.log, self.rows))

    execute_write = execute_read

    def run(self, q, **p):
        self.log.append((" ".join(q.split()), p))
        return FakeResult()


@pytest.fixture
def st(monkeypatch):
    log, rows = [], []
    import neo4j

    class D:
        def session(self, **k):
            return FakeSession(log, rows)

        def close(self):
            pass

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", lambda *a, **k: D())
    s = S.Neo4jGraphStore()
    s.log, s.rows = log, rows
    return s


def test_upsert_partition_is_one_delete_then_batched_creates(st):
    V = [{"vid": "d1:s", "label": "substation", "pk": "d1", "props": {"kva": 1500, "tags": ["a"], "skip": None}},
         {"vid": "d1:e", "label": "equipment", "pk": "d1", "props": {}}]
    E = [{"label": "CONTAINS", "out": "d1:s", "in": "d1:e", "props": {"w": 1}}]
    st.upsert_partition("d1", V, E)
    qs = [q for q, _ in st.log]
    assert qs[0].startswith("MATCH (n:Node {pk: $pk}) DETACH DELETE n")
    assert any("MERGE (n:Node {vid: r.vid}) SET n:`substation`" in q for q in qs)
    assert any("CREATE (a)-[x:`CONTAINS`]->(b)" in q for q in qs)
    sub = next(p for q, p in st.log if "`substation`" in q)["rows"][0]
    assert sub["props"] == {"kva": 1500, "tags": json.dumps(["a"])} and sub["pk"] == "d1"
    assert next(p for q, p in st.log if "`CONTAINS`" in q)["rows"][0]["pk"] == "d1"


def test_unsafe_label_rejected(st):
    with pytest.raises(ValueError):
        st.upsert_shared([{"vid": "x", "label": "a`) DETACH DELETE (n", "pk": "_shared", "props": {}}], [])


def test_shared_edges_are_merged(st):
    st.upsert_shared([], [{"label": "IS_A", "out": "a", "in": "c:x", "pk": "_shared", "props": {}}])
    assert "MERGE (a)-[x:`IS_A` {pk: r.pk}]->(b)" in st.log[0][0]


def test_partitions_row_shapes(st):
    st.rows += [
        [FakeRec({"l": ["Node", "substation"], "m": {"vid": "d1:s", "pk": "d1", "kva": 1500, "tags": "[\"a\"]"}})],
        [FakeRec({"l": "HAS_FACET", "o": "d1:s", "i": "f:x=1", "m": {"pk": "d1"}, "pk": "d1"})],
        [FakeRec({"l": ["Node", "facet"], "m": {"vid": "f:x=1", "pk": "_shared", "key": "x", "value": 1}})],
    ]
    out = st.partitions(["d1"])["d1"]
    assert out["vertices"][0] == {"vid": "d1:s", "label": "substation", "pk": "d1", "props": {"kva": 1500, "tags": ["a"]}}
    assert {v["vid"] for v in out["vertices"]} == {"d1:s", "f:x=1"}
    assert out["edges"] == [{"label": "HAS_FACET", "out": "d1:s", "in": "f:x=1", "props": {}}]


def test_shortlist_counts_each_hit(st):
    st.rows += [[FakeRec({"vid": "s1"}), FakeRec({"vid": "s1"}), FakeRec({"vid": "s2"})],
                [FakeRec({"vid": "s2"})]]
    assert st.shortlist(["f:a"], ["c:b"]) == {"s1": 2, "s2": 2}
