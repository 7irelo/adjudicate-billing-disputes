# 2. Parameterised tools and evidence handles, not text-to-SQL

**Decision.** The model reaches records only through written, tested, tenant-scoped queries. Tools return aggregates and a handle, the model cites the handle, and code expands it to record IDs.

**Rejected.** Text-to-SQL adds injection and tenant-escape surface, and is hard to verify. Showing the model raw CDRs invites miscounting and floods its context.

**Consequence.** A citation can narrow a handle to specific IDs, such as the two duplicate lines on an invoice, because citing a whole invoice would make record precision meaningless. `verify` rejects any ID outside the handle.

**What would change this.** Analysts needing ad hoc questions, which would be a separate read-only product, not this decision path.
