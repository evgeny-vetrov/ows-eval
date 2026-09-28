import pytest

from ai.gena.services.fabula.server.settings import ServerSettings, SettingsError, load_catalog, load_settings


def test_paths_resolve_against_the_file_and_the_environment_overrides_the_address(tmp_path):
    (tmp_path / "catalog.yaml").write_text("capabilities:\n  - {ref: 'echo:1@x'}\nevents:\n  - {type: x.happened}\n")
    (tmp_path / "server.yaml").write_text("http: {port: 9000}\ncatalog: {files: [catalog.yaml]}\nseed: {scenarios_dir: scenarios}\n")
    settings = load_settings(tmp_path / "server.yaml", environ={"FABULA_SERVER_PORT": "9100", "FABULA_SERVER_HOST": "0.0.0.0"})
    assert (settings.http.host, settings.http.port) == ("0.0.0.0", 9100)
    assert settings.seed.scenarios_dir == tmp_path / "scenarios"
    capabilities, events = load_catalog(settings.catalog)
    assert [c.ref for c in capabilities] == ["echo:1@x"] and [e.type for e in events] == ["x.happened"]


def test_no_file_means_the_safe_defaults():
    settings = load_settings(None, environ={})
    assert settings == ServerSettings()
    assert settings.auth.mode == "tokens" and settings.auth.grants == {} and settings.http.host == "127.0.0.1"


@pytest.mark.parametrize(
    "content, message",
    [
        ("- a list\n", "the top level must be a mapping"),
        ("http: {port: nope}\n", "http.port"),
        ("auth: {mode: open}\n", "auth.mode"),
        ("unknown: 1\n", "unknown"),
        ("auth: {tokens: [{sha256: short, actor: 'user:a'}]}\n", "auth.tokens.0.sha256"),
        ("background: {agents: [{actor_id: a}, {actor_id: a}]}\n", "distinct actor_id"),
        ("http: [\n", "cannot read"),
    ],
)
def test_mistakes_are_reported_with_their_place(tmp_path, content, message):
    (tmp_path / "server.yaml").write_text(content)
    with pytest.raises(SettingsError, match=message):
        load_settings(tmp_path / "server.yaml", environ={})


def test_a_capability_declared_twice_is_a_mistake(tmp_path):
    (tmp_path / "a.yaml").write_text("capabilities: [{ref: 'echo:1@x'}]\n")
    settings = ServerSettings.model_validate({"catalog": {"files": [str(tmp_path / "a.yaml")], "capabilities": [{"ref": "echo:1@x"}]}})
    with pytest.raises(SettingsError, match="capability declared twice: echo:1@x"):
        load_catalog(settings.catalog)
