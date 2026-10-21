from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_nebius_provider_config_uses_openai_compatible_token_factory_endpoint():
    provider_path = PROJECT_ROOT / "conf/model_providers.yaml"
    provider_config = yaml.safe_load(provider_path.read_text(encoding="utf-8"))
    nebius = provider_config["chat"]["nebius"]

    assert nebius["name"] == "Nebius Token Factory"
    assert nebius["litellm_provider"] == "openai"
    assert nebius["kwargs"]["api_base"] == "https://api.tokenfactory.nebius.com/v1"
    assert nebius["models_list"]["endpoint_url"] == "/models"
    assert "api_key_mode" not in nebius


