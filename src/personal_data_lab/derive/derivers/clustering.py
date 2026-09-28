"""Phase 9 (PHASE9_TOPIC_CLUSTERING_IMPLEMENTATION_PLAN.md): unsupervised topic clustering over
Phase 6's real embeddings -- the complement to Phase 7's fixed-taxonomy annotation. Phase 7 answers
"which of these known categories"; this answers "what groupings actually exist in the data that no
fixed taxonomy would have anticipated."

Pure CPU math over vectors Phase 6 already computed -- no model download, no rented hardware,
unlike every other item in the semantic layer. `HDBSCAN` (decision 2) over L2-normalized vectors
with Euclidean distance (decision 3's documented cosine equivalence: for unit vectors,
||a-b||^2 = 2 - 2*cos(a,b), so Euclidean neighborhoods over normalized vectors match cosine
neighborhoods exactly). Cluster labels are extractive TF-IDF top terms (decision 4), never an LLM
call, so `final_inference = "STATISTICAL"` (decision 5), not `"SEMANTIC"`.

One output table, not the originally-planned two -- `derive/worker.py` writes exactly one
output_relation per compute() call, so `semantic.topic_cluster_assignment` denormalizes each
cluster's label onto every member row instead of factoring it into a separate table (same fix
Phase 5's inline-JSONB evidence column already established for this constraint).
"""

from __future__ import annotations

import numpy as np
from sklearn.cluster import HDBSCAN
from sklearn.feature_extraction.text import TfidfVectorizer
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from ...fingerprints import fingerprint_value
from ..registry import ComputeResult, deriver
from ..versioning import active_version_id

# Grounded 2026-09-28 against the real 70,145-vector canonical population: a benchmark swept six
# (min_cluster_size, min_samples) combinations -- (15,5), (25,5), (50,5), (100,10), (200,10),
# (500,20) -- and measured real cluster count/noise ratio for each (see PROJECT_STATE.md's "Phase
# 9" rollout section for the full table). Noise held essentially flat between 15 and 25 (49.1% ->
# 49.1%) while cluster count nearly halved (621 -> 396) -- a real elbow: 25 buys less
# fragmentation at no coverage cost, whereas 25 -> 50 starts trading noise for fewer clusters
# (49.1% -> 52.1%). 25/5 is the grounded choice at that elbow, not a guess -- per the plan's own
# decision 8/exit-criterion.
_MIN_CLUSTER_SIZE = 25
_MIN_SAMPLES = 5
_TOP_TERMS = 5


def _parse_vector(value: str) -> list[float]:
    """pgvector's own text representation ("[0.1,0.2,...]") -- no Python-side pgvector codec is
    registered in this project (writes already go through a plain literal string, matching
    semantic.py's own _vector_literal), so reads are parsed the same plain way.
    """
    return [float(x) for x in value.strip("[]").split(",")]


def _fetch_vectors(connection: Connection) -> tuple[list[int], list[str], np.ndarray]:
    rows = connection.execute(
        text(
            """
            SELECT e.text_unit_id, t.text, e.vector
            FROM semantic.embedding_bge_small_en_v15 e
            JOIN semantic.text_unit t ON t.id = e.text_unit_id
            ORDER BY e.text_unit_id
            """
        )
    ).all()
    text_unit_ids = [r.text_unit_id for r in rows]
    texts = [r.text for r in rows]
    vectors = np.array([_parse_vector(r.vector) for r in rows], dtype=np.float64)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0  # a zero vector has no direction to normalize; leave it as-is
    normalized = vectors / norms
    return text_unit_ids, texts, normalized


def _cluster(
    vectors: np.ndarray,
    *,
    min_cluster_size: int = _MIN_CLUSTER_SIZE,
    min_samples: int = _MIN_SAMPLES,
) -> np.ndarray:
    # min_cluster_size/min_samples are overridable (not just module constants) so tests can
    # exercise real clustering behavior over a small synthetic population without needing
    # thousands of points to satisfy the real, grounded production defaults.
    #
    # n_jobs=-1 is not optional: the 2026-09-27 parameter-sweep benchmark that grounded
    # _MIN_CLUSTER_SIZE/_MIN_SAMPLES above ran with n_jobs=-1 (parallelized across every core) and
    # still took ~25 minutes per combination on the real 70,145-vector population. A real rollout
    # attempt that omitted this argument (an oversight -- the benchmark script and this production
    # function drifted apart) sat "idle in transaction" over 10 minutes into a single-threaded run
    # before being found and killed; single-threaded, this would have taken hours; this is the
    # exact fix that already brought the earlier benchmark down from 3+ hours to ~25 minutes.
    model = HDBSCAN(
        metric="euclidean", min_cluster_size=min_cluster_size, min_samples=min_samples,
        copy=False, n_jobs=-1,
    )
    return model.fit_predict(vectors)


def _label_cluster(member_indices: np.ndarray, tfidf_matrix, feature_names: np.ndarray) -> str:
    scores = np.asarray(tfidf_matrix[member_indices].sum(axis=0)).flatten()
    top_indices = np.argsort(scores)[::-1][:_TOP_TERMS]
    top_terms = [feature_names[i] for i in top_indices if scores[i] > 0]
    return ", ".join(top_terms) if top_terms else "(no shared terms)"


def _clear_topic_cluster_assignment(connection: Connection, partition_key: str) -> None:
    version_id = active_version_id(connection, "semantic_topic_cluster")
    connection.execute(
        text("DELETE FROM semantic.topic_cluster_assignment WHERE deriver_version_id = :vid"),
        {"vid": version_id},
    )


