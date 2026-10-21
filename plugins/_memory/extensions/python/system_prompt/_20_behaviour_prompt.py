from helpers.extension import Extension
from agent import Agent, LoopData
from helpers import files
from helpers.print_style import PrintStyle

# Direct import - this extension lives inside the memory plugin
from plugins._memory.helpers import memory


class BehaviourPrompt(Extension):

    async def execute(self, system_prompt: list[str]=[], loop_data: LoopData = LoopData(), **kwargs):
        if not self.agent:
            return

        prompt = read_rules(self.agent)
        system_prompt.insert(0, prompt)


def get_custom_rules_file(agent: Agent) -> str | None:
    """Path to this user's custom behaviour rules, or None if there is no owner.

    Behaviour rules live in the user's private memory directory, which cannot be
    resolved outside a user context. Returning None lets prompt rendering fall
    back to the defaults instead of failing: no owner simply means no custom
    rules, and degrading here is far better than an unrenderable system prompt.

    Inside a project too: the rules are the person's, never the project's.
    """
    from helpers import user_scope

    _warn_of_project_rules(agent)
    try:
        return files.get_abs_path(memory.get_user_memory_subdir_abs(agent), "behaviour.md")
    except user_scope.UserScopeError:
        return None


_warned: set[str] = set()


def _warn_of_project_rules(agent: Agent) -> None:
    """Say once that a project's old `behaviour.md` is no longer read.

    Any member's agent could write that file, and it went in front of every
    member's prompt. Nothing proves who wrote one that exists, so it is not
    used; rules meant for a whole project are its instructions, which only an
    administrator changes.
    """
    try:
        subdir = memory.get_agent_memory_subdir(agent)
        if not subdir.startswith("projects/"):
            return
        legacy = files.get_abs_path(memory.abs_db_dir(subdir), "behaviour.md")
    except Exception:
        return
    if legacy not in _warned and files.exists(legacy):
        _warned.add(legacy)
        PrintStyle.warning(
            f"Ignoring {legacy}: behaviour rules are per person. "
            "Put rules for the whole project in its instructions."
        )


def read_rules(agent: Agent):
    rules_file = get_custom_rules_file(agent)
    if rules_file and files.exists(rules_file):
        rules = files.read_file(rules_file)
        return agent.read_prompt("agent.system.behaviour.md", rules=rules)
    else:
        rules = agent.read_prompt("agent.system.behaviour_default.md")
        return agent.read_prompt("agent.system.behaviour.md", rules=rules)
