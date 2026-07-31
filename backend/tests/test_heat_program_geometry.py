import pytest

from material_workbench.contracts.heat_program_contracts import HeatProgramParameters
from material_workbench.contracts.candidate_project_contracts import HeatPoint
from material_workbench.domain.heat_program import (
    HeatProgramNotRepresentable,
    decode_ramp_hold_cool,
    encode_ramp_hold_cool,
)
def test_ramp_hold_cool_template_roundtrips_canonical_history() -> None:
    points = [
        HeatPoint(
            time_s=time_s,
            temperature_c=temperature_c,
            stage_category=stage_category,
            stage_name=stage_name,
        )
        for time_s, temperature_c, stage_category, stage_name in (
            (0, 25, "加熱", "入口"),
            (40, 500, "加熱", "昇温"),
            (80, 800, "均熱", "保持開始"),
            (120, 800, "均熱", "保持終了"),
            (180, 450, "冷却", "冷却1"),
            (240, 120, "冷却", "出口"),
        )
    ]
    template, parameters = encode_ramp_hold_cool(points)
    decoded = decode_ramp_hold_cool(template, parameters)

    assert template.decoder_id == "template_ramp_hold_cool"
    assert template.decoder_version == "1.0.0"
    assert template.digest.startswith("sha256:")
    assert len(decoded) == len(points)
    for actual, expected in zip(decoded, points, strict=True):
        assert actual.time_s == pytest.approx(expected.time_s, abs=1e-9)
        assert actual.temperature_c == pytest.approx(
            expected.temperature_c, abs=1e-9
        )
        assert actual.model_dump(exclude={"time_s", "temperature_c"}) == (
            expected.model_dump(exclude={"time_s", "temperature_c"})
        )

    repeated_template, repeated_parameters = encode_ramp_hold_cool(decoded)
    assert repeated_template.digest == template.digest
    assert repeated_parameters == parameters


@pytest.mark.parametrize(
    "temperatures",
    [
        (20, 800, 800, 600, 750, 100),
        (20, 800, 600, 800, 800, 100),
        (20, 800, 700, 500),
    ],
)
def test_semantically_ambiguous_heat_routes_are_rejected(
    temperatures: tuple[float, ...],
) -> None:
    points = [
        HeatPoint(time_s=index * 10, temperature_c=temperature)
        for index, temperature in enumerate(temperatures)
    ]
    with pytest.raises(
        HeatProgramNotRepresentable,
        match="heat_program_not_representable",
    ):
        encode_ramp_hold_cool(points)


def test_segment_boundary_is_not_silently_fitted() -> None:
    points = [
        HeatPoint(time_s=0, temperature_c=20),
        HeatPoint(time_s=10, temperature_c=800),
        HeatPoint(time_s=20, temperature_c=800, segment_start=True),
        HeatPoint(time_s=30, temperature_c=100),
    ]
    with pytest.raises(HeatProgramNotRepresentable, match="segment"):
        encode_ramp_hold_cool(points)


def test_decoder_rejects_peak_below_template_end_temperature() -> None:
    points = [
        HeatPoint(time_s=0, temperature_c=25),
        HeatPoint(time_s=80, temperature_c=800),
        HeatPoint(time_s=120, temperature_c=800),
        HeatPoint(time_s=240, temperature_c=120),
    ]
    template, _ = encode_ramp_hold_cool(points)
    invalid = HeatProgramParameters(
        ramp_duration_s=80,
        peak_temperature_c=0,
        hold_duration_s=40,
        cool_duration_s=120,
    )
    with pytest.raises(HeatProgramNotRepresentable, match="最高温度"):
        decode_ramp_hold_cool(template, invalid)