@deriver(
    name="semantic_topic_cluster",
    partition_class="GLOBAL",
    final_inference="STATISTICAL",
    description=(
        "Unsupervised HDBSCAN topic clustering over Phase 6's message embeddings, extractive "
        "TF-IDF labels (PHASE9_TOPIC_CLUSTERING_IMPLEMENTATION_PLAN.md decisions 2-5)."
    ),
    output_relation="semantic.topic_cluster_assignment",
    inputs=("semantic_embedding_bge_small",),
    params={
        "algorithm": "hdbscan",
        "min_cluster_size": _MIN_CLUSTER_SIZE,
        "min_samples": _MIN_SAMPLES,
        "metric": "euclidean_on_l2_normalized",
    },
    clear_partition=_clear_topic_cluster_assignment,
)
def semantic_topic_cluster(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    text_unit_ids, texts, vectors = _fetch_vectors(connection)
    # Fingerprints the set of embedded units, not the raw float vectors: an embedding is a pure,
    # deterministic function of its text_unit's immutable text (semantic_embedding_bge_small's own
    # clear-and-replace contract), so an unchanged id set really does mean unchanged vectors too --
    # the same cheaper-proxy-for-real-content-identity precedent other GLOBAL derivers already use
    # rather than hashing the full vector population on every sync.
    fingerprint = fingerprint_value(sorted(text_unit_ids))
    if not text_unit_ids:
        return ComputeResult(input_fingerprint=fingerprint, rows=[])

    labels = _cluster(vectors)

    vectorizer = TfidfVectorizer(stop_words="english", max_features=5000)
    tfidf_matrix = vectorizer.fit_transform(texts)
    feature_names = vectorizer.get_feature_names_out()

    version_id = active_version_id(connection, "semantic_topic_cluster")
    label_by_cluster: dict[int, str] = {}
    centroid_by_cluster: dict[int, np.ndarray] = {}
    for cluster_index in set(labels.tolist()):
        if cluster_index == -1:
            label_by_cluster[cluster_index] = "noise"
            continue
        member_indices = np.where(labels == cluster_index)[0]
        label_by_cluster[cluster_index] = _label_cluster(member_indices, tfidf_matrix, feature_names)
        centroid_by_cluster[cluster_index] = vectors[member_indices].mean(axis=0)

    rows = []
    for i, text_unit_id in enumerate(text_unit_ids):
        cluster_index = int(labels[i])
        if cluster_index == -1:
            distance = None
        else:
            distance = float(np.linalg.norm(vectors[i] - centroid_by_cluster[cluster_index]))
        rows.append(
            {
                "text_unit_id": text_unit_id,
                "deriver_version_id": version_id,
                "cluster_index": cluster_index,
                "cluster_label": label_by_cluster[cluster_index],
                "distance_to_centroid": distance,
                "computed_epoch_id": epoch_id,
            }
        )
    return ComputeResult(input_fingerprint=fingerprint, rows=rows)


def list_topic_clusters(engine: Engine, *, min_size: int = 0) -> list[dict]:
    """Every cluster from the active version, ordered by size descending -- decision 4's own CLI,
    answered by a plain GROUP BY over the one table this deriver writes, no second table needed.
    """
    with engine.connect() as connection:
        version_id = active_version_id(connection, "semantic_topic_cluster")
        rows = connection.execute(
            text(
                """
                SELECT cluster_index, cluster_label, count(*) AS size
                FROM semantic.topic_cluster_assignment
                WHERE deriver_version_id = :vid
                GROUP BY cluster_index, cluster_label
                HAVING count(*) >= :min_size
                ORDER BY count(*) DESC
                """
            ),
            {"vid": version_id, "min_size": min_size},
        ).mappings().all()
    return [dict(r) for r in rows]


def show_topic_cluster(engine: Engine, cluster_index: int, *, sample_size: int = 10) -> dict:
    """A cluster's label/size plus a sample of its member text units' first ~200 characters each --
    inspectable from the CLI without a direct SQL query, same precedent claims-why/episode-list
    already set. Raises ValueError for a cluster_index with no rows under the active version.
    """
    with engine.connect() as connection:
        version_id = active_version_id(connection, "semantic_topic_cluster")
        summary = connection.execute(
            text(
                """
                SELECT cluster_label, count(*) AS size
                FROM semantic.topic_cluster_assignment
                WHERE deriver_version_id = :vid AND cluster_index = :cluster_index
                GROUP BY cluster_label
                """
            ),
            {"vid": version_id, "cluster_index": cluster_index},
        ).mappings().first()
        if summary is None:
            raise ValueError(f"no cluster_index {cluster_index} under the active version")
        members = connection.execute(
            text(
                """
                SELECT t.id AS text_unit_id, left(t.text, 200) AS sample
                FROM semantic.topic_cluster_assignment a
                JOIN semantic.text_unit t ON t.id = a.text_unit_id
                WHERE a.deriver_version_id = :vid AND a.cluster_index = :cluster_index
                ORDER BY a.text_unit_id
                LIMIT :sample_size
                """
            ),
            {"vid": version_id, "cluster_index": cluster_index, "sample_size": sample_size},
        ).mappings().all()
    return {
        "cluster_index": cluster_index,
        "cluster_label": summary["cluster_label"],
        "size": summary["size"],
        "members_sample": [dict(m) for m in members],
    }
