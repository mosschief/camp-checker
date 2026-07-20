import yaml

from campwatch.camply_gen import camply_search_dict, write_camply_configs


def test_generated_yaml_validates_against_camply_model(app_config, tmp_path):
    """The generated files must be accepted by camply's own YamlSearchFile model."""
    from camply.containers.search_model import YamlSearchFile

    paths = write_camply_configs(app_config, tmp_path / "runtime")
    assert len(paths) == 2
    for path in paths:
        model = YamlSearchFile(**yaml.safe_load(path.read_text()))
        assert model.provider.value == "GoingToCamp"
        assert model.recreation_area == 3
        assert model.continuous is True
        assert model.search_forever is True
        assert model.notifications == "webhook"


def test_search_dict_uses_watch_values(app_config):
    watch = app_config.watch_by_name("kanaskat-palmer-aug")
    search = camply_search_dict(app_config, watch)
    assert search["campgrounds"] == 111
    assert search["start_date"] == "2026-08-14"
    assert search["end_date"] == "2026-08-16"
    assert search["nights"] == 1
    assert search["polling_interval"] == 5
