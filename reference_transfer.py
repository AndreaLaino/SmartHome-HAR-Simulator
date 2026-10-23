"""Explicit reference-object roles and weighted substitutions between objects.

A target's properties never emit activity evidence on their own. An observed
object must match a stored reference binding before it can fill that rule role.
"""

import copy

from device_properties import (default_device_properties, default_sensor_properties,
                               normalize_properties, property_key, validate_weight)


def default_reference_bindings():
    # These are the known reference associations. There is deliberately no
    # Microwave -> cooking association: it must substitute for the Oven.
    examples = {
        "cooking": ("Oven", "cooking", 1),
        "relaxing": ("TV", "relaxing", 1),
        "working": ("Computer", "working", .85),
        "cleaning": ("Vacuum_Cleaner", "cleaning", 1),
        "laundry": ("Washing_Machine", "laundry", 1),
        "dishwashing": ("Dishwasher", "dishwashing", 1),
        "drink_preparation": ("Coffee_Machine", "drink_preparation", 1),
        "toileting": ("Toilet", "toileting", 1),
        "showering": ("Shower", "showering", 1),
        "washing_hands": ("Sink", "washing_hands", 1),
        "sleeping": ("Bed", "sleeping", 1),
        "eating": ("Dining_Table", "eating", 1),
        "seating": ("Sofa", "seating", 1),
        "grooming": ("Hair_Dryer", "grooming", 1),
        "gaming": ("PlayStation", "gaming", 1),
    }
    bindings = {}
    for feature, (kind, relevant, strength) in examples.items():
        bindings[f"device:{kind}:{feature}"] = {
            "feature": feature, "kind": "device",
            "reference": {"name": kind, "type": kind, "properties": default_device_properties(kind),
                          "recognition_weight": 1, "house_id": None},
            "property_weights": {relevant: 1}, "required_properties": [relevant],
            "minimum_similarity": .5, "strength": strength, "weight": 1,
        }
    for kind, feature in (("PIR", "presence"), ("Weight", "weight"), ("Switch", "contact"),
                          ("Smart Meter", "energy"), ("Water Flow", "water_flow"),
                          ("Temperature", "temperature")):
        bindings[f"sensor:{kind}:{feature}"] = {
            "feature": feature, "kind": "sensor",
            "reference": {"name": kind, "type": kind, "properties": default_sensor_properties(kind),
                          "recognition_weight": 1, "house_id": None},
            "property_weights": {feature: 1}, "required_properties": [feature],
            "minimum_similarity": .5, "strength": 1, "weight": 1,
        }
    return bindings


def validate_reference_bindings(bindings):
    if not isinstance(bindings, dict):
        raise ValueError("Reference bindings must be a dictionary.")
    checked = copy.deepcopy(bindings)
    for key, binding in checked.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(binding, dict):
            raise ValueError("Each reference binding needs a name and definition.")
        if binding.get("kind") not in {"device", "sensor"}:
            raise ValueError("A reference binding must observe a device or sensor.")
        binding["feature"] = property_key(binding.get("feature", ""))
        if not binding["feature"]:
            raise ValueError("A reference binding needs an evidence role.")
        reference = binding.get("reference")
        if not isinstance(reference, dict) or not reference.get("name") or not reference.get("type"):
            raise ValueError("Select a reference object.")
        reference["properties"] = normalize_properties(reference.get("properties"))
        reference["recognition_weight"] = validate_weight(reference.get("recognition_weight", 1))
        binding["property_weights"] = normalize_properties(binding.get("property_weights"))
        if not any(binding["property_weights"].values()):
            raise ValueError("Give at least one relevant property a positive weight.")
        for prop, weight in binding["property_weights"].items():
            if weight > 0 and reference["properties"].get(prop, 0) <= 0:
                raise ValueError(f"Relevant property '{prop}' is missing from the reference object.")
        required = binding.get("required_properties", [])
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise ValueError("Required reference properties must be a list of names.")
        binding["required_properties"] = [property_key(item) for item in required]
        if any(reference["properties"].get(prop, 0) <= 0 for prop in binding["required_properties"]):
            raise ValueError("Required properties must exist on the reference object.")
        for field, default in (("minimum_similarity", .5), ("strength", 1), ("weight", 1)):
            binding[field] = validate_weight(binding.get(field, default))
    return checked


def property_similarity(properties, binding):
    """Directional, task-relevant coverage; extra target properties are harmless."""
    reference = binding["reference"]["properties"]
    weights = binding["property_weights"]
    missing = [prop for prop in binding["required_properties"] if properties.get(prop, 0) <= 0]
    matched = {prop: min(1, properties.get(prop, 0) / reference[prop])
               for prop, weight in weights.items() if weight > 0}
    similarity = sum(weights[prop] * value for prop, value in matched.items()) / sum(weights.values())
    return similarity, missing, matched


