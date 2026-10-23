This directory is shared by all scenario CSV files opened in this installation.

- `library.json`: versioned property profiles, explicit reference-object associations, shared activity rules and reference-house ID.
- `houses/`: registered house definitions, independent of scenario filenames.
- `datasets/`: human-labelled simulation runs, including unlabelled temporal context.
- `reports/`: property-transfer reports and measured held-out evaluation metrics.
- `reports/experiments/`: versioned source/target comparisons, frozen data, JSON
  forests, source-only calibration, causal features and prediction reports.

Create or manage it through **Scenario > Shared recognition library…**.
The simplified window registers houses, lists their device/sensor counts, selects
the reference and opens the existing device-association editor. **Advanced…** keeps
the complete shared model, reference rules, transfer reports and evaluation controls.
Selecting a reference does not publish its local rules; the window explains this.
Register your baseline house, publish its rules in **Advanced…** if desired, and mark it as the
reference. Register each target as a **new house**, connect the shared model, and
map its devices/sensors to shared property profiles. Local overrides are explicit.
Save each scenario CSV to persist its house ID, links, properties and frozen shared
rule snapshot. Use Disconnect to keep local values when the library is unavailable.

In **Reference rules**, store the known association (for example Oven → cooking)
and the source object's frozen properties. Observed target devices/sensors must
match a reference association before they supply an activity role. Microwave →
cooking is derived from the oven match; it is not a built-in direct association.
Relevant property weights, required properties and minimum similarity control
substitution. Extra target properties are allowed. Reference associations are
included in scenario snapshots, model fingerprints and held-out replay.

Default associations provide a known reference catalogue. Setting a reference
house anchors existing associations to source objects with the known type; missing
source types retain their catalogue examples. Edit or delete them to restrict the
experiment to your own reference knowledge. An explicit empty association map
disables all object roles. Old libraries migrate once without replacing existing
property profiles, activity thresholds or house identities.

Copy this whole directory along with the scenario CSV files to move an experiment
between installations. Its JSON data is intentionally ignored by Git because it
contains local house definitions and user labels. Do not mix test-house labels
into reference tuning: reference-house runs are excluded from test metrics.

**Property coverage**, **uncalibrated confidence**, and **measured accuracy** have
different meanings. Confidence uses rule support × evidence coverage, with a
0.85 factor for inferred activities. It is not a probability of correctness.
Eating while relaxing additionally requires independently recognized eating and
relaxing, retains both base activities, and caps its confidence at the weaker
component. Their intervals are logged separately, including activities in progress.
Measured accuracy is exact activity-set agreement on manually labelled held-out
frames; precision, recall, F1, sample count and house count are reported alongside
it. Unlabelled context is never assumed to mean “no activity.” Before labelled
test evaluation, accuracy is unavailable.
