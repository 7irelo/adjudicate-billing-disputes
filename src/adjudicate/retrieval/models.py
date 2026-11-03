"""Dense and reranking models, loaded lazily. Requires the `ml` extra and a first-run download."""

from collections.abc import Sequence

import psycopg

from adjudicate.config import get_settings


class BgeEmbedder:
    """BAAI/bge-m3 (MIT, 1024 dimensions), normalised so cosine distance is meaningful."""

    def __init__(self, model: str = "BAAI/bge-m3") -> None:
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model, device="cpu")

    def encode_query(self, text: str) -> list[float]:
        return self.model.encode(text, normalize_embeddings=True).tolist()

    def encode_many(self, texts: Sequence[str], batch_size: int = 16) -> list[list[float]]:
        return self.model.encode(
            list(texts), normalize_embeddings=True, batch_size=batch_size
        ).tolist()


class BgeReranker:
    """BAAI/bge-reranker-v2-m3 (Apache 2.0). CPU latency is why the candidate count is capped."""

    def __init__(self, model: str = "BAAI/bge-reranker-v2-m3") -> None:
        from sentence_transformers import CrossEncoder

        self.model = CrossEncoder(model, device="cpu", max_length=512)

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        return [float(s) for s in self.model.predict([(query, t) for t in texts])]


def embed_all_chunks(dsn: str | None = None) -> int:
    """Fill `chunks.embedding` for chunks that lack one. Runs as the admin role, offline."""
    embedder = BgeEmbedder()
    with psycopg.connect(dsn or get_settings().admin_dsn) as conn:
        rows = conn.execute(
            "SELECT chunk_id, match_text FROM chunks WHERE embedding IS NULL ORDER BY chunk_id"
        ).fetchall()
        vectors = embedder.encode_many([r[1] for r in rows])
        for (chunk_id, _), vector in zip(rows, vectors, strict=True):
            literal = "[" + ",".join(f"{x:.6f}" for x in vector) + "]"
            conn.execute(
                "UPDATE chunks SET embedding = %s::vector WHERE chunk_id = %s", (literal, chunk_id)
            )
        conn.commit()
    return len(rows)


if __name__ == "__main__":
    print("embedded", embed_all_chunks())
