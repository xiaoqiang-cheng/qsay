from dox.cli import main


def test_default_cli_explains_api_configuration(tmp_path, capsys):
    missing_config = tmp_path / "missing-config"
    exit_code = main([
        "--config",
        str(missing_config),
        "--print",
        "查看 git 状态",
    ])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "尚未配置 API" in captured.err
    assert "dox config --global llm.model" in captured.err
