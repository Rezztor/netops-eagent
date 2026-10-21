import json
import os
import tempfile
import uuid
from collections import OrderedDict
from datetime import datetime
from typing import Any

from agent import Agent, AgentConfig, AgentContext, AgentContextType, ConversationSummary
from helpers import files, history, subagents, user_scope
from helpers.litellm_transport import delete_stored_response_ids
from helpers.localization import Localization
from initialize import initialize_agent

from helpers.log import Log, LogItem
from helpers.print_style import PrintStyle

# Conversations are rows now, protected by a row-level policy rather than by the
# directory they sat in. What is still a folder is the *attachments* a
# conversation carries -- those are files and stay files -- so `chats_folder`
# survives while everything that read or wrote a chat.json has gone.
CHATS_SUBFOLDER = user_scope.CHATS_DIR
LOG_SIZE = 1000
SAVED_CHAT_CONTEXT_DATA_KEY = "_persist_chat_saved"


def _fallback_datetime_iso() -> str:
    return datetime.fromtimestamp(0, tz=Localization.get().get_tzinfo()).isoformat()


def _parse_persisted_datetime(value: str | None) -> datetime:
    raw_value = value or _fallback_datetime_iso()
    dt = datetime.fromisoformat(raw_value)
    if dt.tzinfo is None:
        dt = Localization.get().localize_naive_datetime(dt)
    return dt


def chats_folder(user_id: str | None = None) -> str:
    """Absolute path to one user's chats folder."""
    return user_scope.get_user_path(CHATS_SUBFOLDER, user_id=user_id)


def get_chat_folder_path(ctxid: str, user_id: str | None = None):
    """
    Get the folder path for any context (chat or task).

    Args:
        ctxid: The context ID
        user_id: Owner. Defaults to the current user scope.

    Returns:
        The absolute path to the context folder
    """
    # One path segment, and nothing else. The id comes from the request, and an
    # id nobody owns passes the ownership check -- so `chat_remove` with
    # "../.." deleted the directory above every account's conversations, and an
    # absolute one deleted that path itself.
    if (
        not isinstance(ctxid, str)
        or ctxid in ("", ".", "..")
        or os.path.basename(ctxid) != ctxid
        or "\\" in ctxid
    ):
        raise ValueError("Invalid context id")
    return files.get_abs_path(chats_folder(user_id), ctxid)


def get_chat_msg_files_folder(ctxid: str, user_id: str | None = None):
    return files.get_abs_path(get_chat_folder_path(ctxid, user_id), "messages")

def save_tmp_chat(context: AgentContext):
    """Write one conversation.

    Scoped to the conversation's *own* owner rather than to whoever is running.
    The framework saves on behalf of people all the time -- the job loop, a
    shutdown flush, a backup -- and scoping to the caller would mean the policy
    rejecting a write the caller is legitimately making.
    """
    # Skip saving BACKGROUND contexts as they should be ephemeral
    if context.type == AgentContextType.BACKGROUND:
        return

    if not user_scope.is_safe_user_id(context.owner_id):
        # Nothing to own it, so nowhere to put it. Ephemeral by consequence.
        return

    from psycopg.types.json import Jsonb

    from helpers import db

    data = _serialize_context(context)
    summary = context.summary()
    with db.transaction(context.owner_id) as conn:
        conn.execute(
            """
            INSERT INTO conversations (id, owner_id, name, type, created_at,
                                       last_message, agent_profile, log_guid,
                                       log_length, output_data, document, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                type = EXCLUDED.type,
                last_message = EXCLUDED.last_message,
                agent_profile = EXCLUDED.agent_profile,
                log_guid = EXCLUDED.log_guid,
                log_length = EXCLUDED.log_length,
                output_data = EXCLUDED.output_data,
                document = EXCLUDED.document,
                updated_at = now()
            """,
            (
                context.id, context.owner_id, context.name, context.type.value,
                context.created_at, context.last_message, summary.agent_profile,
                summary.log_guid, summary.log_length,
                Jsonb(summary.output_data), Jsonb(json.loads(_safe_json_serialize(data))),
            ),
        )
    mark_chat_saved(context)

    # Refresh the index from the object that was just written, so an evicted
    # conversation is listed as it looked when it left memory rather than when
    # it was created. Guarded because saving accepts anything context-shaped --
    # several tests pass a stand-in -- and refreshing an index is a side effect
    # of saving, not a new requirement on what may be saved.
    summarise = getattr(context, "summary", None)
    if callable(summarise):
        AgentContext.remember(summarise())


def save_tmp_chats():
    """Save every *loaded* context.

    `AgentContext.all()` returns what is resident, which is exactly right here:
    a conversation that is not loaded has not been changed, and its file on disk
    is already what saving it would write.
    """
    for context in AgentContext.all():
        # Skip BACKGROUND contexts as they should be ephemeral
        if context.type == AgentContextType.BACKGROUND:
            continue
        save_tmp_chat(context)


