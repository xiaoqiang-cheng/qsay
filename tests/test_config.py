from pathlib import Path

from qsay.config import Config, canonical_key, load_values, parse_config, read_config, render_config


def test_defaults_to_api_with_local_qwen_available():
    config = Config.from_values(load_values(Path("/definitely/missing/qsay-config")))
    assert config.provider == "api"
    assert config.local_model == "Qwen3-0.6B"
    assert config.local_backend == "auto"


def test_explicit_local_provider_overrides_api_default():
    values = load_values(Path("/definitely/missing/qsay-config"))
    values["llm.provider"] = "local"
    assert Config.from_values(values).provider == "local"


def test_parse_and_render_git_style_config():
    source = """[core]
language = "en"

[llm]
provider = "api"
model = "demo"
api-key = "secret"

[local]
backend = "mlx"
"""
    values = parse_config(source)
    assert values["core.language"] == "en"
    assert values["llm.model"] == "demo"
    assert values["llm.api-key"] == "secret"
    assert parse_config(render_config(values)) == values


def test_aliases_are_canonicalized():
    assert canonical_key("model") == "llm.model"
    assert canonical_key("backend") == "local.backend"
    assert canonical_key("model-path") == "local.model-path"
