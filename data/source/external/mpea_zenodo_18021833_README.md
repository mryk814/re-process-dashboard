# MPEA literature dataset (Zenodo 18021833)

- Source: https://zenodo.org/records/18021833
- Article DOI: https://doi.org/10.1016/j.dib.2026.112540
- Dataset DOI: https://doi.org/10.5281/zenodo.18021833
- License: CC BY 4.0
- Bundled file: `mpea_ground_truth_18021833.csv`
- Upstream MD5: `28ec29a243176e40fa5da7292630f7b3`

The upstream file is kept unchanged. It contains 396 material records from 100
papers, a two-row header, mixed units, partially reported targets, and six
unnamed trailing columns. The companion Curation Recipe is deliberately strict:
it only accepts finite 14-element atomic-percent compositions summing to
99–101 at% and safely parseable tensile yield strength.

`File_Name`, `Material`, microstructure, precipitate, and other measured
properties are provenance/evidence only. They must not be used as predictors in
the first task because they can leak paper identity or post-process observations.
Validation must be grouped by `File_Name`, never split randomly by row.

The first package is a deliberately conservative composition-only ridge
baseline. Candidate editing stays disabled until the Design Space composition
simplex constraint is connected to proposal generation. The baseline is for
workflow and uncertainty inspection, not a claim of literature-grade accuracy.
