# 6. Retrieval: pgvector, bm25s, RRF, BGE reranker

**Decision.** `bm25s` per tenant, `BAAI/bge-m3` in pgvector with an exact scan, RRF fusion, and `BAAI/bge-reranker-v2-m3`. Vectors in Postgres fall under the same RLS policies as everything else. Serving defaults to BM25 on strategy D with query rewriting, because that needs no model download and rewriting measured better on dev. Change it only on measured evidence.

**Rejected.** Qdrant adds a service and a second isolation story. Postgres `ts_rank` isn't BM25. Weighted fusion needs score normalisation and more labels to tune.

**Measure, don't assume.** Chunking strategy and retrieval mode are decided by `evals/retrieval_eval.py`. Results must be reported separately for real and authored documents, because clause-aware chunking may only win on documents structured by the same author.

**What would change this.** A corpus large enough to need an HNSW index or a dedicated store.
