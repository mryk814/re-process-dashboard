"""Strict ramp-hold-cool parameterization used by the #213 geometry spike."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from material_workbench.contracts.task_contracts import ContractModel
from material_workbench.execution.inference_work_graph import semantic_digest


class HeatProgramParameters(ContractModel):
    ramp_duration_s: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    peak_temperature_c: Annotated[
        float, Field(ge=-273.15, le=1800, allow_inf_nan=False)
    ]
    hold_duration_s: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    cool_duration_s: Annotated[float, Field(gt=0, allow_inf_nan=False)]


class HeatProgramTemplatePoint(ContractModel):
    phase: Literal["ramp", "hold", "cool"]
    time_fraction: Annotated[float, Field(ge=0, le=1)]
    temperature_fraction: Annotated[float, Field(ge=0, le=1)]
    segment_start: bool = False
    set_temperature_c: float | None = None
    stage_category: str | None = None
    stage_name: str | None = None
    mapping_status: str | None = None


class HeatProgramTemplate(ContractModel):
    schema_version: Literal["heat-program-template/v1"] = "heat-program-template/v1"
    decoder_id: Literal["template_ramp_hold_cool"] = "template_ramp_hold_cool"
    decoder_version: Literal["1.0.0"] = "1.0.0"
    start_time_s: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    start_temperature_c: Annotated[
        float, Field(ge=-273.15, le=1800, allow_inf_nan=False)
    ]
    end_temperature_c: Annotated[
        float, Field(ge=-273.15, le=1800, allow_inf_nan=False)
    ]
    points: Annotated[tuple[HeatProgramTemplatePoint, ...], Field(min_length=4)]

    @property
    def digest(self) -> str:
        return semantic_digest(self.model_dump(mode="json"))
