from pathlib import Path

from dox.config import Config, canonical_key, load_values, parse_config, read_config, render_config


def test_defaults_to_local_qwen():
    config = Config.from_values(load_values(Path("/definitely/missing/dox-config")))
    assert config.provider == "local"
    assert config.local_model == "Qwen3-0.6B"
    assert config.local_backend == "auto"


def test_parse_and_render_git_style_config():
    source = """[core]
language = "en"

[llm]
provider = "api"
model = "demo"

[local]
backend = "mlx"
"""
    values = parse_config(source)
    assert values["core.language"] == "en"
    assert values["llm.model"] == "demo"
    assert parse_config(render_config(values)) == values


def test_aliases_are_canonicalized():
    assert canonical_key("model") == "llm.model"
    assert canonical_key("backend") == "local.backend"
    assert canonical_key("model-path") == "local.model-path"
