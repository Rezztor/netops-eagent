"""Agent memory in the database, and the policy that keeps it apart.

This is the most sensitive policy in the product. A vector store holds the text
of what somebody told the agent, and ADR 0003 records that this exact data has
leaked once before: the FAISS cache was keyed by a subdirectory name that is
`main` for every user, so the first user to load it populated the cache for
everyone. The path was per-user; the key was not.

The replacement is a row-level policy, and `test_one_persons_memories_are_not_in
_another_persons_recall` is the test that says so. The store's queries do not
mention `owner_id` at all -- if the policy were dropped, that test would fail and
nothing else would.

A fake embedder is used throughout. These tests are about storage and isolation,
and a real model would make them about the model.
"""

from __future__ import annotations

import pytest
from langchain_core.documents import Document

from helpers import db
from plugins._memory.helpers.pgvector_store import PgVectorStore


class CountingEmbedder:
    """Deterministic vectors, so similarity is predictable and offline.

    Each text maps to a point on a circle by its first character, which makes
    "apple" and "apricot" close and "zebra" far without involving a model.
    """

    def __init__(self) -> None:
        self.calls = 0

    def _vector(self, text: str) -> list[float]:
        import math

        angle = (ord(text.strip().lower()[0]) - 97) / 26 * 2 * math.pi
        return [math.cos(angle), math.sin(angle)]

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [self._vector(text) for text in texts]

    async def aembed_query(self, text: str) -> list[float]:
        self.calls += 1
        return self._vector(text)


def store_for(owner: str | None, area: str = "main", scope: str = "user") -> PgVectorStore:
    return PgVectorStore(CountingEmbedder(), area=area, scope=scope, owner_id=owner)


def doc(doc_id: str, text: str, **metadata) -> Document:
    return Document(id=doc_id, page_content=text, metadata=metadata)


# --- the property that matters most ------------------------------------------

@pytest.mark.asyncio
async def test_one_persons_memories_are_not_in_another_persons_recall(database):
    """The bug ADR 0003 records, prevented structurally this time."""
    await store_for("asha").aadd_documents([doc("a1", "asha remembers apples")])
    await store_for("bruno").aadd_documents([doc("b1", "bruno remembers apples")])

    found = await store_for("asha").asimilarity_search_with_relevance_scores("apples", k=10)
    assert [document.id for document, _ in found] == ["a1"]

    everything = store_for("asha").get_all_docs()
    assert set(everything) == {"a1"}


@pytest.mark.asyncio
async def test_a_memory_cannot_be_written_for_someone_else(database):
    """The policy checks writes as well as reads. A store built with one owner
    cannot insert a row owned by another, so a bug in scope resolution fails
    loudly instead of quietly filing a memory in the wrong person's head."""
    import psycopg

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.transaction("asha") as conn:
            conn.execute(
                "INSERT INTO memories (id, scope, owner_id, area, content, embedding) "
                "VALUES ('x', 'user', 'bruno', 'main', 'forged', '[0,1]')"
            )


@pytest.mark.asyncio
async def test_fetching_by_id_is_scoped_too(database):
    """Knowing another user's document id must not be enough."""
    await store_for("bruno").aadd_documents([doc("b1", "bruno's note")])
    assert store_for("asha").get_by_ids(["b1"]) == []


# --- project memory is shared, deliberately ----------------------------------

@pytest.mark.asyncio
async def test_project_memory_is_shared_the_way_it_always_was(database):
    """Project memory was a shared directory and is a shared scope. Anyone
    signed in can read it, which is the decision that was made when it was
    files."""
    from helpers import user_scope

    project = store_for(None, area="projects/atlas", scope="project")
    with user_scope.user_scope("bruno"):
        await project.aadd_documents([doc("p1", "atlas deployment notes")])

    from plugins._memory.helpers.pgvector_store import PgVectorStore as Store

    as_asha = Store(CountingEmbedder(), area="projects/atlas", scope="project", owner_id="asha")
    assert set(as_asha.get_all_docs()) == {"p1"}


def project_store() -> PgVectorStore:
    """A project's area, written as whoever the ambient scope says."""
    return store_for(None, area="projects/atlas", scope="project")


