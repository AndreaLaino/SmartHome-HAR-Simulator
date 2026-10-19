This directory is shared by all scenario CSV files opened in this installation.

- `library.json`: versioned property profiles, shared activity rules and reference-house ID.
- `houses/`: registered house definitions, independent of scenario filenames.
- `datasets/`: human-labelled simulation runs, including unlabelled temporal context.
- `reports/`: property-transfer reports and measured held-out evaluation metrics.

Create or manage it through **Scenario > Shared recognition library…**.
Register your baseline house, publish its rules if desired, and mark it as the
reference. Register each target as a **new house**, connect the shared model, and
map its devices/sensors to shared property profiles. Local overrides are explicit.
Save each scenario CSV to persist its house ID, links, properties and frozen shared
rule snapshot. Use Disconnect to keep local values when the library is unavailable.

Copy this whole directory along with the scenario CSV files to move an experiment
between installations. Its JSON data is intentionally ignored by Git because it
contains local house definitions and user labels. Do not mix test-house labels
into reference tuning: reference-house runs are excluded from test metrics.

**Property coverage**, **uncalibrated confidence**, and **measured accuracy** have
different meanings. Confidence uses rule support × evidence coverage, with a
0.85 factor for inferred activities. It is not a probability of correctness.
Measured accuracy is exact activity-set agreement on manually labelled held-out
frames; precision, recall, F1, sample count and house count are reported alongside
it. Unlabelled context is never assumed to mean “no activity.” Before labelled
test evaluation, accuracy is unavailable.
