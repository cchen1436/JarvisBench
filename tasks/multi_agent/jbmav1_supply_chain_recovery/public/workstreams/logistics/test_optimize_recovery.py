from optimize_recovery import build_options


def test_three_named_options() -> None:
    options = {item["option"]: item for item in build_options()}
    assert set(options) == {"no_expedite", "partial_air_600", "full_air_900_rejected"}


def test_partial_air_is_bounded() -> None:
    option = {item["option"]: item for item in build_options()}["partial_air_600"]
    assert option["units_air"] == 600
    assert option["incremental_cost"] == 10800
    assert option["qc_assumption"] == "QC-cleared LOT-771A only"


def test_held_sublot_is_rejected() -> None:
    option = {item["option"]: item for item in build_options()}["full_air_900_rejected"]
    assert option["valid"] is False
    assert "LOT-771B" in option["qc_assumption"]
