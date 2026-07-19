from material_workbench.runtime import FEATURE_NAMES, ModelRuntime


def test_grouped_oof_calibration_and_parent_condition_support(client) -> None:
    runtime: ModelRuntime = client.app.state.runtime
    assert runtime.support_reference is not None
    assert runtime.support_reference.parent_vectors.shape[1] == len(FEATURE_NAMES)
    assert len(runtime.support_reference.parent_rows) == len(runtime.support_reference.loo_nearest_distances)
    for model in runtime.models.values():
        assert model.calibration_folds == 5
        assert len(model.oof_residuals) == len(model.rows)
        # Multiple measurement repeats exist, so calibration must split by parent condition rather than row.
        assert len({row["parent_key"] for row in model.rows}) < len(model.rows)
        lower, upper = model.interval_offsets()
        assert lower < upper