def match_reference_roles(obj, kind, bindings, *, house_id=None):
    matches = []
    for key, binding in bindings.items():
        if binding["kind"] != kind:
            continue
        similarity, missing, matched = property_similarity(obj.properties, binding)
        reference = binding["reference"]
        strength = (similarity * binding["strength"] * binding["weight"]
                    * reference["recognition_weight"] * obj.recognition_weight)
        if missing or similarity <= 0 or similarity < binding["minimum_similarity"] or strength <= 0:
            continue
        is_reference = (house_id == reference.get("house_id") and obj.name == reference["name"]
                        if reference.get("house_id") else obj.type == reference["type"])
        matches.append({"binding_id": key, "feature": binding["feature"], "kind": kind,
                        "target": obj.name, "target_type": obj.type,
                        "reference": reference["name"], "reference_type": reference["type"],
                        "reference_house_id": reference.get("house_id"),
                        "relation": "reference" if is_reference else "substitution",
                        "similarity": similarity, "strength": strength,
                        "matched_properties": matched})
    return matches


def explain_match(match):
    properties = ", ".join(f"{prop} {value:.0%}" for prop, value in match["matched_properties"].items())
    return (f"{match['target']} ({match['target_type']}) fills {match['reference']} "
            f"({match['reference_type']}) role '{match['feature']}': {match['similarity']:.0%} match [{properties}]")


def compatible_reference_activities(source_config, target, target_config):
    """Preview existing device-role transfers before offering activity choices.

    This is property compatibility, not an observation or a recognition result.
    A source catalogue is normally restricted to one known reference object.
    Context-only and composed activities cannot be assigned to a device here.
    """
    from activity_recognition import ACTIVITY_COMPONENTS, recognition_settings

    source = recognition_settings(source_config)
    destination = recognition_settings(target_config)
    matches = match_reference_roles(target, "device", source["reference_bindings"])
    options = {}
    for name, source_rule in source["rules"].items():
        rule = destination["rules"].get(name)
        if (name in ACTIVITY_COMPONENTS or source_rule["weight"] <= 0
                or rule is None or rule["weight"] <= 0):
            continue

        def contributes(feature):
            # The recognition engine derives bed_weight from a sleeping-role
            # fixture observed by a Weight sensor. Other sleep gates still apply.
            evidence = "bed_weight" if feature == "sleeping" and name == "sleeping" else feature
            return source_rule["weights"].get(evidence, 0) > 0 and rule["weights"].get(evidence, 0) > 0

        relevant = [match for match in matches if contributes(match["feature"])]
        if relevant:
            options[name] = {"matches": relevant,
                             "similarity": max(match["similarity"] for match in relevant)}
    return options


def import_compatible_references(source_config, target, target_config, activities):
    """Add only revalidated selected references, without changing target labels.

    Existing references remain available. Conflicting source IDs receive stable
    distinct IDs, so importing another house never overwrites a local reference.
    """
    import hashlib
    import json
    from activity_recognition import recognition_settings

    selected = set(activities)
    options = compatible_reference_activities(source_config, target, target_config)
    if not selected or not selected.issubset(options):
        raise ValueError("Choose at least one currently compatible activity. Refresh the source and destination if their properties changed.")
    source = recognition_settings(source_config)["reference_bindings"]
    bindings = copy.deepcopy(recognition_settings(target_config)["reference_bindings"])
    keys = {match["binding_id"] for name in selected for match in options[name]["matches"]}
    for key in sorted(keys):
        binding = source[key]
        if any(existing == binding for existing in bindings.values()):
            continue
        new_key = key
        if key in bindings:
            digest = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()[:12]
            new_key = f"{key}:import:{digest}"
            suffix = 2
            while new_key in bindings and bindings[new_key] != binding:
                new_key = f"{key}:import:{digest}:{suffix}"
                suffix += 1
        bindings[new_key] = copy.deepcopy(binding)
    return validate_reference_bindings(bindings)


def anchor_reference_bindings(bindings, snapshot):
    """Attach existing known associations to actual source-house objects.

Only an already-known reference type is selected. Target-house properties never
create new activity associations. Unavailable roles retain their catalogue source.
"""
    updated = copy.deepcopy(bindings)
    for binding in updated.values():
        objects = snapshot["devices" if binding["kind"] == "device" else "sensors"]
        candidates = [obj for obj in objects if obj["type"] == binding["reference"]["type"]]
        candidates.sort(key=lambda obj: (obj["name"] != binding["reference"]["name"], obj["name"]))
        for obj in candidates:
            reference = {"name": obj["name"], "type": obj["type"], "properties": copy.deepcopy(obj["properties"]),
                         "recognition_weight": obj["recognition_weight"], "house_id": snapshot["house_id"]}
            candidate = {**binding, "reference": reference}
            try:
                validate_reference_bindings({"candidate": candidate})
            except ValueError:
                continue
            binding["reference"] = reference
            break
    return validate_reference_bindings(updated)