def load_tmp_chats():
    """Index every user's conversations. Do not build them.

    This used to deserialise every conversation of every user into memory before
    the server would accept a request -- 16 ms and 0.45 MB each, on the blocking
    boot path, with no eviction. Measured, that is a hard ceiling at roughly
    2,300 conversations inside the 2Gi a modest volume allows, reached after a
    forty-second boot (`docs/internal/storage-plan.md`).

    Parsing is 6% of that cost and building the context object is the other 94%,
    so indexing still reads and parses each file and simply does not build
    anything. What it returns is the same list of ids it always did; what it no
    longer returns is a process holding all of them.

    A conversation is built on first use, by `AgentContext.get()`.
    """
    from helpers import db

    install_context_hooks()

    # Unscoped, because the index is instance-wide: the process holds one
    # registry and filters it per user in memory. That is the second of ADR
    # 0003's two boundaries -- the policy on the table is the first -- and it is
    # why `index_for_user` still filters rather than trusting the query.
    with db.unscoped_transaction() as conn:
        rows = conn.execute(
            """
            SELECT id, owner_id, name, type, created_at, last_message,
                   agent_profile, log_guid, log_length, output_data
              FROM conversations
             ORDER BY last_message DESC
            """
        ).fetchall()

    ctxids = []
    for number, row in enumerate(rows, start=1):
        (ctxid, owner_id, name, kind, created_at, last_message,
         agent_profile, log_guid, log_length, output_data) = row
        AgentContext.remember(
            ConversationSummary(
                id=ctxid,
                owner_id=owner_id,
                name=name,
                created_at=created_at,
                last_message=last_message,
                type=AgentContextType(kind),
                no=number,
                log_guid=log_guid or "",
                log_length=int(log_length or 0),
                paused=False,
                agent_profile=agent_profile or "",
                output_data=dict(output_data or {}),
            )
        )
        ctxids.append(ctxid)
    return ctxids




def hydrate_chat(ctxid: str, owner_id: str | None) -> AgentContext | None:
    """Build one conversation from disk. The other half of lazy loading.

    Returns None when the file has gone, which `AgentContext.get()` treats as
    "drop it from the index" rather than as an error -- a conversation deleted
    from under us should stop being listed, not fail to load forever.
    """
    from helpers import db

    with db.transaction(owner_id) as conn:
        row = conn.execute(
            "SELECT document FROM conversations WHERE id = %s", (ctxid,)
        ).fetchone()
    if row is None:
        return None
    context = _deserialize_context(row[0], owner_id=owner_id)
    mark_chat_saved(context)
    return context


def install_context_hooks() -> None:
    """Tell AgentContext how to load and save.

    Hooks rather than imports: this module already imports agent, so agent
    cannot import it back.
    """
    AgentContext._hydrate = hydrate_chat
    AgentContext._persist = save_tmp_chat






def mark_chat_saved(context: AgentContext) -> None:
    context.data[SAVED_CHAT_CONTEXT_DATA_KEY] = True


def saved_chat_ids(user_id: str | None = None) -> set[str]:
    """Ids that are stored. Defaults to the current user."""
    from helpers import db

    user_id = user_id or user_scope.get_current_user_id()
    if not user_scope.is_safe_user_id(user_id):
        return set()
    with db.transaction(user_id) as conn:
        return {
            row[0] for row in conn.execute("SELECT id FROM conversations").fetchall()
        }






def _keep_what_an_export_holds(data: dict, may_set_connection: bool) -> None:
    """Reduce an imported chat to what `export_json_chat` could have written.

    The document was typed by whoever imports it. Keys beginning with `_` are
    the application's own and never exported: `_parallel_parent_context_id`
    names the conversation that approval cards and downloads are shown in, so
    an imported one pointed them at somebody else's chat. A free-form model
    override carries `api_base`, where the next turn goes with the instance's
    provider key; storing one takes `settings.manage` in `model_override`, so
    it does here. A chosen preset is every account's, and is kept by name only.
    """
    holders = [data, *(a for a in data.get("agents") or [] if isinstance(a, dict))]
    for holder in holders:
        for field in ("data", "output_data"):
            kept = holder.get(field)
            holder[field] = (
                {k: v for k, v in kept.items() if not str(k).startswith("_")}
                if isinstance(kept, dict)
                else {}
            )
    override = data["data"].get("chat_model_override")
    if isinstance(override, dict) and override.get("preset_name"):
        data["data"]["chat_model_override"] = {"preset_name": str(override["preset_name"])}
    elif override is not None and not may_set_connection:
        del data["data"]["chat_model_override"]


