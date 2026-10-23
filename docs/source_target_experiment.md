# Source-house / target-house recognition experiment

Open **Scenario > Source / target experiment...**. The same window is available
from the shared library's **Accuracy / ground truth** tab.

## What is compared

| Method | Knowledge and inputs |
| --- | --- |
| Reference-transfer rules | A known Oven/TV reference association; target device usage must match its relevant physical capabilities. |
| Direct-property rules | Earlier property-to-role behavior under the same sensor/context gates. A physical `heats_food` property does not automatically become a `cooking` property. |
| Semantic Random Forest | Source-labelled training sessions, primitive matched roles and causal temporal features shared across houses. |
| Source-identity Random Forest | The same source training labels, with a fixed source sensor/device identity schema. Unseen target identities have zero corresponding channels. |

The identity baseline deliberately exposes the problem of environment-specific
sensor columns. The direct-property baseline exposes the distinction between an
activity-labelled property and a reference match. These comparisons do not prove
that every possible property-based or raw-sensor method performs worse.

## Run the included demonstration

Choose **Demo - scripted synthetic ground truth**, leave the default 20 sessions
per house, and click **Run experiment**. Computation runs outside Tk's event loop;
the comparison table and exported results appear on completion.

The source contains an Oven and TV. The target contains a Microwave and
PlayStation with physical capabilities and additional unrelated properties, but
no direct cooking/relaxing properties or target activity associations. The
controlled script includes:

- Cooking with observed Kitchen presence.
- Relaxation before any cooking.
- Cooking followed by concurrent eating and relaxing, using the stated behavioral
  assumption. These frames carry both base labels and the additional
  `eating while relaxing` composite label.
- Idle/away periods, standby with Kitchen presence, unrelated heating, and an
  operating food appliance without Kitchen presence.

Labels express scenario intent and are generated independently of the
recognizer. They are not observations of people. Durations, power readings and
time of day vary deterministically with the random seed. Every session starts
with fresh temporal state. Sampling is denser in positive phases than long idle
periods to balance frame counts; metrics are frame-weighted and reflect this
sampling distribution.

The demo is also available from the command line:

```sh
python -m har_experiment --demo --sessions 20 --seed 42
```

Use `--output PATH` for an empty output directory, `--window-minutes 10` for the
rolling observation window, and `--time-of-day` to add explicit clock features.
`--broad-cooking` disables the experiment's Kitchen-PIR observation gate.

## Use independent human-labelled sessions

1. In **Shared recognition library...**, register the source house and set it as
   reference. Configure known source associations before evaluating target data.
2. Record at least **five independent Reference sessions** in the source house.
   Reset the simulation between sessions. Label actual activities or explicitly
   label no activity; retain unlabelled observations for temporal context.
3. Register a different target house as a new house. Configure its physical
   device/sensor capabilities and record **Held-out test** sessions with labels.
4. Select **Registered human-labelled datasets** in the experiment window.

Training uses 60% of source Reference sessions, sigmoid calibration uses 20%, and
the final source comparison uses 20%, rounded by complete sessions. Additional
source-house sessions explicitly marked Test remain final source tests. Every
other house marked Test is a target test house. No target label or target feature
distribution is used to fit the forests, calibrators or feature schema.

The training label vocabulary comes only from source training. Activities seen
only in target ground truth are reported as unsupported and count as errors.
Collect more source sessions if a rare source activity is missing from its
training split. Other houses' Reference sessions are listed as ignored.

Label every recognized activity intended by the protocol: concurrent eating and
relaxing use both base labels, plus `eating while relaxing` when the known
cooking-then-relaxing composite applies. The live rule system preserves the base
activities and requires each before emitting that composite. Composite confidence
cannot exceed either constituent's confidence. Existing datasets with only the
old composite label are not rewritten automatically; record or explicitly review
their labels before comparing them with the updated multi-label output. Composite
scores are redundant with their constituents and count as an additional class in
the reported micro/macro metrics; inspect per-activity scores as well.

New recordings preserve an explicit simulation-session ID across model changes.
Fragments of one declared session cannot be split into independent train/test
runs. Identical copied source observation traces are rejected even when labels
and run names differ. Legacy run-only datasets receive an independence warning.

Do not tune the model after inspecting final target results while continuing to
call the same target set held out. Reserve additional target houses/sessions for
the final evaluation of any revised model.

## Features and confidence

Semantic features use primitive matched object/sensor roles and physical context.
For each role, the extractor records its latest strength, time-weighted rolling
mean, change count and time since positive observation. It also records observed
window duration and sensor event counts. Full future activity duration, activity
labels, rule predictions/confidences and hand-written composite activity decisions
are excluded from classifier inputs. The time-since-use feature is capped at the
larger of the observation window and configured meal-memory duration, plus one.

Time-of-day sine/cosine features are optional and require an explicit simulation
clock on every frame. Elapsed time is not silently treated as a wall clock.
Old datasets without clock values can still be used with this option disabled.

Both forests use 128 trees, a maximum depth of 12, a minimum leaf size of two and
a fixed random seed. Eligible output probabilities receive sigmoid calibration
using separate source validation rows. An output needs both source training
classes and at least five positive and five negative validation examples;
otherwise its forest votes remain explicitly uncalibrated.

The positive class threshold is fixed at 0.5. **Minimum decision certainty** can
make the forest abstain when any known-label decision is too uncertain. The same
requested cutoff applies to heuristic rule decisions, including idle decisions.
Rule confidence and calibrated forest probabilities have different meanings;
source calibration does not establish calibration in another house.

Cooking requires positive Kitchen-PIR evidence by default in the experiment,
before cooking can enter temporal memory. This implements the paper's stricter
observation baseline without changing the broad default of existing scenarios.
An authoritative Smart Meter reading is not overridden by presence fallback;
standby/zero readings cannot become appliance-use evidence merely because its
device state is ON and someone is nearby.

## Outputs and replay

Each execution creates a new directory under
`recognition_library/reports/experiments/`. Existing experiments are not overwritten.

- `report.html`, `report.md`, `report.json`: comparison, counts, calibration and limitations.
- `comparison.csv`, `per_activity.csv`: aggregate and class-wise metrics/confusion counts.
- `predictions.json`: predictions, independent labels, abstentions, probabilities
  and reference explanations on final source and target tests.
- `features.json`: feature schemas, raw source identities and extracted samples.
- `experiment.json`: frozen observations, labels, settings and property catalogue.
- `models.json`: portable tree arrays and calibration parameters, without pickle.

Reports include exact activity-set accuracy, micro/macro F1, class support,
per-house metrics, session/house counts, unsupported activities, coverage and
selective accuracy. Abstentions remain incorrect in the full exact-accuracy
denominator, including on idle frames. Per-activity counts retain actual partial
predictions. A scoped Brier score is available for model probabilities on known
classes; unknown labels still count as false negatives in recognition metrics.

Replay an existing result without refitting or reading the live library:

```sh
python -m har_experiment --replay recognition_library/reports/experiments/RUN_DIRECTORY
```

Replay verifies observation, model, inference-code and extracted-feature
fingerprints. If the implementation changes, create a new experiment to compare
it rather than silently treating the updated inference as an identical replay.

High scores on the scripted demo establish correctness on that controlled
distribution. Real-world recognition, robustness to noisy/missing sensors,
multiple occupants, and confidence calibration on other houses require separate
data and experiments.
