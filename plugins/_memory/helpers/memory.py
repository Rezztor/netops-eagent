from typing import Any, List, Sequence
from langchain_core.stores import InMemoryByteStore
from langchain_classic.storage import LocalFileStore
from langchain_classic.embeddings import CacheBackedEmbeddings
from helpers import guids

# from langchain_chroma import Chroma
from langchain_community.vectorstores import FAISS

# faiss needs to be patched for python 3.12 on arm #TODO remove once not needed
from helpers import faiss_monkey_patch
import faiss


from langchain_community.docstore.in_memory import InMemoryDocstore
from langchain_community.vectorstores.utils import (
    DistanceStrategy,
)
from langchain_core.embeddings import Embeddings

import ast, asyncio, os, json, hashlib, re

import numpy as np

from helpers.print_style import PrintStyle
from plugins._memory.helpers.pgvector_store import PgVectorStore
from helpers import files, plugins, projects
from helpers.localization import Localization
from langchain_core.documents import Document
from . import knowledge_import
from helpers.log import Log, LogItem
from enum import Enum
from agent import Agent, AgentContext
import models
import logging
from simpleeval import SimpleEval


# Raise the log level so WARNING messages aren't shown
logging.getLogger("langchain_core.vectorstores.base").setLevel(logging.ERROR)


# MyFaiss is gone with the index it wrapped. It existed to reach into FAISS's
# private docstore for get_all_docs and get_by_ids -- both of which are ordinary
# queries now, which is a decent sign the wrapper was working around the store
# rather than with it.