def load_json_chats(jsons: list[str], may_set_connection: bool = False):
    """Load contexts from JSON strings a user supplied (`/api/chat_load`).

    `may_set_connection` says the caller holds `settings.manage`.
    """
    ctxids = []
    for js in jsons:
        data = json.loads(js)
        if "id" in data:
            del data["id"]  # remove id to get new
        _keep_what_an_export_holds(data, may_set_connection)
        ctx = _deserialize_context(data)
        ctxids.append(ctx.id)
    return ctxids


def export_json_chat(context: AgentContext):
    """Export context as JSON string"""
    data = _serialize_context(context)
    js = _safe_json_serialize(data, ensure_ascii=False)
    return js


def remove_chat(ctxid):
    """Remove a chat or task context"""
    if not isinstance(ctxid, str) or not ctxid.strip():
        raise ValueError("remove_chat: context id must not be empty")
    from helpers import db

    _delete_provider_responses_for_chat(ctxid)
    # The message files stay on the volume -- they are attachments, not state --
    # so the directory goes too.
    files.delete_dir(get_chat_folder_path(ctxid))
    with db.transaction(None) as conn:
        conn.execute("DELETE FROM conversations WHERE id = %s", (ctxid,))


def remove_msg_files(ctxid):
    """Remove all message files for a chat or task context"""
    if not isinstance(ctxid, str) or not ctxid.strip():
        raise ValueError("remove_msg_files: context id must not be empty")
    path = get_chat_msg_files_folder(ctxid)
    files.delete_dir(path)


def _serialize_context(context: AgentContext):
    profile = str(
        getattr(context.root_agent.config, "profile", None)
        or getattr(context.config, "profile", None)
        or ""
    )

    # serialize agents
    agents = []
    agent = context.root_agent
    while agent:
        agents.append(_serialize_agent(agent))
        agent = agent.data.get(Agent.DATA_NAME_SUBORDINATE, None)


    data = {k: v for k, v in context.data.items() if not k.startswith("_")}
    output_data = {k: v for k, v in context.output_data.items() if not k.startswith("_")}

    return {
        "id": context.id,
        "name": context.name,
        "created_at": (
            Localization.get().serialize_datetime(context.created_at)
            if context.created_at
            else _fallback_datetime_iso()
        ),
        "type": context.type.value,
        "last_message": (
            Localization.get().serialize_datetime(context.last_message)
            if context.last_message
            else _fallback_datetime_iso()
        ),
        "agents": agents,
        "streaming_agent": (
            context.streaming_agent.number if context.streaming_agent else 0
        ),
        "agent_profile": profile,
        "log": _serialize_log(context.log),
        "data": data,
        "output_data": output_data,
    }


def _serialize_agent(agent: Agent):
    data = {k: v for k, v in agent.data.items() if not k.startswith("_")}

    history = agent.history.serialize()

    return {
        "number": agent.number,
        "agent_profile": str(getattr(agent.config, "profile", "") or ""),
        "data": data,
        "history": history,
    }


def _serialize_log(log: Log):
    # Guard against concurrent log mutations while serializing.
    with log._lock:
        logs = [item.output() for item in log.logs[-LOG_SIZE:]]  # serialize LogItem objects
        guid = log.guid
        progress = log.progress
        progress_no = log.progress_no
    return {
        "guid": guid,
        "logs": logs,
        "progress": progress,
        "progress_no": progress_no,
    }


def _deserialize_context(data, owner_id: str | None = None):
    # The document may be one a user handed to /api/chat_load.
    profile = subagents.safe_profile_name(data.get("agent_profile"))
    override_settings = {"agent_profile": profile} if profile else None
    config = initialize_agent(override_settings=override_settings)
    log = _deserialize_log(data.get("log", None))

    context = AgentContext(
        config=config,
        id=data.get("id", None),  # get new id
        name=data.get("name", None),
        created_at=(
            _parse_persisted_datetime(data.get("created_at"))
        ),
        type=AgentContextType(data.get("type", AgentContextType.USER.value)),
        last_message=(
            _parse_persisted_datetime(data.get("last_message"))
        ),
        log=log,
        paused=False,
        data=data.get("data", {}),
        output_data=data.get("output_data", {}),
        owner_id=owner_id,
        # root_agent=root_agent,
        # streaming_agent=straming_agent,
    )

    agents = data.get("agents", [])
    root_agent = _deserialize_agents(agents, config, context)
    streaming_agent = root_agent
    while streaming_agent and streaming_agent.number != data.get("streaming_agent", 0):
        streaming_agent = streaming_agent.data.get(Agent.DATA_NAME_SUBORDINATE, None)

    context.root_agent = root_agent
    context.config = root_agent.config
    context.streaming_agent = streaming_agent

    return context


