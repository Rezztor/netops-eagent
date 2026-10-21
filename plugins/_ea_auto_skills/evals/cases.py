"""Realistic turn transcripts, and what a good reviewer should do with each.

Half should produce a skill and half should not. Tuning against only the first
half would trade a reviewer that never proposes for one that always does.
"""

PROPOSE = "propose"
SKIP = "skip"

CASES = [
    # ---------- should produce a skill ----------
    dict(
        # Mislabelled on the first pass: eagent-debug-plugin:48-53 already covers
        # checking scoped toggles, and :174 covers how they evaluate. The
        # reviewer skipping this was correct, and the expectation was the error.
        id="plugin-scope-diagnosis",
        expect=SKIP,
        why="Already covered by eagent-debug-plugin.",
        loaded=[],
        evidence="""user: A plugin shows as enabled in the Plugins list but its tool is missing for one agent profile. Why?

ai: Reading helpers/plugins.py.

tool text_editor: read helpers/plugins.py
result: get_toggle_state() reads only the root plugin paths. get_enabled_plugins() also walks agent- and project-scoped paths and calls determined_toggle_from_paths(True, reversed(plugin_paths)), so a lower-priority .toggle-0 can be overridden by a higher-priority .toggle-1, and vice versa.

tool text_editor: read helpers/plugins.py
result: find_plugin_assets searches project/agents/<profile>, project, usr/agents/<profile>, plugin agents/<profile>, agents/<profile>, usr/, plugins/ in that order.

ai: The Plugins list shows the global state only. A profile-scoped .toggle-0 under usr/agents/<profile>/plugins/<name>/ disables it for that profile while the list still says enabled. Found one at usr/agents/researcher/plugins/_browser/.toggle-0.

response: The list shows global activation. Scoped overrides live in toggle files under the profile's own plugin directory and win over the global setting. To diagnose: find usr/agents/<profile>/plugins/<name>/.toggle-* and the project equivalent, then read them in priority order.""",
    ),
    dict(
        id="suite-only-failure",
        expect=PROPOSE,
        why="A debugging method that generalises well beyond this one failure.",
        loaded=[],
        evidence="""user: Four tests pass on their own and fail in the full suite. I cannot work out why.

tool terminal: pytest path/to/file.py -q
result: 12 passed

tool terminal: pytest -q
result: 4 failed, 1266 passed

ai: Passing alone and failing together means shared state, not a bad assertion. Bisecting by running the file against progressively larger subsets.

tool terminal: pytest tests other/file.py -q
result: all passed

tool terminal: pytest plugins other/file.py -q
result: 4 failed

ai: Narrowed it to one directory, then one file, then found it: the earlier test cleared a module-level cache. With the cache warm the code path never ran; cold, it raised, and the error was swallowed and reported as an unrelated refusal.

response: It was cache-warming order. The fix was to make the fixture build the state the code expects rather than depend on a previous test having populated a cache.""",
    ),
    dict(
        id="release-checklist",
        expect=PROPOSE,
        why="A multi-step procedure with a step that is easy to miss.",
        loaded=[],
        evidence="""user: Add a new HTTP endpoint that only administrators can call.

tool text_editor: read api/AGENTS.md
result: Handlers derive from helpers.api.ApiHandler and implement async def process. Every direct *.py endpoint must have a matching *.py.dox.md in the same directory.

tool text_editor: read helpers/api_policy.py
result: Central table keyed by dispatch path. A handler's own required_permission() wins if declared, otherwise this table is consulted. Comment: the table once held only core endpoint names, which left every plugin-owned endpoint ungated.

tool text_editor: write api/thing.py
result: written

ai: The endpoint answered but any signed-in account could call it. Adding it to the api_policy table fixed that.

response: Four steps: write the handler, add the sibling .dox.md, add the dispatch path to the permission table in helpers/api_policy.py, and add a test asserting the role that must not reach it. The third is the one that gets missed, because the endpoint works without it -- it is just ungated.""",
    ),

    dict(
        # The case that actually discriminates. Adjacent to a listed skill, but
        # the insight is genuinely absent from it, so the right answer is to
        # update that skill rather than to skip or to mint a rival.
        id="adjacent-but-missing",
        expect=PROPOSE,
        why="build-skill says nothing about shadowing between roots.",
        loaded=["build-skill"],
        evidence="""user: I added a skill under my own user directory with the same name as one in usr/skills and now the shared one never loads. Is that a bug?

tool skills_tool: load build-skill
result: Skills are folders containing SKILL.md with name and description frontmatter. Use lowercase hyphenated names. Decide whether the skill belongs in core skills/ or a plugin's skills/ directory.

tool text_editor: read helpers/skills.py
result: get_skill_roots returns the personal root first, then project, usr/skills, plugin roots and bundled skills. list_skills dedupes by normalised name and keeps the first occurrence, so the earliest root wins.

ai: Not a bug. Roots are ordered and deduplication keeps the first match, so a skill in a higher-priority root shadows one of the same name lower down. build-skill covers where to put a skill but says nothing about what happens when two roots hold the same name.

response: It is shadowing, and it is deliberate. Roots resolve in priority order and list_skills keeps the first occurrence of each normalised name, so your personal copy wins over the shared one. To make the shared one load again, rename or remove the personal copy.""",
    ),

    # ---------- should not ----------
    dict(
        id="file-lookup",
        expect=SKIP,
        why="Pure lookup. Nothing recurs.",
        loaded=[],
        evidence="""user: What version is set in plugins/_skills/plugin.yaml?

tool text_editor: read plugins/_skills/plugin.yaml
result: name: _skills\ntitle: Skills\nversion: 1.0.0

response: Version 1.0.0.""",
    ),
    dict(
        id="typo-fix",
        expect=SKIP,
        why="A one-off edit with no method behind it.",
        loaded=[],
        evidence="""user: There is a typo in the README, 'recieve' should be 'receive'.

tool text_editor: read README.md
result: ... you will recieve a notification ...

tool text_editor: patch README.md
result: applied

response: Fixed.""",
    ),
    dict(
        id="already-covered",
        expect=SKIP,
        why="The listed skill already covers this exact ground.",
        loaded=["build-skill"],
        evidence="""user: How do I write a new skill for this project?

tool skills_tool: load build-skill
result: Skills are folders containing SKILL.md with name and description frontmatter, optional scripts/, references/ and assets/. Use lowercase hyphenated names. Keep the body focused on procedure.

response: Create a folder named for the skill with a SKILL.md inside, frontmatter carrying name and description, and keep the body to the procedure. Put long material in references/.""",
    ),
]