class Memory:

    class Area(Enum):
        MAIN = "main"
        FRAGMENTS = "fragments"
        SOLUTIONS = "solutions"

    # Cache of loaded FAISS indexes, keyed by the resolved database directory
    # rather than by the memory subdir name.
    #
    # This matters: memory is stored per user, so two users share the subdir
    # name "main" while having entirely different databases. Keying by name
    # meant the first user to load "main" populated the cache and every
    # subsequent user was handed that same index -- one person's memories
    # surfacing in another person's recall.
    #
    # Keying by resolved path is correct by construction: per-user memory
    # resolves to distinct paths and gets distinct entries, while shared project
    # memory resolves to one path and is correctly shared.
    index: dict[str, "PgVectorStore"] = {}

    @staticmethod
    def index_key(memory_subdir: str) -> str:
        return abs_db_dir(memory_subdir)

    @staticmethod
    def _get_embedding_config(agent=None):
        from plugins._model_config.helpers.model_config import get_embedding_model_config_object
        return get_embedding_model_config_object(agent)

    @staticmethod
    async def get(agent: Agent):
        memory_subdir = get_agent_memory_subdir(agent)
        cache_key = Memory.index_key(memory_subdir)
        if Memory.index.get(cache_key) is None:
            log_item = agent.context.log.log(
                type="util",
                heading=f"Initializing VectorDB in '/{memory_subdir}'",
            )
            db, created = Memory.initialize(
                log_item,
                Memory._get_embedding_config(agent),
                memory_subdir,
                False,
            )
            Memory.index[cache_key] = db
            wrap = Memory(db, memory_subdir=memory_subdir)
            knowledge_subdirs = get_knowledge_subdirs_by_memory_subdir(
                memory_subdir, agent.config.knowledge_subdirs or []
            )
            if knowledge_subdirs:
                await wrap.preload_knowledge(log_item, knowledge_subdirs, memory_subdir)
            return wrap
        else:
            return Memory(
                db=Memory.index[cache_key],
                memory_subdir=memory_subdir,
            )

    @staticmethod
    async def get_by_subdir(
        memory_subdir: str,
        log_item: LogItem | None = None,
        preload_knowledge: bool = True,
    ):
        cache_key = Memory.index_key(memory_subdir)
        if not Memory.index.get(cache_key):
            import initialize

            agent_config = initialize.initialize_agent()
            model_config = Memory._get_embedding_config()
            db, _created = Memory.initialize(
                log_item=log_item,
                model_config=model_config,
                memory_subdir=memory_subdir,
                in_memory=False,
            )
            wrap = Memory(db, memory_subdir=memory_subdir)
            if preload_knowledge:
                knowledge_subdirs = get_knowledge_subdirs_by_memory_subdir(
                    memory_subdir, agent_config.knowledge_subdirs or []
                )
                if knowledge_subdirs:
                    await wrap.preload_knowledge(
                        log_item, knowledge_subdirs, memory_subdir
                    )
            Memory.index[cache_key] = db
        return Memory(db=Memory.index[cache_key], memory_subdir=memory_subdir)

    @staticmethod
    async def reload(agent: Agent):
        memory_subdir = get_agent_memory_subdir(agent)
        cache_key = Memory.index_key(memory_subdir)
        if Memory.index.get(cache_key):
            del Memory.index[cache_key]
        return await Memory.get(agent)

    @staticmethod
    def initialize(
        log_item: LogItem | None,
        model_config: models.ModelConfig,
        memory_subdir: str,
        in_memory=False,
    ) -> tuple[PgVectorStore, bool]:

        PrintStyle.standard("Initializing VectorDB...")

        if log_item:
            log_item.stream(progress="\nInitializing VectorDB")

        # Shared on purpose: this caches embedding vectors keyed by a hash of
        # the text. It holds no user content that is not already known to
        # whoever supplies the same text, and sharing it avoids re-embedding the
        # same knowledge base for every account.
        em_dir = files.get_abs_path("tmp/memory/embeddings")
        db_dir = abs_db_dir(memory_subdir)

        # make sure embeddings and database directories exist
        os.makedirs(db_dir, exist_ok=True)

        if in_memory:
            store = InMemoryByteStore()
        else:
            os.makedirs(em_dir, exist_ok=True)
            store = LocalFileStore(em_dir)

        embeddings_model = models.get_embedding_model(
            model_config.provider,
            model_config.name,
            **model_config.build_kwargs(),
        )
        embeddings_model_id = files.safe_file_name(
            model_config.provider + "_" + model_config.name
        )

        # here we setup the embeddings model with the chosen cache storage
        embedder = CacheBackedEmbeddings.from_bytes_store(
            embeddings_model, store, namespace=embeddings_model_id
        )

        # Which index this is, and who may read it. `projects/<name>` memory is
        # shared by everyone with access to the project -- a deliberate decision
        # from when it was a shared directory -- and everything else is private
        # to one account.
        scope, owner_id = memory_scope(memory_subdir)

        db = PgVectorStore(
            embedder=embedder,
            area=memory_subdir,
            scope=scope,
            owner_id=owner_id,
        )

        # Re-index when the embedding model changes. Vectors from two models are
        # not comparable, so a mixed index returns nonsense rather than nothing
        # -- which is the worse of the two failures, because it looks like an
        # answer. This is the same check the `embedding.json` file used to do.
        from helpers import kvp

        # The marker belongs to whoever the rows belong to. A person's area has
        # the same name for everybody -- `main` -- and `get_all_docs` below
        # returns only the caller's own rows, so with one marker for the
        # instance the first account to arrive indexed its own memories and
        # every other account's kept the old model's vectors for good.
        marker = "memory_embedding_" + files.safe_file_name(memory_subdir)
        per_account = scope == "user" and bool(owner_id)
        stored = (
            kvp.get_user_persistent(marker, user_id=owner_id) if per_account else kvp.get_persistent(marker)
        ) or {}
        wanted = {"model_provider": model_config.provider, "model_name": model_config.name}

        created = False
        if stored != wanted:
            existing = db.get_all_docs()
            if existing:
                PrintStyle.standard("Re-indexing memories for a new embedding model...")
                if log_item:
                    log_item.stream(progress="\nRe-indexing memories")
                # Vectors only: in a project's area most rows are someone
                # else's, and only their writer may change them otherwise.
                asyncio.run(db.areembed(list(existing.values())))
            if per_account:
                kvp.set_user_persistent(marker, wanted, user_id=owner_id)
            else:
                kvp.set_persistent(marker, wanted)
            created = not existing

        return db, created

    def __init__(
        self,
        db: PgVectorStore,
        memory_subdir: str,
    ):
        self.db = db
        self.memory_subdir = memory_subdir

    async def preload_knowledge(
        self, log_item: LogItem | None, kn_dirs: list[str], memory_subdir: str
    ):
        if log_item:
            log_item.update(heading="Preloading knowledge...")

        # db abs path
        db_dir = abs_db_dir(memory_subdir)

        # Load the index file if it exists
        index_path = files.get_abs_path(db_dir, "knowledge_import.json")

        # make sure directory exists
        if not os.path.exists(db_dir):
            os.makedirs(db_dir)

        index: dict[str, knowledge_import.KnowledgeImport] = {}
        if os.path.exists(index_path):
            with open(index_path, "r") as f:
                index = json.load(f)

        # preload knowledge folders
        index = self._preload_knowledge_folders(log_item, kn_dirs, index)

        for file in index:
            if index[file]["state"] in ["changed", "removed"] and index[file].get(
                "ids", []
            ):  # for knowledge files that have been changed or removed and have IDs
                # remove original version, whoever imported it
                await self.db.adelete_knowledge(index[file]["ids"])
            if index[file]["state"] == "changed":
                index[file]["ids"] = await self.insert_documents(
                    index[file]["documents"]
                )  # insert new version

        # remove index where state="removed"
        index = {k: v for k, v in index.items() if v["state"] != "removed"}

        # strip state and documents from index and save it
        for file in index:
            if "documents" in index[file]:
                del index[file]["documents"]  # type: ignore
            if "state" in index[file]:
                del index[file]["state"]  # type: ignore
        with open(index_path, "w") as f:
            json.dump(index, f)

    def _preload_knowledge_folders(
        self,
        log_item: LogItem | None,
        kn_dirs: list[str],
        index: dict[str, knowledge_import.KnowledgeImport],
    ):
        # load knowledge folders, subfolders by area
        for kn_dir in kn_dirs:
            # everything in the root of the knowledge goes to main
            index = knowledge_import.load_knowledge(
                log_item,
                abs_knowledge_dir(kn_dir),
                index,
                {"area": Memory.Area.MAIN.value},
                filename_pattern="*",
                recursive=False,
            )
            # subdirectories go to their folders
            for area in Memory.Area:
                index = knowledge_import.load_knowledge(
                    log_item,
                    # files.get_abs_path("knowledge", kn_dir, area.value),
                    abs_knowledge_dir(kn_dir, area.value),
                    index,
                    {"area": area.value},
                    recursive=True,
                )

        return index

    def get_document_by_id(self, id: str) -> Document | None:
        return self.db.get_by_ids(id)[0]

    async def search_similarity_threshold(
        self, query: str, limit: int, threshold: float, filter: str = ""
    ):
        comparator = Memory._get_comparator(filter) if filter else None

        return await self.db.asearch(
            query,
            search_type="similarity_score_threshold",
            k=limit,
            score_threshold=threshold,
            filter=comparator,
        )

    async def search_similarity_threshold_with_scores(
        self, query: str, limit: int, threshold: float, filter: str = ""
    ) -> list[tuple[Document, float]]:
        comparator = Memory._get_comparator(filter) if filter else None

        return await self.db.asimilarity_search_with_relevance_scores(
            query,
            k=limit,
            score_threshold=threshold,
            filter=comparator,
        )

    async def delete_documents_by_query(
        self,
        query: str,
        threshold: float,
        filter: str = "",
        *,
        include_exact: bool = False,
        cascade: bool = False,
    ):
        k = 100
        tot = 0
        removed = []
        removed_ids: set[str] = set()

        while True:
            # Perform similarity search with score
            docs = await self.search_similarity_threshold(
                query, limit=k, threshold=threshold, filter=filter
            )
            document_ids = [result.metadata["id"] for result in docs]

            # Only what was actually deleted: a project memory someone else
            # wrote is refused by the policy, quietly, and must not be reported
            # as gone -- nor found again by the next round of this loop.
            deleted = set(await self.db.adelete(ids=document_ids)) if document_ids else set()
            removed += [d for d in docs if d.metadata["id"] in deleted]
            removed_ids.update(str(doc_id) for doc_id in deleted)
            tot += len(deleted)

            # If fewer than K document IDs, break the loop
            if len(document_ids) < k or len(deleted) < len(document_ids):
                break

        if include_exact:
            exact_docs = self._find_exact_query_docs(query, filter, removed_ids)
            if exact_docs:
                deleted = set(await self.db.adelete(ids=[doc.metadata["id"] for doc in exact_docs]))
                removed += [d for d in exact_docs if d.metadata["id"] in deleted]
                removed_ids.update(str(doc_id) for doc_id in deleted)
                tot += len(deleted)

        if cascade and removed_ids:
            related_docs = self._find_related_docs_by_ids(removed_ids)
            if related_docs:
                deleted = set(await self.db.adelete(ids=[doc.metadata["id"] for doc in related_docs]))
                removed += [d for d in related_docs if d.metadata["id"] in deleted]
                removed_ids.update(str(doc_id) for doc_id in deleted)
                tot += len(deleted)

        if tot:
            self._save_db()  # persist
        return removed

    async def delete_documents_by_ids(
        self, ids: list[str], *, cascade: bool = False, filter: str = "", unowned_too: bool = False
    ):
        """`unowned_too`: an administrator may also delete project memories with
        no recorded writer (from before migration 0025). The dashboard passes it;
        nothing automatic does."""
        # aget_by_ids is not yet implemented in faiss, need to do a workaround
        rem_docs = await self.db.aget_by_ids(
            ids
        )  # existing docs to remove (prevents error)
        rem_ids = [doc.metadata["id"] for doc in rem_docs]

        if cascade:
            related_docs = self._find_related_docs_by_ids(set(ids) | set(rem_ids))
            if related_docs:
                existing = {doc.metadata["id"] for doc in rem_docs}
                rem_docs.extend(
                    doc for doc in related_docs if doc.metadata["id"] not in existing
                )

        if rem_docs:
            rem_ids = [doc.metadata["id"] for doc in rem_docs]  # ids to remove
            # what was actually deleted; another writer's project memory is not
            deleted = set(await self.db.adelete(ids=rem_ids))
            if unowned_too:
                deleted |= set(await self.db.adelete_unowned([i for i in rem_ids if i not in deleted]))
            rem_docs = [doc for doc in rem_docs if doc.metadata["id"] in deleted]

        if rem_docs:
            self._save_db()  # persist
        return rem_docs

    async def insert_text(self, text, metadata: dict = {}):
        doc = Document(text, metadata=metadata)
        ids = await self.insert_documents([doc])
        return ids[0]

    async def insert_documents(self, docs: list[Document]):
        ids = [self._generate_doc_id() for _ in range(len(docs))]
        timestamp = self.get_timestamp()

        if ids:
            for doc, id in zip(docs, ids):
                doc.metadata["id"] = id  # add ids to documents metadata
                doc.metadata["timestamp"] = timestamp  # add timestamp
                if not doc.metadata.get("area", ""):
                    doc.metadata["area"] = Memory.Area.MAIN.value

            await self.db.aadd_documents(documents=docs, ids=ids)
            self._save_db()  # persist
        return ids

    async def update_documents(self, docs: list[Document], *, unowned_too: bool = False):
        """`unowned_too`: as in `delete_documents_by_ids`, for the dashboard."""
        ids = [doc.metadata["id"] for doc in docs]
        refused = set(ids) - self.db.writable_ids(ids)
        unowned = self.db.unowned_ids(list(refused)) if unowned_too and refused else set()
        if refused - unowned:
            raise ValueError("A project memory can be changed only by whoever wrote it.")
        if unowned:
            for doc in docs:
                doc.id = doc.metadata["id"]
            await self.db.aupdate_unowned([doc for doc in docs if doc.id in unowned])
        docs = [doc for doc in docs if doc.metadata["id"] not in unowned]
        if docs:
            own = [doc.metadata["id"] for doc in docs]
            await self.db.adelete(ids=own)  # delete originals
            await self.db.aadd_documents(documents=docs, ids=own)  # add updated
        self._save_db()  # persist
        return ids

    def _save_db(self):
        """Nothing to do: a write is a write.

        Kept as a call so the call sites still read as "persist here", and so a
        future store that does need a flush has somewhere to put it. The file
        version wrote the whole index out after every change, which is why those
        call sites exist at all.
        """

    def _generate_doc_id(self):
        while True:
            doc_id = guids.generate_id(10)  # random ID
            if not self.db.get_by_ids(doc_id):  # check if exists
                return doc_id

    def _find_exact_query_docs(
        self, query: str, filter: str, skip_ids: set[str]
    ) -> list[Document]:
        needle = _normalize_memory_match_text(query)
        if len(needle) < 3:
            return []

        docs: list[Document] = []
        comparator = Memory._get_comparator(filter) if filter else None
        for doc in self.db.get_all_docs().values():
            doc_id = str(doc.metadata.get("id", ""))
            if not doc_id or doc_id in skip_ids:
                continue
            if comparator and not comparator(doc.metadata):
                continue
            haystack = _normalize_memory_match_text(
                f"{doc.page_content}\n{json.dumps(doc.metadata, sort_keys=True, default=str)}"
            )
            if needle in haystack:
                docs.append(doc)
        return docs

    def _find_related_docs_by_ids(
        self, ids: set[str], filter: str = ""
    ) -> list[Document]:
        ids = {str(doc_id) for doc_id in ids if str(doc_id)}
        if not ids:
            return []

        docs: list[Document] = []
        comparator = Memory._get_comparator(filter) if filter else None
        for doc in self.db.get_all_docs().values():
            doc_id = str(doc.metadata.get("id", ""))
            if not doc_id or doc_id in ids:
                continue
            if comparator and not comparator(doc.metadata):
                continue
            if _metadata_references_any(doc.metadata, ids):
                docs.append(doc)
        return docs

    @staticmethod
    def _get_comparator(condition: str):
        _FILTER_SAFE = re.compile(
            r"^[a-zA-Z0-9_\-\.\ \t'\"=<>!()\[\],:\+]+$"
        )
        if len(condition) > 512 or not _FILTER_SAFE.match(condition):
            PrintStyle.error(
                f"Memory filter rejected (unsafe characters or too long): {condition!r}"
            )
            return lambda _data: False

        # No attribute access, which is what a method call starts with. The
        # filter is the request's or the model's, it runs once per stored row,
        # and "'x'.rjust(3000000000)" passes the character check above -- a few
        # gigabytes per row, in the process every account shares.
        evaluator = SimpleEval(functions={})
        evaluator.nodes.pop(ast.Attribute, None)

        def comparator(data: dict[str, Any]):
            try:
                evaluator.names = data
                result = evaluator.eval(condition)
                return result
            except Exception as e:
                PrintStyle.error(f"Error evaluating condition: {e}")
                return False

        return comparator

    @staticmethod
    def _score_normalizer(val: float) -> float:
        res = 1 - 1 / (1 + np.exp(val))
        return res

    @staticmethod
    def _cosine_normalizer(val: float) -> float:
        res = (1 + val) / 2
        res = max(
            0, min(1, res)
        )  # float precision can cause values like 1.0000000596046448
        return res

    @staticmethod
    def format_docs_plain(docs: list[Document]) -> list[str]:
        result = []
        for doc in docs:
            text = ""
            for k, v in doc.metadata.items():
                text += f"{k}: {v}\n"
            text += f"Content: {doc.page_content}"
            result.append(text)
        return result

    @staticmethod
    def get_timestamp():
        return Localization.get().now_iso(timespec="seconds")