def _deserialize_agent_config(
    agent_data: dict[str, Any], fallback_config: AgentConfig
) -> AgentConfig:
    fallback_profile = str(getattr(fallback_config, "profile", "") or "")
    profile = subagents.safe_profile_name(str(agent_data.get("agent_profile") or "").strip()) or fallback_profile
    if profile == fallback_profile:
        return fallback_config
    override_settings = {"agent_profile": profile} if profile else None
    return initialize_agent(override_settings=override_settings)


def _deserialize_agents(
    agents: list[dict[str, Any]], config: AgentConfig, context: AgentContext
) -> Agent:
    prev: Agent | None = None
    zero: Agent | None = None

    for ag in agents:
        current = Agent(
            number=ag["number"],
            config=_deserialize_agent_config(ag, config),
            context=context,
        )
        current.data = ag.get("data", {})
        current.history = history.deserialize_history(
            ag.get("history", ""), agent=current
        )
        if not zero:
            zero = current

        if prev:
            prev.set_data(Agent.DATA_NAME_SUBORDINATE, current)
            current.set_data(Agent.DATA_NAME_SUPERIOR, prev)
        prev = current

    return zero or Agent(0, config, context)


# def _deserialize_history(history: list[dict[str, Any]]):
#     result = []
#     for hist in history:
#         content = hist.get("content", "")
#         msg = (
#             HumanMessage(content=content)
#             if hist.get("type") == "human"
#             else AIMessage(content=content)
#         )
#         result.append(msg)
#     return result


def _deserialize_log(data: dict[str, Any]) -> "Log":
    log = Log()
    log.guid = data.get("guid", str(uuid.uuid4()))
    log.set_initial_progress()

    # Deserialize the list of LogItem objects
    i = 0
    for item_data in data.get("logs", []):
        agentno = item_data.get("agentno")
        if agentno is None:
            agentno = item_data.get("agent_number", 0)
        log.logs.append(
            LogItem(
                log=log,  # restore the log reference
                no=i,  # item_data["no"],
                type=item_data["type"],
                heading=item_data.get("heading", ""),
                content=item_data.get("content", ""),
                kvps=OrderedDict(item_data["kvps"]) if item_data["kvps"] else None,
                timestamp=item_data.get("timestamp", 0.0),
                agentno=agentno,
                id=item_data.get("id"),
            )
        )
        log.updates.append(i)
        i += 1

    return log


def _safe_json_serialize(obj, **kwargs):
    def serializer(o):
        if isinstance(o, dict):
            return {k: v for k, v in o.items() if is_json_serializable(v)}
        elif isinstance(o, (list, tuple)):
            return [item for item in o if is_json_serializable(item)]
        elif is_json_serializable(o):
            return o
        else:
            return None  # Skip this property

    def is_json_serializable(item):
        try:
            json.dumps(item)
            return True
        except (TypeError, OverflowError):
            return False

    return json.dumps(obj, default=serializer, **kwargs)


def _delete_provider_responses_for_chat(ctxid: str) -> None:
    from helpers import db

    try:
        with db.transaction(None) as conn:
            row = conn.execute(
                "SELECT document FROM conversations WHERE id = %s", (ctxid,)
            ).fetchone()
        if row is None:
            return
        data = row[0]
    except Exception:
        return
    if _responses_delete_disabled(data):
        return
    response_ids = _collect_response_ids(data)
    if not response_ids:
        return
    delete_stored_response_ids(response_ids)


def _responses_delete_disabled(data: dict[str, Any]) -> bool:
    if data.get("responses_delete_on_chat_delete") is False:
        return True
    context_data = data.get("data")
    if isinstance(context_data, dict) and context_data.get("responses_delete_on_chat_delete") is False:
        return True
    for agent_data in data.get("agents", []) or []:
        if not isinstance(agent_data, dict):
            continue
        state = agent_data.get("data")
        if isinstance(state, dict) and state.get("responses_delete_on_chat_delete") is False:
            return True
    return False


def _collect_response_ids(data: Any) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        response_id = str(value or "").strip()
        if response_id and response_id not in seen:
            seen.add(response_id)
            found.append(response_id)

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            state = obj.get(Agent.DATA_NAME_RESPONSES_STATE)
            if isinstance(state, dict):
                add(state.get("response_id"))
                for response_id in state.get("response_ids") or []:
                    add(response_id)

            metadata = obj.get("metadata")
            if isinstance(metadata, dict):
                responses = metadata.get("responses")
                if isinstance(responses, dict):
                    add(responses.get("response_id"))

            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)
        elif isinstance(obj, str) and '"response_id"' in obj:
            try:
                walk(json.loads(obj))
            except Exception:
                return

    walk(data)
    return found
