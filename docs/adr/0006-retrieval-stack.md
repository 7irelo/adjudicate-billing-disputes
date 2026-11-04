# 6. Retrieval: pgvector, bm25s, RRF, BGE reranker

**Decision.** `bm25s` per tenant, `BAAI/bge-m3` in pgvector with an exact scan, RRF fusion, and `BAAI/bge-reranker-v2-m3`. Vectors in Postgres fall under the same RLS policies as everything else. Serving defaults to BM25 on strategy D with query rewriting. Dense scored higher on authored documents (0.99 against 0.94 recall@5 with rewriting, on draft labels), but that gain didn't appear on a long real gazette, and it would add torch and a 4 GB model to the demo image. Reranking took 11 seconds per query on the laptop CPU.

**Rejected.** Qdrant adds a service and a second isolation story. Postgres `ts_rank` isn't BM25. Weighted fusion needs score normalisation and more labels to tune.

**Measure, don't assume.** Chunking strategy and retrieval mode are decided by `evals/retrieval_eval.py`. Results must be reported separately for real and authored documents, because clause-aware chunking may only win on documents structured by the same author.

**What would change this.** Owner-labelled results showing dense helps on real documents, a GPU for reranking, or a corpus large enough to need an HNSW index or a dedicated store.
