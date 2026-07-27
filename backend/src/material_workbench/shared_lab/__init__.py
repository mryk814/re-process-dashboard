"""Explicit PostgreSQL/S3 shared-workbench experiment.

This package is intentionally separate from the local-first application. Import
``create_shared_lab_app`` only from a shared-mode entry point.
"""

from material_workbench.shared_lab.app import create_shared_lab_app

__all__ = ["create_shared_lab_app"]