@pytest.mark.asyncio
async def test_a_project_memory_is_changed_only_by_whoever_wrote_it(database):
    """Migration 0025. Everyone reads a project's memories; only the writer
    deletes or replaces one. Before it, any account could, and automatic
    consolidation did it without anyone asking."""
    import psycopg

    from helpers import user_scope

    with user_scope.user_scope("asha"):
        await project_store().aadd_documents([doc("p1", "atlas runs on the blue cluster")])

    with user_scope.user_scope("bruno"):
        assert set(project_store().get_all_docs()) == {"p1"}, "reading is shared"
        assert project_store().writable_ids(["p1"]) == set()
        assert await project_store().adelete(["p1"]) == []
        with db.transaction("bruno") as conn:
            assert conn.execute("UPDATE memories SET content = 'injected' WHERE id = 'p1'").rowcount == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await project_store().aadd_documents([doc("p1", "atlas runs on the red cluster")])

    with user_scope.user_scope("asha"):
        assert project_store().get_all_docs()["p1"].page_content == "atlas runs on the blue cluster"
        assert project_store().writable_ids(["p1"]) == {"p1"}
        assert await project_store().adelete(["p1"]) == ["p1"]


def test_the_writer_of_a_project_memory_cannot_be_named_by_the_caller(database):
    """Forging the writer would make a colleague's own consolidation treat the
    row as theirs to merge."""
    import psycopg

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.transaction("bruno") as conn:
            conn.execute(
                "INSERT INTO memories (id, scope, area, content, embedding, written_by) "
                "VALUES ('x', 'project', 'projects/atlas', 'forged', '[0,1]', 'asha')"
            )


@pytest.mark.asyncio
async def test_a_project_memory_with_no_account_to_attribute_it_to_is_refused(database):
    import psycopg

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await project_store().aadd_documents([doc("p1", "nobody wrote this")])


@pytest.mark.asyncio
async def test_maintenance_reaches_a_colleagues_project_rows(database):
    """Re-embedding and replacing imported knowledge are nobody's edit: they
    must still work on rows another account wrote, and the knowledge path
    touches nothing that did not come from a knowledge file."""
    from helpers import user_scope

    with user_scope.user_scope("asha"):
        await project_store().aadd_documents([
            doc("k1", "apple from a file", knowledge_source=True),
            doc("m1", "apple from a chat"),
        ])

    with user_scope.user_scope("bruno"):
        store = project_store()
        await store.areembed([doc("m1", "zebra")])
        assert await store.adelete_knowledge(["k1", "m1"]) == ["k1"]
        ((found, score),) = await store.asimilarity_search_with_relevance_scores("zebra", k=1)
    assert found.id == "m1" and score == pytest.approx(1.0, abs=1e-6)
    assert found.page_content == "apple from a chat", "re-embedding changes the vector, not the text"


@pytest.mark.asyncio
async def test_consolidation_offers_only_the_writers_own_memories(database, monkeypatch):
    """Auto-memorisation consolidates a new fragment into similar ones. In a
    project's area the similar ones may be a colleague's, and they are not
    shown to the model at all, so it cannot plan to merge or replace them."""
    from unittest.mock import AsyncMock, MagicMock

    from helpers import user_scope
    from plugins._memory.helpers import memory_consolidation
    from plugins._memory.helpers.memory import Memory

    with user_scope.user_scope("asha"):
        await project_store().aadd_documents([doc("a1", "apple", id="a1", area="fragments")])
    with user_scope.user_scope("bruno"):
        await project_store().aadd_documents([doc("b1", "apricot", id="b1", area="fragments")])

    monkeypatch.setattr(Memory, "get", AsyncMock(side_effect=lambda agent: Memory(project_store(), "projects/atlas")))
    consolidator = memory_consolidation.MemoryConsolidator(MagicMock(), memory_consolidation.ConsolidationConfig(similarity_threshold=0.5))
    monkeypatch.setattr(consolidator, "_extract_search_keywords", AsyncMock(return_value=[]))

    with user_scope.user_scope("bruno"):
        similar = await consolidator._find_similar_memories("apple", "fragments")
    assert [d.metadata["id"] for d in similar] == ["b1"]


