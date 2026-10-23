# Research alignment: Digital Twins, activity recognition and device transfer

Reviewed 2026-10-20. Page references below refer to the six PDF pages in each
supplied document. This note distinguishes statements in the papers from the
additional transfer requirements discussed with the user.

## Sources

- **Paper A:** [Digital Twin Framework for Personalized Building Management in Ambient Assisted Living](../BuildingDigitalTwin_WF_IoT24.pdf), Marcello, Chouquir, Atzori and Pilloni. Architecture and requirements: pp. 2-3; ontology and personal behavior: p. 4; prototype: pp. 5-6.
- **Paper B:** [Digital Twin-Based Framework for Behavioral Modeling and Activity Recognition in Smart Homes](<../DT_based_framework_for_behavioral_modeling_and_activity_recognition_in_smart_homes 1.pdf>), Marcello, Petza, Martalo and Pilloni. System model: pp. 2-3; recognition rules: p. 4; validation and classifier: pp. 5-6.

## What the papers establish

Paper A models three connected kinds of Digital Twin (Fig. 3, p. 4):

| Entity | Information and relationships |
| --- | --- |
| Object DT (ODT) | Sensors, actuators, appliances, functions/tasks, observations and energy profiles. |
| Personal DT (PDT) | Current activities, historical habits, behavior and interactions with objects. |
| Building DT (BDT) | Building/floor/room structure, environmental state, contained objects and occupants. |

Its architecture combines current observations with sensor history, building
state and user activities/habits (Fig. 2, p. 3). Mobility between buildings and
consistency of people/object representations are explicit requirements. Its
personal-behavior discussion mentions a Naive Bayes classifier, referring to
related work; this does not specify the proposed oven/microwave transfer method.
The prototype uses Azure services and multiple external datasets. Those deployment
choices do not establish that this simulator must migrate to Azure.

Paper B develops the simulator as a controllable source of sensor/device events
and temporal activity patterns, with replay and what-if scenarios. The baseline
online recognition rules and the classification experiment are distinct:

- Cooking uses an operating oven observed by a Smart Meter and an active kitchen
  PIR (p. 4).
- Sleeping uses more than ten simulated minutes of bed-weight presence (p. 4).
- Meal labels use kitchen/seat evidence and time-of-day intervals (p. 4).
- Concurrent activities can appear in the interface (p. 4), but the reported
  Random Forest confusion matrix evaluates ten individual activity classes.
- Validation uses 20 manually executed simulations per activity: 14 for training
  and 6 for testing (p. 5). Features are duration, event count, per-sensor
  activation ratios and a cosine encoding of time of day.
- The reported perfect classification matrix contains six test examples per
  class (Fig. 7, p. 6). It establishes results for that experiment; it does not
  demonstrate unseen-house transfer or general real-world accuracy.
- The paper explicitly identifies one occupant, ideal sensors, no modeled
  missing/noisy events and one appliance-use pattern per appliance as limitations
  (pp. 5-6).

Neither supplied paper specifies weighted reference-object substitution, an
oven/microwave similarity formula, or a calibrated confidence score for that
substitution. Those belong to the user's proposed extension.

## Interpretation of the requested extension

The requested contribution is to reuse known activity knowledge when the objects
and sensors differ between houses. A stored Oven -> cooking association is the
source knowledge. A microwave has capabilities, but no direct cooking association.
Observed use can fill the oven role after a relevant capability match.

The capability vocabulary, reference associations and activity definitions belong
in a shared model. Each house retains its own object instances, sensor identities,
positions and observations. A reference house supplies source examples/data; it
does not need to own the entire global vocabulary or other houses' state.

Separate the following concepts in the model and experiments:

1. **Capability/task:** what an object can do, such as heating/preparing food.
2. **Observation/state:** what is actually measured, such as power, presence or flow.
3. **Reference association:** the known connection between observed object use and
   an activity role.
4. **Activity inference:** a decision from matched roles, temporal/contextual
   evidence and thresholds.
5. **Ground truth:** independently supplied actual activities used for evaluation.

An object's capabilities are insufficient evidence of current use. Generic
similarity (for example, producing heat) is insufficient to substitute a hair
dryer for an oven. Relevant/required properties and adequate observation evidence
must constrain the transfer.

## Current implementation and material gaps

- `reference_transfer.py` now stores explicit reference associations and computes
  task-relevant, weighted property coverage during recognition. Target properties
  do not directly emit activity roles. Deleting the source association blocks its
  contribution; original device names/types do not bypass the property match.
- `activity_recognition.py` combines these matched roles with observations,
  sequences and context. Explanations preserve source/target objects, the reference
  rule, similarity and sensor provenance.
- `recognition_library.py` shares versioned profiles, associations and rules.
- `recognition_evaluation.py` replays independently labelled held-out rule runs.
  The added `har_experiment.py` pipeline now compares those rules with source-only
  Random Forest classifiers, separate source validation calibration, held-out
  source/target sessions and reproducible frozen-model replay. Its causal features
  differ from the paper's complete-episode features, as required for online use.
- The current property matcher compares normalized keys and numeric weights. It
  is not a formal ontology reasoner and cannot infer arbitrary equivalence or
  subsumption between previously unseen capability names.
- The default cooking rule currently allows meter-supported appliance use without
  presence: 0.8 support exceeds the 0.65 threshold. This differs from Paper B's
  kitchen-PIR-plus-meter baseline and must be an explicit experimental choice.
- Added lighting conditions for sleep and cooking-then-relaxing meal inference
  come from the user's requirements. They are extensions to the paper's baseline.
- Confidence is a heuristic evidence-support score. It is not a calibrated
  probability, a property-similarity accuracy estimate or learned user behavior.

## Recommended next experiment

This is a proposed implementation/evaluation direction, not a method already
specified by either paper.

1. Preserve the direct-property recognizer as a comparison baseline and evaluate
   explicit reference substitution under the same observation/context rules.
2. Define a controlled capability vocabulary, including documented aliases or
   hierarchical relationships where needed. Keep source associations separate
   from target capability descriptions.
3. Generate/replay independent sessions in a source house with Oven/TV and target
   houses with Microwave/PlayStation or other substitutions. Include different
   durations, habits and device strengths, plus negative and ambiguous examples.
4. Keep actual activity labels independent of recognizer predictions. Use target
   labels only for testing when claiming transfer without target training data.
5. For a learned-recognition extension, export stable semantic feature windows
   derived from the matched roles. Compare a Random Forest baseline with the
   rule system. Raw columns such as a particular oven meter's name cannot serve
   as the shared representation across unrelated houses.
6. Separate sessions/houses before training, tuning or feature normalization.
   Full-activity duration is available after an episode; live recognition needs
   causal windows/features that do not read its future end time.
7. Report class-wise precision/recall/F1, sample and house counts, false positives,
   abstention/coverage and transfer errors. Use activity-set or multi-label metrics
   for concurrent activities. Calibrate confidence on separate validation data if
   it is to represent a probability.

The reference matcher is a reusable semantic mapping component. The learned
source/target experiment is now implemented; see
[the experiment guide](source_target_experiment.md). Scripted benchmark results
and passing software tests do not establish real-world transfer accuracy. The
remaining research work is independent human-data collection, final held-out
validation, broader sensor/occupant variability and confidence calibration across
houses.