def get_custom_knowledge_subdir_abs(agent: Agent) -> str:
    for dir in agent.config.knowledge_subdirs:
        if dir != "default":
            if dir == "custom":
                return files.get_abs_path("usr/knowledge")
            return files.get_abs_path("usr/knowledge", dir)
    raise Exception("No custom knowledge subdir set")


def reload():
    # clear the memory index, this will force all DBs to reload
    Memory.index = {}


def _normalize_memory_match_text(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _metadata_references_any(value: Any, ids: set[str]) -> bool:
    if isinstance(value, dict):
        return any(_metadata_references_any(item, ids) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_metadata_references_any(item, ids) for item in value)
    text = str(value or "").strip()
    if not text:
        return False
    if text in ids:
        return True
    return any(doc_id in text.split(",") for doc_id in ids)


def memory_scope(memory_subdir: str) -> tuple[str, str | None]:
    """Whether an area is a project's or a person's, and whose.

    The same split `abs_db_dir` made when this was directories, stated once so
    the table's policy and the store agree on it.
    """
    if memory_subdir.startswith("projects/"):
        return "project", None

    from helpers import user_scope

    return "user", user_scope.get_current_user_id()


def abs_db_dir(memory_subdir: str) -> str:
    # patch for projects, this way we don't need to re-work the structure of memory subdirs
    if memory_subdir.startswith("projects/"):
        # NOTE: projects are a shared workspace, so project memory is shared by
        # everyone with access to the project. Making projects per-user is a
        # separate design decision, not an oversight here.
        from helpers.projects import get_project_meta

        return files.get_abs_path(get_project_meta(memory_subdir[9:]), "memory")

    # Standard memory is private. A vector store holds the text of what a user
    # told the agent, so a shared index would surface one person's conversations
    # in another person's recall.
    from helpers import user_scope
    from helpers.projects import validate_project_name

    try:
        # One path segment. The area name is the request's, and "../../x" made
        # this a directory anywhere the application can write.
        validate_project_name(memory_subdir)
    except ValueError:
        raise ValueError(f"Invalid memory area: {memory_subdir!r}") from None
    return user_scope.get_user_path(user_scope.MEMORY_DIR, memory_subdir)


def abs_knowledge_dir(knowledge_subdir: str, *sub_dirs: str) -> str:
    # patch for projects, this way we don't need to re-work the structure of knowledge subdirs
    if knowledge_subdir.startswith("projects/"):
        from helpers.projects import get_project_meta

        return files.get_abs_path(
            get_project_meta(knowledge_subdir[9:]), "knowledge", *sub_dirs
        )
    # standard subdirs
    if knowledge_subdir == "default":
        return files.get_abs_path("knowledge", *sub_dirs)
    if knowledge_subdir == "custom":
        return files.get_abs_path("usr/knowledge", *sub_dirs)
    return files.get_abs_path("usr/knowledge", knowledge_subdir, *sub_dirs)


def get_memory_subdir_abs(agent: Agent) -> str:
    subdir = get_agent_memory_subdir(agent)
    return abs_db_dir(subdir)


def get_user_memory_subdir_abs(agent: Agent) -> str:
    """The owner's own memory folder, also inside a project.

    Inside a project `get_memory_subdir_abs` is the project's shared folder.
    That is right for memories the project shares and wrong for anything put in
    front of every member's system prompt, such as behaviour rules: one member's
    agent would be writing the others' instructions.
    """
    config = plugins.get_plugin_config("_memory", agent) or {}
    subdir = config.get("agent_memory_subdir", "") or "default"
    if subdir.startswith("projects/"):
        subdir = "default"
    return abs_db_dir(subdir)


def get_agent_memory_subdir(agent: Agent) -> str:
    config = plugins.get_plugin_config("_memory", agent)

    if not config:
        return "default"
    
    # Check if project isolation is enabled and we are in a project
    if config.get("project_memory_isolation", True):
        project_name = projects.get_context_project_name(agent.context)
        if project_name:
            return "projects/" + project_name

    # Fallback to configured subdir or default
    return config.get("agent_memory_subdir", "") or "default"


def get_context_memory_subdir(context: AgentContext) -> str:
    agent = context.get_agent()
    return get_agent_memory_subdir(agent)


def get_existing_memory_subdirs() -> list[str]:
    """Areas that have memories for the caller, from the database.

    The dashboard used to look for `memory/index.faiss` under each project.
    Nothing writes that file since memories moved to PostgreSQL, so a project
    with rows never appeared. Row-level security already limits the query to
    this account's areas and shared project areas.
    """
    try:
        from helpers import db, user_scope

        subdirs: list[str] = []
        owner = user_scope.get_current_user_id()
        if user_scope.is_safe_user_id(owner):
            with db.transaction(owner) as conn:
                rows = conn.execute(
                    "SELECT DISTINCT area FROM memories ORDER BY area"
                ).fetchall()
            subdirs = [str(row[0]) for row in rows if row and row[0]]

        # Ensure 'default' is always available
        if "default" not in subdirs:
            subdirs.insert(0, "default")

        return subdirs
    except Exception as e:
        PrintStyle.error(f"Failed to get memory subdirectories: {str(e)}")
        return ["default"]


def get_knowledge_subdirs_by_memory_subdir(
    memory_subdir: str, default: list[str]
) -> list[str]:
    if memory_subdir.startswith("projects/"):
        from helpers.projects import get_project_meta

        default.append(get_project_meta(memory_subdir[9:], "knowledge"))
    return default
