class ResponseCurveNotApplicableError(ValueError):
    """The selected target does not depend on the requested input variable."""


class ResponseCurveTrainingRangeUnavailableError(ValueError):
    """The model may use the variable, but its package cannot define a safe curve range."""
