"""The embedding model has to be in the image, not on huggingface.co.

Memory, document search and the vector store all go through one small
sentence-transformers model. The build already downloads it -- install_eagent.sh
runs preload.py for exactly that reason -- but it is a two-stage build, and
whatever the builder wrote outside /eagent and the virtualenv never reached the
delivered image. An appliance in an air-gapped rack therefore reached for
huggingface.co on every start, got nothing, and ran on without embeddings.
"""

from __future__ import annotations

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(*parts: str) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def stages() -> tuple[str, str]:
    """The Dockerfile's builder and runtime halves, without their comments --
    the file has to be free to explain a rule without the prose being read as
    the rule. Only the second half ships."""
    body = "\n".join(
        line for line in read("Dockerfile").splitlines() if not line.lstrip().startswith("#")
    )
    froms = [m.start() for m in re.finditer(r"(?m)^FROM ", body)]
    assert len(froms) == 2, "the Dockerfile is no longer two stages"
    return body[froms[0] : froms[1]], body[froms[1] :]


def model_home() -> str:
    """The directory both stages agree the model cache lives in."""
    builder, _ = stages()
    match = re.search(r"(?m)^ENV HF_HOME=(\S+)", builder)
    assert match, "the builder does not say where the model cache goes"
    return match.group(1)


def test_the_build_downloads_the_model_somewhere_the_runtime_stage_can_take_it():
    """Left to itself, the download lands in the builder's ~/.cache and dies
    with the stage. It has to go somewhere named, before the preload runs."""
    builder, _ = stages()
    home = model_home()
    assert f"SENTENCE_TRANSFORMERS_HOME={home}" in builder, "sentence-transformers uses its own"
    assert builder.index("HF_HOME=") < builder.index("install_eagent.sh"), \
        "the model cache is named after the preload has already run"
    assert not home.startswith(("/root", "/home")), \
        "a per-user cache is not readable by the uid the container runs as"


def test_the_delivered_image_carries_the_model_and_looks_for_it_there():
    _, runtime = stages()
    home = model_home()
    assert re.search(rf"(?m)^COPY --from=builder .*{re.escape(home)} {re.escape(home)}$", runtime), \
        "the model is left in the builder"
    assert f"ENV HF_HOME={home}" in runtime, "the model ships but nothing points at it"
    assert f"SENTENCE_TRANSFORMERS_HOME={home}" in runtime
    # uid 1000 owns it. Reading the baked model would work read-only; writing a
    # different one, on a deployment that has internet and chooses one, does not.
    # On the COPY and by number: the account is created later, and a chown -R
    # after the fact rewrites every file into a second layer the same size.
    assert f"COPY --from=builder --chown=1000:1000 {home} {home}" in runtime, \
        f"{home} is not owned by the account that reads it, or costs a second layer"


def test_the_model_that_is_baked_is_the_one_the_default_config_asks_for():
    """preload.py downloads whatever the configured embedding model is, so a
    preset naming a model that is not a local sentence-transformers one would
    put the download back on the customer's first start."""
    presets = yaml.safe_load(read("plugins", "_model_config", "mode_presets_fallback.yaml"))
    embedding = presets[0]["embedding"]
    assert embedding["provider"] == "huggingface"
    assert embedding["name"].startswith("sentence-transformers/"), \
        "the default embedding model is not one the build can bake in"


def test_neither_stack_asks_a_hub_it_cannot_reach():
    """The model is in the image, so nothing should ask huggingface.co whether
    there is a newer one -- and on a site with no route out the question is not
    refused, it times out: 167 seconds of every start, before falling back to
    the cache it already had.

    Both definitions used to live in the Helm chart's values and in the cluster
    installer's air-gapped branch. Both went with the chart on 2026-09-18 and
    the compose stack, which is now the only shape, never had them.
    """
    for compose in (
        ("deploy", "customer", "docker-compose.yml"),
        ("docker", "run", "docker-compose.yml"),
    ):
        body = read(*compose)
        where = "/".join(compose)
        assert 'HF_HUB_OFFLINE: "${HF_HUB_OFFLINE-1}"' in body, \
            f"{where} lets the application wait on a hub it does not need"
        assert "LITELLM_LOCAL_MODEL_COST_MAP" in body, \
            f"{where} lets LiteLLM fetch a price list it already carries"




def test_a_build_that_did_not_download_the_model_fails_there_and_not_later():
    """preload.py catches its own exceptions, by design -- it is a warm cache,
    not a correctness gate. So a failed download leaves an empty directory that
    copies forward cleanly and is discovered by the customer with no internet."""
    builder, _ = stages()
    guard = re.search(r"(?m)^RUN test .*?(?=^\S)", builder, re.S)
    assert guard, "nothing checks that the model actually landed"
    assert model_home() in guard.group(0)
    assert "exit 1" in guard.group(0), "an empty cache does not fail the build"
    assert builder.index("install_eagent.sh") < builder.index("RUN test "), \
        "the check runs before the preload that fills the directory"
