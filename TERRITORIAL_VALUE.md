
# Territorial Value / Sunk-Cost Model v2.5

Owned planets are not treated equally.

The AI estimates:
- population at risk
- factories/mines/defenses already invested
- mineral stock/concentrations
- starbase/scanner/strategic value
- distance from the empire core
- integration with the logistics network
- irreplaceability (homeworld, major population hub, starbase hub)

This produces:
- `total_value`
- `defense_priority`
- `abandonability`
- `escalation_priority`

Examples:

Remote 5k-pop fringe colony under attack:
- low sunk cost
- low core proximity
- high abandonability
- weak justification for escalating into a major war

Homeworld / major core world under attack:
- high population and industrial sunk cost
- high irreplaceability
- species/economic survival risk
- high-priority defense and diplomatic escalation

The conflict planner should use `escalation_priority` and `territorial_loss_penalty()`
when deciding whether an incident warrants hostility or a full war.
