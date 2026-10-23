This example demonstrates an explicit reference-rule transfer during recognition.

- `oven_reference.csv`: using `reference_oven` supports cooking through a known oven association.
- `microwave_target.csv`: using `target_microwave` must substitute for that reference oven.

The devices have physical capability properties (`heats_food`, `food_preparation`,
and `bakes` or `defrosts`). Neither device has a `cooking` property. Both scenarios
store the same known **oven** association; neither stores a microwave association.

1. Open a scenario, start Manual simulation and turn its appliance ON.
2. Open **Scenario > Activity recognition…**. Double-click Cooking to inspect the
   reference, target, relevant-property match, observing sensor and confidence.
3. In the microwave house, open **Reference device rules… > Advanced reference rules…** and delete
   `known_oven_cooking`. Cooking stops on the next simulation observation even
   though the microwave's properties have not changed.
4. Reload the example to restore the original association. Edit the reference
   properties or increase its minimum similarity to explore partial/rejected matches.

Each file stores its explicit reference catalogue locally, so the demonstration
works independently of shared-library edits. To reuse the model across registered
houses, publish its effective model through the shared-library window and connect
target houses with local model overrides cleared.

This is an inference demonstration. Ground-truth labels remain separate; confidence
and property similarity do not establish measured recognition accuracy.