@pytest.mark.asyncio
async def test_an_administrator_can_tidy_project_memories_that_have_no_writer(database, monkeypatch):
    """Rows from before migration 0025 have no writer, so the policy lets no one
    change them. An administrator may, from the dashboard; a plain account still
    may not, and nothing automatic takes this path."""
    from helpers import rbac, user_scope, users
    from plugins._memory.helpers.memory import Memory

    admin = users.create_user("root", "admin-password-1", rbac.ROLE_ADMIN)["id"]
    plain = users.create_user("bruno", "plain-password-1", rbac.ROLE_USER)["id"]
    with db.unscoped_transaction() as conn:
        for row_id, writer in (("L1", None), ("L2", None), ("B1", plain)):
            conn.execute(
                "INSERT INTO memories (id, scope, area, content, metadata, embedding, written_by) "
                "VALUES (%s, 'project', 'projects/atlas', 'old', %s, '[1,0]', %s)",
                (row_id, '{"id": "%s"}' % row_id, writer),
            )

    def memory():
        mem = Memory(project_store(), "projects/atlas")
        mem._save_db = lambda: None
        return mem

    edited = lambda row_id: Document(page_content="apple, tidied", metadata={"id": row_id})

    with user_scope.user_scope(plain):
        assert await memory().delete_documents_by_ids(["L1"], unowned_too=True) == []
        with pytest.raises(ValueError):
            await memory().update_documents([edited("L1")], unowned_too=True)

    with user_scope.user_scope(admin):
        assert await memory().delete_documents_by_ids(["L2"]) == [], "only when asked for"
        assert await memory().update_documents([edited("L1")], unowned_too=True) == ["L1"]
        assert [d.id for d in await memory().delete_documents_by_ids(["L2"], unowned_too=True)] == ["L2"]
        assert await memory().delete_documents_by_ids(["B1"], unowned_too=True) == [], "a colleague's row is still theirs"
        assert set(project_store().get_all_docs()) == {"L1", "B1"}
        assert project_store().get_all_docs()["L1"].page_content == "apple, tidied"


def test_a_personal_memory_without_an_owner_cannot_be_stored(database):
    """The unsafe combination is refused by the schema rather than avoided by
    care. "owner_id is null means shared" would turn a forgotten owner into a
    silent leak; a CHECK turns it into an error."""
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        with db.unscoped_transaction() as conn:
            conn.execute(
                "INSERT INTO memories (id, scope, owner_id, area, content, embedding) "
                "VALUES ('orphan', 'user', NULL, 'main', 'whose is this', '[0,1]')"
            )


# --- ordinary behaviour -------------------------------------------------------

@pytest.mark.asyncio
async def test_search_ranks_by_similarity(database):
    await store_for("asha").aadd_documents([
        doc("apple", "apple"), doc("apricot", "apricot"), doc("zebra", "zebra"),
    ])
    found = await store_for("asha").asimilarity_search_with_relevance_scores("apple", k=2)
    assert [document.id for document, _ in found] == ["apple", "apricot"]


@pytest.mark.asyncio
async def test_scores_are_relevance_not_distance(database):
    """pgvector returns cosine distance where 0 is identical; the callers
    threshold on relevance where 1 is. Getting this backwards would make every
    threshold reject the best match and keep the worst."""
    await store_for("asha").aadd_documents([doc("apple", "apple")])
    ((_, score),) = await store_for("asha").asimilarity_search_with_relevance_scores(
        "apple", k=1
    )
    assert score == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
async def test_a_threshold_excludes_the_far_ones(database):
    await store_for("asha").aadd_documents([doc("apple", "apple"), doc("mango", "mango")])
    found = await store_for("asha").asimilarity_search_with_relevance_scores(
        "apple", k=10, score_threshold=0.99
    )
    assert [document.id for document, _ in found] == ["apple"]


@pytest.mark.asyncio
async def test_a_metadata_filter_still_runs_in_python(database):
    """The callers compile a little expression language into a closure. Two
    evaluators of one expression would be two behaviours, so the closure decides
    and the database only narrows."""
    await store_for("asha").aadd_documents([
        doc("keep", "apple", area="fragments"), doc("drop", "apricot", area="solutions"),
    ])
    found = await store_for("asha").asimilarity_search_with_relevance_scores(
        "apple", k=10, filter=lambda meta: meta.get("area") == "fragments"
    )
    assert [document.id for document, _ in found] == ["keep"]


