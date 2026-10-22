import math


def discard_unresolved_cues(manifest):
    removed = []
    for scene in manifest.get("scenes", []):
        for step_index, step in enumerate(scene.get("steps", [])):
            retained = []
            for cue in step.get("cues", []):
                timing = cue.get("at")
                if (isinstance(timing, (int, float)) and not isinstance(timing, bool)
                        and math.isfinite(timing) and timing >= 0):
                    retained.append(cue)
                else:
                    removed.append({"scene": scene["id"], "step": step_index,
                                    "phrase": cue.get("phrase"), "reason": "no valid explicit timing without narration"})
            if retained:
                step["cues"] = retained
            else:
                step.pop("cues", None)
    return removed
