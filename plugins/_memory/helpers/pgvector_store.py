"""A vector store backed by pgvector, with the surface `Memory` actually uses.

Written rather than adopted. `langchain-postgres` would have been fewer lines and
brings its own tables, created outside our migrations and therefore outside our
row-level policy -- and memory is the most personal data this product holds. A
library's schema is a poor place for the isolation boundary to live.

The surface is small because `Memory` only ever calls seven things: add, delete,
fetch by id, fetch all, and two kinds of search. They are implemented here with
the same signatures and the same semantics, including the ones that look odd:

**Metadata filters stay Python callables.** FAISS applied them in Python and the
callers build them with `Memory._get_comparator`, which compiles a little
expression language into a closure. Translating that to SQL would mean
reimplementing that language against a second evaluator, and two evaluators of
one expression is two behaviours. Candidates come back from the database and the
closure decides -- the same as before, over a smaller candidate set.

**Scores are relevance, not distance.** pgvector's `<=>` returns cosine distance
where 0 is identical; the callers threshold on relevance where 1 is identical.
The conversion happens here so no caller has to know which convention it is
holding.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Sequence

from langchain_core.documents import Document


class PgVectorStore:
    """One area of memory -- a user's, or a project's."""

    def __init__(self, embedder: Any, area: str, scope: str, owner_id: str | None) -> None:
        self.embedder = embedder
        self.area = area
        self.scope = scope
        self.owner_id = owner_id

    # --- connections ---------------------------------------------------------

    def _transaction(self):
        from helpers import db

        # Scoped to the owner even for project memory, where the policy does not
        # need it. Passing the owner always means there is no code path here
        # that runs unscoped, so a future area that *does* need scoping cannot
        # inherit a connection that never had it.
        return db.transaction(self.owner_id)

    # --- writing -------------------------------------------------------------

    async def aadd_documents(
        self, documents: list[Document], ids: list[str] | None = None
    ) -> list[str]:
        if not documents:
            return []
        texts = [document.page_content for document in documents]
        vectors = await self.embedder.aembed_documents(texts)
        assigned = list(ids or [document.id for document in documents])

        with self._transaction() as conn:
            for doc_id, document, vector in zip(assigned, documents, vectors):
                conn.execute(
                    """
                    INSERT INTO memories (id, scope, owner_id, area, content,
                                          metadata, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        content = EXCLUDED.content,
                        metadata = EXCLUDED.metadata,
                        embedding = EXCLUDED.embedding
                    """,
                    (
                        doc_id, self.scope, self.owner_id, self.area,
                        document.page_content, json.dumps(document.metadata or {}),
                        _literal(vector),
                    ),
                )
        return assigned

    async def adelete(self, ids: Sequence[str] | None = None, **_: Any) -> list[str]:
        """Delete, and return the ids that were actually deleted.

        A project memory written by someone else is not deleted (migration
        0025): the policy filters it out of the DELETE without an error, so the
        returned ids are the only true account of what went.
        """
        if not ids:
            return []
        with self._transaction() as conn:
            rows = conn.execute(
                "DELETE FROM memories WHERE area = %s AND id = ANY(%s) RETURNING id",
                (self.area, list(ids)),
            ).fetchall()
        return [row[0] for row in rows]

    def writable_ids(self, ids: Sequence[str]) -> set[str]:
        """Which of these the caller may change or delete -- the policy's own test.

        Consolidation shows the model only these, so it never plans to replace
        a row it could not replace.
        """
        if not ids:
            return set()
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT id FROM memories WHERE area = %s AND id = ANY(%s) AND "
                "(owner_id = app_user() OR (scope = 'project' AND written_by = app_user()))",
                (self.area, list(ids)),
            ).fetchall()
        return {row[0] for row in rows}

    # --- maintenance, as the owner role ---------------------------------------
    #
    # Two writes in a project's area are nobody's edit: embedding the same text
    # again under a new model, and replacing what was imported from a knowledge
    # file that changed. Under migration 0025 the application role can change
    # only the rows its caller wrote, so either would fail on a colleague's rows
    # (or leave the old import beside the new). Both act only on ids the caller
    # has already read, in this area, and the second only on knowledge rows.

    def _maintenance(self):
        from helpers import db

        return db.unscoped_transaction() if self.scope == "project" else self._transaction()

    async def areembed(self, documents: list[Document]) -> None:
        """New vectors for existing rows; content and writer are untouched."""
        if not documents:
            return
        vectors = await self.embedder.aembed_documents([d.page_content for d in documents])
        with self._maintenance() as conn:
            for document, vector in zip(documents, vectors):
                conn.execute(
                    "UPDATE memories SET embedding = %s WHERE area = %s AND id = %s",
                    (_literal(vector), self.area, document.id),
                )

    # --- project rows with no writer, for an administrator ----------------------
    #
    # A project row written before migration 0025 has no writer, so the policy
    # lets no account change it. An administrator may, from the dashboard: the
    # policy cannot see a permission, so the check is here and the write goes
    # through the owner role, limited to writerless project rows in this area.
    # Automatic memorisation never comes this way.

    def _may_change_unowned(self) -> bool:
        from helpers import rbac, user_scope, users

        return self.scope == "project" and users.holds_permission(
            user_scope.get_current_user_id(), rbac.PERM_SETTINGS_MANAGE
        )

    def unowned_ids(self, ids: Sequence[str]) -> set[str]:
        """Which of these are writerless project rows the caller may change."""
        if not ids or not self._may_change_unowned():
            return set()
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT id FROM memories WHERE area = %s AND id = ANY(%s) "
                "AND scope = 'project' AND written_by IS NULL",
                (self.area, list(ids)),
            ).fetchall()
        return {row[0] for row in rows}

    async def adelete_unowned(self, ids: Sequence[str]) -> list[str]:
        if not ids or not self._may_change_unowned():
            return []
        from helpers import db

        with db.unscoped_transaction() as conn:
            rows = conn.execute(
                "DELETE FROM memories WHERE area = %s AND id = ANY(%s) "
                "AND scope = 'project' AND written_by IS NULL RETURNING id",
                (self.area, list(ids)),
            ).fetchall()
        return [row[0] for row in rows]

    async def aupdate_unowned(self, documents: list[Document]) -> list[str]:
        if not documents or not self._may_change_unowned():
            return []
        from helpers import db

        vectors = await self.embedder.aembed_documents([d.page_content for d in documents])
        changed: list[str] = []
        with db.unscoped_transaction() as conn:
            for document, vector in zip(documents, vectors):
                row = conn.execute(
                    "UPDATE memories SET content = %s, metadata = %s, embedding = %s "
                    "WHERE area = %s AND id = %s AND scope = 'project' AND written_by IS NULL "
                    "RETURNING id",
                    (
                        document.page_content, json.dumps(document.metadata or {}),
                        _literal(vector), self.area, document.id,
                    ),
                ).fetchone()
                if row:
                    changed.append(row[0])
        return changed

    async def adelete_knowledge(self, ids: Sequence[str]) -> list[str]:
        """Remove rows that came from a knowledge file, whoever imported them."""
        if not ids:
            return []
        with self._maintenance() as conn:
            rows = conn.execute(
                "DELETE FROM memories WHERE area = %s AND id = ANY(%s) "
                "AND metadata->>'knowledge_source' = 'true' RETURNING id",
                (self.area, list(ids)),
            ).fetchall()
        return [row[0] for row in rows]

    def delete(self, ids: Sequence[str] | None = None, **kwargs: Any) -> list[str]:
        import asyncio

        return asyncio.get_event_loop().run_until_complete(self.adelete(ids, **kwargs))

    # --- reading -------------------------------------------------------------

    def get_by_ids(self, ids: Sequence[str] | str, /) -> list[Document]:
        wanted = [ids] if isinstance(ids, str) else list(ids)
        if not wanted:
            return []
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT id, content, metadata FROM memories "
                "WHERE area = %s AND id = ANY(%s)",
                (self.area, wanted),
            ).fetchall()
        return [_document(row) for row in rows]

    async def aget_by_ids(self, ids: Sequence[str], /) -> list[Document]:
        return self.get_by_ids(ids)

    def get_all_docs(self) -> dict[str, Document]:
        """Every document in this area, keyed by id.

        The callers walk this to find exact matches and to follow references
        between documents. It reached into FAISS's private docstore before, which
        is one of the reasons this is better as a query.
        """
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT id, content, metadata FROM memories WHERE area = %s",
                (self.area,),
            ).fetchall()
        return {row[0]: _document(row) for row in rows}

    # --- search --------------------------------------------------------------

    async def asimilarity_search_with_relevance_scores(
        self,
        query: str,
        k: int = 4,
        score_threshold: float | None = None,
        filter: Callable[[dict], bool] | None = None,
        **_: Any,
    ) -> list[tuple[Document, float]]:
        vector = await self.embedder.aembed_query(query)

        # Over-fetched on purpose when a filter is present: the filter runs after
        # the ranking, so asking for exactly k would return fewer than k whenever
        # any of the top k is filtered out. FAISS had the same problem and the
        # same answer.
        limit = k * 10 if filter else k
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT id, content, metadata, 1 - (embedding <=> %s) AS relevance
                  FROM memories
                 WHERE area = %s
                 ORDER BY embedding <=> %s
                 LIMIT %s
                """,
                (_literal(vector), self.area, _literal(vector), limit),
            ).fetchall()

        results: list[tuple[Document, float]] = []
        for row in rows:
            relevance = float(row[3])
            if score_threshold is not None and relevance < score_threshold:
                continue
            document = _document(row)
            if filter and not filter(document.metadata or {}):
                continue
            results.append((document, relevance))
            if len(results) >= k:
                break
        return results

    async def asearch(
        self, query: str, search_type: str = "similarity", k: int = 4, **kwargs: Any
    ) -> list[Document]:
        scored = await self.asimilarity_search_with_relevance_scores(query, k=k, **kwargs)
        return [document for document, _ in scored]


def _document(row: Any) -> Document:
    metadata = row[2] or {}
    return Document(id=row[0], page_content=row[1] or "", metadata=dict(metadata))


def _literal(vector: Sequence[float]) -> str:
    """pgvector's text form.

    Sent as a string rather than through a registered adapter so this module
    needs no psycopg type registration at import time -- which would have to
    happen on every connection in the pool, including ones opened before this
    module is first imported.
    """
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"