@pytest.mark.asyncio
async def test_a_filter_does_not_shrink_the_result_below_k(database):
    """The filter runs after the ranking, so asking the database for exactly k
    would return fewer than k whenever any of the top k is filtered out."""
    await store_for("asha").aadd_documents(
        [doc(f"d{i}", "apple", keep=(i % 2 == 0)) for i in range(20)]
    )
    found = await store_for("asha").asimilarity_search_with_relevance_scores(
        "apple", k=5, filter=lambda meta: bool(meta.get("keep"))
    )
    assert len(found) == 5


@pytest.mark.asyncio
async def test_adding_the_same_id_twice_replaces_rather_than_duplicates(database):
    await store_for("asha").aadd_documents([doc("a1", "apple")])
    await store_for("asha").aadd_documents([doc("a1", "apricot")])
    docs = store_for("asha").get_all_docs()
    assert len(docs) == 1 and docs["a1"].page_content == "apricot"


@pytest.mark.asyncio
async def test_deleting_removes_it(database):
    await store_for("asha").aadd_documents([doc("a1", "apple"), doc("a2", "apricot")])
    await store_for("asha").adelete(["a1"])
    assert set(store_for("asha").get_all_docs()) == {"a2"}


@pytest.mark.asyncio
async def test_two_areas_do_not_see_each_other(database):
    """One person can have several indexes -- their main memory, an agent
    profile's. They are separate stores and must stay separate."""
    await store_for("asha", area="main").aadd_documents([doc("m1", "apple")])
    await store_for("asha", area="other").aadd_documents([doc("o1", "apple")])
    assert set(store_for("asha", area="main").get_all_docs()) == {"m1"}


@pytest.mark.asyncio
async def test_nothing_is_embedded_when_there_is_nothing_to_add(database):
    embedder = CountingEmbedder()
    store = PgVectorStore(embedder, area="main", scope="user", owner_id="asha")
    assert await store.aadd_documents([]) == []
    assert embedder.calls == 0


# --- a new embedding model, and whose memories are indexed again ---------------


@pytest.fixture
def reindexing(database, tmp_path, monkeypatch):
    """`Memory.initialize` with a model that records what it was asked to embed.

    Returns (load, embedded): `load(owner, model)` opens that account's main
    memory under that model and returns the store; `embedded` is the list of
    (model, text) pairs embedded so far. The two models' vectors differ in
    length, as two real models' do.
    """
    from types import SimpleNamespace

    from helpers import files, user_scope
    from plugins._memory.helpers import memory

    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    embedded: list[tuple[str, str]] = []

    class Embedder:
        def __init__(self, model):
            self.model = model

        def _vector(self, text):
            embedded.append((self.model, text))
            return [1.0, 0.0] if self.model == "old" else [1.0, 0.0, 0.0]

        async def aembed_documents(self, texts):
            return [self._vector(text) for text in texts]

        async def aembed_query(self, text):
            return self._vector(text)

    monkeypatch.setattr(memory.models, "get_embedding_model", lambda provider, name, **kwargs: name)
    monkeypatch.setattr(
        memory.CacheBackedEmbeddings, "from_bytes_store", staticmethod(lambda model, store, namespace=None: Embedder(model))
    )

    def load(owner, model):
        config = SimpleNamespace(provider="test", name=model, build_kwargs=lambda: {})
        with user_scope.user_scope(owner):
            store, _ = memory.Memory.initialize(None, config, "main", in_memory=True)
            return store

    return load, embedded


def _remember(load, owner, model="old"):
    import asyncio

    from helpers import user_scope

    store = load(owner, model)
    with user_scope.user_scope(owner):
        asyncio.run(store.aadd_documents([doc(f"{owner}-1", f"{owner} remembers")]))


def test_stored_memories_are_indexed_again_when_the_embedding_model_changes(reindexing):
    """Vectors from two models are not comparable, so a change of model indexes
    the stored memories again. The code that does it calls `asyncio.run` in a
    module that never imported `asyncio`: for an account with any memory at all,
    opening its memory after the model changed raised NameError, every time,
    and the memory could not be used again."""
    load, embedded = reindexing
    _remember(load, "asha")
    embedded.clear()

    load("asha", "new")

    assert embedded == [("new", "asha remembers")]



