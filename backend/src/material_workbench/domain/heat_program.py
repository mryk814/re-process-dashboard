"""Strict semantic heat-program encoder/decoder.

This spike deliberately refuses reheating and multi-stage routes instead of
silently fitting them to a simpler program.
"""
from __future__ import annotations

import math

from material_workbench.contracts.heat_program_contracts import (
    HeatProgramParameters,
    HeatProgramTemplate,
    HeatProgramTemplatePoint,
)
from material_workbench.contracts.schemas import HeatPoint


class HeatProgramNotRepresentable(ValueError):
    code = "heat_program_not_representable"

    def __init__(self, message: str) -> None:
        super().__init__(f"[{self.code}] {message}")


def encode_ramp_hold_cool(
    points: list[HeatPoint] | tuple[HeatPoint, ...],
) -> tuple[HeatProgramTemplate, HeatProgramParameters]:
    if len(points) < 4:
        raise HeatProgramNotRepresentable("4点以上の履歴が必要です")
    if any(point.segment_start for point in points[1:]):
        raise HeatProgramNotRepresentable("途中のsegment境界を含む履歴は対象外です")
    times = [point.time_s for point in points]
    temperatures = [point.temperature_c for point in points]
    if any(
        not math.isfinite(value)
        for value in (*times, *temperatures)
    ) or any(later <= earlier for earlier, later in zip(times, times[1:])):
        raise HeatProgramNotRepresentable("時刻・温度が有限で時刻昇順である必要があります")
    peak = max(temperatures)
    peak_indices = [
        index for index, temperature in enumerate(temperatures)
        if temperature == peak
    ]
    first_peak = peak_indices[0]
    last_peak = peak_indices[-1]
    if peak_indices != list(range(first_peak, last_peak + 1)):
        raise HeatProgramNotRepresentable("最高温度plateauが連続していません")
    if first_peak < 1 or last_peak >= len(points) - 1 or first_peak == last_peak:
        raise HeatProgramNotRepresentable("ramp・正のhold・coolが必要です")
    if any(
        later < earlier
        for earlier, later in zip(
            temperatures[: first_peak + 1],
            temperatures[1 : first_peak + 1],
        )
    ) or any(
        later > earlier
        for earlier, later in zip(
            temperatures[last_peak:],
            temperatures[last_peak + 1 :],
        )
    ):
        raise HeatProgramNotRepresentable("再加熱または多段冷却を含む履歴は対象外です")
    if temperatures[0] >= peak or temperatures[-1] >= peak:
        raise HeatProgramNotRepresentable("開始・終了温度は最高温度未満にしてください")

    ramp_duration = times[first_peak] - times[0]
    hold_duration = times[last_peak] - times[first_peak]
    cool_duration = times[-1] - times[last_peak]
    parameters = HeatProgramParameters(
        ramp_duration_s=ramp_duration,
        peak_temperature_c=peak,
        hold_duration_s=hold_duration,
        cool_duration_s=cool_duration,
    )
    template_points = []
    for index, point in enumerate(points):
        if index < first_peak:
            phase = "ramp"
            time_fraction = (point.time_s - times[0]) / ramp_duration
            temperature_fraction = (
                (point.temperature_c - temperatures[0])
                / (peak - temperatures[0])
            )
        elif index <= last_peak:
            phase = "hold"
            time_fraction = (
                (point.time_s - times[first_peak]) / hold_duration
            )
            temperature_fraction = 1.0
        else:
            phase = "cool"
            time_fraction = (
                (point.time_s - times[last_peak]) / cool_duration
            )
            temperature_fraction = (
                (peak - point.temperature_c)
                / (peak - temperatures[-1])
            )
        template_points.append(
            HeatProgramTemplatePoint(
                phase=phase,
                time_fraction=time_fraction,
                temperature_fraction=temperature_fraction,
                segment_start=point.segment_start,
                set_temperature_c=point.set_temperature_c,
                stage_category=point.stage_category,
                stage_name=point.stage_name,
                mapping_status=point.mapping_status,
            )
        )
    return (
        HeatProgramTemplate(
            start_time_s=times[0],
            start_temperature_c=temperatures[0],
            end_temperature_c=temperatures[-1],
            points=tuple(template_points),
        ),
        parameters,
    )


def decode_ramp_hold_cool(
    template: HeatProgramTemplate,
    parameters: HeatProgramParameters,
) -> list[HeatPoint]:
    if parameters.peak_temperature_c <= max(
        template.start_temperature_c,
        template.end_temperature_c,
    ):
        raise HeatProgramNotRepresentable(
            "最高温度は開始・終了温度より高い必要があります"
        )
    phases = [point.phase for point in template.points]
    phase_rank = {"ramp": 0, "hold": 1, "cool": 2}
    if (
        phases[0] != "ramp"
        or phases[-1] != "cool"
        or "hold" not in phases
        or any(
            phase_rank[later] < phase_rank[earlier]
            for earlier, later in zip(phases, phases[1:])
        )
    ):
        raise HeatProgramNotRepresentable(
            "templateはramp→hold→coolの順である必要があります"
        )
    for phase in ("ramp", "hold", "cool"):
        phase_points = [point for point in template.points if point.phase == phase]
        fractions = [point.time_fraction for point in phase_points]
        if any(
            later <= earlier
            for earlier, later in zip(fractions, fractions[1:])
        ):
            raise HeatProgramNotRepresentable(
                f"{phase}内のtime fractionは狭義単調増加である必要があります"
            )
    start = template.start_time_s
    ramp_end = start + parameters.ramp_duration_s
    hold_end = ramp_end + parameters.hold_duration_s
    decoded = []
    for point in template.points:
        if point.phase == "ramp":
            time_s = start + point.time_fraction * parameters.ramp_duration_s
            temperature_c = template.start_temperature_c + (
                point.temperature_fraction
                * (
                    parameters.peak_temperature_c
                    - template.start_temperature_c
                )
            )
        elif point.phase == "hold":
            time_s = ramp_end + point.time_fraction * parameters.hold_duration_s
            temperature_c = parameters.peak_temperature_c
        else:
            time_s = hold_end + point.time_fraction * parameters.cool_duration_s
            temperature_c = parameters.peak_temperature_c - (
                point.temperature_fraction
                * (
                    parameters.peak_temperature_c
                    - template.end_temperature_c
                )
            )
        decoded.append(
            HeatPoint(
                time_s=time_s,
                temperature_c=temperature_c,
                segment_start=point.segment_start,
                set_temperature_c=point.set_temperature_c,
                stage_category=point.stage_category,
                stage_name=point.stage_name,
                mapping_status=point.mapping_status,
            )
        )
    if any(
        later.time_s <= earlier.time_s
        for earlier, later in zip(decoded, decoded[1:])
    ):
        raise HeatProgramNotRepresentable(
            "decoder結果の時刻が狭義単調増加になりません"
        )
    return decoded
