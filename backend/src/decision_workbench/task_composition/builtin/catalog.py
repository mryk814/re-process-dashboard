"""Immutable collection of bundled Task family modules."""

from __future__ import annotations

from decision_workbench.task_composition.builtin.annealed import ANNEALED_TASK_MODULE
from decision_workbench.task_composition.builtin.flank_wear import (
    FLANK_WEAR_TASK_MODULE,
)
from decision_workbench.task_composition.builtin.hot_rolling import (
    HOT_ROLLING_TASK_MODULE,
)
from decision_workbench.task_composition.builtin.tabular import TABULAR_TASK_MODULES
from decision_workbench.task_composition.builtin.welding import (
    WELDING_STAGE_B_TASK_MODULE,
    WELDING_STAGE_C_TASK_MODULE,
)
from decision_workbench.task_composition.descriptors import TaskModule

_BUILTIN_TASK_MODULE_SEQUENCE: tuple[TaskModule, ...] = (
    WELDING_STAGE_B_TASK_MODULE,
    ANNEALED_TASK_MODULE,
    HOT_ROLLING_TASK_MODULE,
    FLANK_WEAR_TASK_MODULE,
    *TABULAR_TASK_MODULES,
    WELDING_STAGE_C_TASK_MODULE,
)
BUILTIN_TASK_MODULES: dict[str, TaskModule] = {
    module.task_id: module for module in _BUILTIN_TASK_MODULE_SEQUENCE
}
