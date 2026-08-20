"""Guarded v8.5 Layer-1 patch for current-main colony_planner.py.

Fixes the original v8.5 patcher's ambiguous explanation anchor.  The current
planner intentionally contains the same home-distance explanation in both the
universal-hab and normal-hab branches, so this patcher requires exactly TWO
copies and updates both.  All other structural anchors remain single-match and
fail closed on drift.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys

EXPECTED_BLOB = "ee212ee883f93ebc8004f19fef58635558baf0aa"
MARKER = "V8_5_LAYER1_COLONY_PROGRAM"


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def replace_exactly(text: str, old: str, new: str, expected: int, label: str) -> str:
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"{label}: expected {expected} anchor(s), found {count}")
    return text.replace(old, new)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    return replace_exactly(text, old, new, 1, label)


def patch(text: str) -> str:
    if MARKER in text:
        return text

    anchor = '''    policy=colonization_policy(state,plan)\n    candidates=[]\n\n    for p in state.planets:\n'''
    insertion = '''    policy=colonization_policy(state,plan)\n    candidates=[]\n\n    # V8_5_LAYER1_COLONY_PROGRAM\n    # Opening onion doctrine: deliberately establish 4-5 quality Layer-1 hubs\n    # around the homeworld. This is a SCORE bonus only; normal racial-hab,\n    # resource-exception, terraform and support-distance eligibility still\n    # applies first, so the AI never settles a bad world just to hit a quota.\n    turn=max(0,int(state.year)-2400)\n    owned_layer1=sum(\n        1 for q in owned\n        if 65.0 <= distance_from_homeworld(state,q.position) <= 190.0\n    )\n    layer1_needed=max(0,5-owned_layer1)\n\n    for p in state.planets:\n'''
    text = replace_once(text, anchor, insertion, "candidate prelude")

    anchor = '''        if turn<=25:\n            home_penalty=home_distance*.14\n            home_bonus=max(0.0,200.0-home_distance)*.10\n        else:\n            home_penalty=home_distance*.05\n            home_bonus=max(0.0,120.0-home_distance)*.04\n\n        if universal:\n'''
    insertion = '''        if turn<=25:\n            home_penalty=home_distance*.14\n            home_bonus=max(0.0,200.0-home_distance)*.10\n        else:\n            home_penalty=home_distance*.05\n            home_bonus=max(0.0,120.0-home_distance)*.04\n\n        layer1_bonus=0.0\n        if turn<=30 and layer1_needed>0 and 65.0<=home_distance<=190.0:\n            radial=max(0.0,1.0-abs(home_distance-130.0)/100.0)\n            layer1_bonus=(\n                8.0\n                + 2.5*min(5,nearby_frontier)\n                + 7.0*radial\n                + 1.5*min(5,layer1_needed)\n            )\n\n        if universal:\n'''
    text = replace_once(text, anchor, insertion, "layer1 score prelude")

    text = replace_once(
        text,
        '''                mineral_bonus+known_bonus+cluster_bonus+home_bonus\n                -travel_penalty-support_penalty-home_penalty-population_penalty-stale_penalty\n''',
        '''                mineral_bonus+known_bonus+cluster_bonus+home_bonus+layer1_bonus\n                -travel_penalty-support_penalty-home_penalty-population_penalty-stale_penalty\n''',
        "universal score",
    )

    text = replace_once(
        text,
        '''                hab+quality_bonus+mineral_bonus+cluster_bonus+home_bonus\n                -travel_penalty-support_penalty-home_penalty-population_penalty\n''',
        '''                hab+quality_bonus+mineral_bonus+cluster_bonus+home_bonus+layer1_bonus\n                -travel_penalty-support_penalty-home_penalty-population_penalty\n''',
        "normal score",
    )

    # This two-line explanation is intentionally present in BOTH scoring branches.
    # The original v8.5 patcher incorrectly required a unique occurrence.
    explanation_anchor = '''                f"home distance {home_distance:.1f} adds {home_bonus:.1f} and costs {home_penalty:.1f}; "\n                f"remembered intel age={intel_age}"\n'''
    explanation_replacement = '''                f"home distance {home_distance:.1f} adds {home_bonus:.1f} and costs {home_penalty:.1f}; "\n                f"Layer-1 hub program bonus={layer1_bonus:.1f} (need {layer1_needed} more of target 5); "\n                f"remembered intel age={intel_age}"\n'''
    text = replace_exactly(
        text,
        explanation_anchor,
        explanation_replacement,
        2,
        "universal + normal explanations",
    )

    # Fail closed if all intended semantic changes are not present.
    if text.count(MARKER) != 1:
        raise RuntimeError(f"postcondition: expected one {MARKER} marker")
    if text.count("+home_bonus+layer1_bonus") != 2:
        raise RuntimeError("postcondition: both universal and normal scores were not patched")
    if text.count("Layer-1 hub program bonus={layer1_bonus:.1f}") != 2:
        raise RuntimeError("postcondition: both explanations were not patched")

    compile(text, "colony_planner.py", "exec")
    return text


def main() -> int:
    path = Path.cwd() / "src" / "stars_ai" / "colony_planner.py"
    if not path.exists():
        print(f"ERROR: run from repo root; missing {path}", file=sys.stderr)
        return 2

    raw = path.read_bytes()
    text = raw.decode("utf-8")

    if MARKER in text:
        print("colony_planner.py already contains v8.5 Layer-1 program patch")
        return 0

    sha = git_blob_sha(raw)
    if sha != EXPECTED_BLOB:
        print(
            "ERROR: colony_planner.py does not match expected current-main baseline.\n"
            f"  expected blob: {EXPECTED_BLOB}\n"
            f"  actual blob:   {sha}\n"
            "Refusing blind patch; rebase this package against the newer planner.",
            file=sys.stderr,
        )
        return 3

    updated = patch(text)
    backup = path.with_suffix(path.suffix + ".pre-v85.bak")
    if not backup.exists():
        backup.write_bytes(raw)
    path.write_text(updated, encoding="utf-8")

    print(f"Patched {path}")
    print(f"Backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
