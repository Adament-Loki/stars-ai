
# Design Lifecycle & Slot Management v2.10

Stars! has hard active-design limits:
- 16 ship designs
- 10 starbase designs

The AI therefore treats a design slot as a scarce strategic resource.

Obsolete does not mean worthless. Old ships can remain useful as:
- starbase defenders
- chaff/screens
- escorts
- raiders
- anti-freighter / anti-colonizer forces
- overmatch against older technology
- secondary-front forces

But a design that occupies one of the 16 ship slots can eventually become more
costly to keep than to expend/recycle.

Lifecycle dispositions:
- KEEP_FIRST_LINE
- KEEP_SECOND_LINE
- KEEP_SPECIALIZED
- EXPEND
- DELETE_WHEN_EMPTY
- RECYCLE
- PROTECT_SLOT

Example:

Legacy Destroyer
active: 43
obsolete: 0.82
secondary role value: 0.61
replacement value: 0.94
ship slots: 16/16

=> EXPEND

Stop building it, use remaining hulls productively, do not spend major resources
preserving the class, and recycle the slot after the surviving hull count reaches zero.

Unique specialist designs are protected until a replacement capability exists.
Starbase designs are evaluated independently against the 10-slot base limit.
