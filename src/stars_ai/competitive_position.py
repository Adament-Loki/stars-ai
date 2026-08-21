from __future__ import annotations

from typing import Any


def evaluate_competitive_position(state: Any) -> dict[str, Any]:
    """Turn current Stars! scores into a legal strategic catch-up signal.

    Private-score games retain the AI's own score for trend reporting, but do
    not infer rival totals.  In public-score games all supplied player records
    are visible and may increase expansion pressure when we fall behind.
    """
    native=getattr(state, "native", {}) or {}
    records=[
        dict(record) for record in (native.get("player_scores") or [])
        if int(record.get("player_id", 0) or 0)>0
    ]
    ours=next(
        (record for record in records if int(record["player_id"])==int(state.player_id)),
        None,
    )
    public=str(native.get("score_visibility", "private")).lower()=="public"
    if not public:
        return {
            "visibility":"private",
            "our_score":int((ours or {}).get("score", 0) or 0),
            "our_rank":int((ours or {}).get("rank", 0) or 0),
            "known_scores":1 if ours else 0,
            "catch_up_pressure":1.0,
            "status":"SELF_SCORE_ONLY",
        }

    rivals=[record for record in records if int(record["player_id"])!=int(state.player_id)]
    leader=max(records,key=lambda record:int(record.get("score", 0) or 0),default=None)
    our_score=int((ours or {}).get("score", 0) or 0)
    leader_score=int((leader or {}).get("score", 0) or 0)
    ratio=our_score/max(1,leader_score)
    if leader is None or int(leader["player_id"])==int(state.player_id):
        pressure=1.0
        status="LEADING"
    elif ratio<0.45:
        pressure=2.0
        status="SEVERELY_TRAILING"
    elif ratio<0.60:
        pressure=1.75
        status="MATERIALLY_TRAILING"
    elif ratio<0.80:
        pressure=1.45
        status="TRAILING"
    else:
        pressure=1.15
        status="COMPETITIVE"
    return {
        "visibility":"public",
        "our_score":our_score,
        "our_rank":int((ours or {}).get("rank", 0) or 0),
        "known_scores":len(records),
        "leader_player_id":int((leader or {}).get("player_id", 0) or 0),
        "leader_score":leader_score,
        "score_ratio_to_leader":round(ratio,3),
        "rival_count":len(rivals),
        "catch_up_pressure":pressure,
        "status":status,
    }