def test_every_account_is_indexed_again_and_only_once(reindexing):
    """The marker that says "already indexed for this model" was one for the
    whole instance, while a person's memories are their own. The first account
    to open its memory after the change indexed its own rows and wrote the
    marker; every other account's memories kept the old model's vectors for
    good, and their recall failed on vectors of the wrong length."""
    load, embedded = reindexing
    _remember(load, "asha")
    _remember(load, "bruno")
    embedded.clear()

    load("asha", "new")
    load("bruno", "new")

    assert ("new", "asha remembers") in embedded
    assert ("new", "bruno remembers") in embedded, "the second account's memories kept the old model's vectors"

    embedded.clear()
    load("asha", "new")
    load("bruno", "new")
    assert embedded == [], "indexed again although nothing changed"


def test_memory_areas_come_from_the_database_not_a_faiss_file(database):
    """The dashboard listed project areas only when `memory/index.faiss` existed
    on disk. Memories live in PostgreSQL now, so nothing writes that file and a
    project with rows was invisible. Areas come from the table instead."""
    import asyncio

    from helpers import user_scope
    from plugins._memory.helpers.memory import get_existing_memory_subdirs

    with user_scope.user_scope("bruno"):
        asyncio.run(
            store_for(None, area="projects/atlas", scope="project").aadd_documents(
                [doc("p1", "atlas deployment notes")]
            )
        )
    asyncio.run(
        store_for("asha", area="custom").aadd_documents([doc("a1", "asha note")])
    )

    with user_scope.user_scope("asha"):
        areas = get_existing_memory_subdirs()

    assert "projects/atlas" in areas
    assert "custom" in areas
    assert "default" in areas


@pytest.mark.asyncio
async def test_consolidation_replace_still_removes_the_old_memory():
    """REPLACE re-fetched the target by id to read `_consolidation_similarity`,
    but that score only lived on the search hit and was never stored. The
    re-fetch defaulted to 0.7, below the 0.75 safety threshold, so every
    REPLACE was converted to KEEP_SEPARATE and the old row stayed."""
    from unittest.mock import AsyncMock, MagicMock

    from plugins._memory.helpers.memory_consolidation import (
        ConsolidationAction,
        ConsolidationConfig,
        ConsolidationResult,
        MemoryConsolidator,
    )

    consolidator = MemoryConsolidator(agent=MagicMock(), config=ConsolidationConfig())
    mem = Document(page_content="old fact", metadata={"id": "m1"})
    db = MagicMock()
    db.db = MagicMock()
    db.db.aget_by_ids = AsyncMock(return_value=[mem])
    db.delete_documents_by_ids = AsyncMock()
    db.insert_text = AsyncMock(return_value="new1")

    result = ConsolidationResult(
        action=ConsolidationAction.REPLACE,
        memories_to_remove=["m1"],
        new_memory_content="updated fact",
        shown_similarity={"m1": 0.92},
    )
    ids = await consolidator._handle_replace(db, result, "main", {})

    assert ids == ["new1"]
    db.delete_documents_by_ids.assert_awaited_once_with(["m1"])
    assert db.insert_text.await_args.args[1]["consolidation_action"] == "replace"

    # A memory the model was not shown, or one too unlike the new text, is kept: the new one goes in beside it.
    for shown in ({}, {"m1": 0.5}):
        db.delete_documents_by_ids.reset_mock()
        result = ConsolidationResult(
            action=ConsolidationAction.REPLACE, memories_to_remove=["m1"], new_memory_content="updated fact",
            shown_similarity=shown,
        )
        await consolidator._handle_replace(db, result, "main", {})
        db.delete_documents_by_ids.assert_not_awaited()
        assert db.insert_text.await_args.args[1]["consolidation_action"] == "keep_separate_safety"


@pytest.mark.asyncio
async def test_a_memorys_writer_scope_and_area_cannot_be_rewritten(database):
    """Migration 0027. 0025's WITH CHECK sees only the new row, so the owner of
    a private memory could name a colleague as its writer or move it into a
    project, where the colleague's consolidation would treat it as theirs."""
    import psycopg

    await store_for("asha").aadd_documents([doc("a1", "asha's own note")])

    for column, value in (
        ("written_by", "bruno"),
        ("scope", "project"),
        ("area", "projects/atlas"),
        ("owner_id", "bruno"),
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with db.transaction("asha") as conn:
                conn.execute(f"UPDATE memories SET {column} = %s WHERE id = 'a1'", (value,))

    with db.transaction("asha") as conn:
        assert conn.execute("UPDATE memories SET content = 'edited' WHERE id = 'a1'").rowcount == 1
