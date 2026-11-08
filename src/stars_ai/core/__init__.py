"""Game-state contracts, the legacy agent wrapper, memory, and personas.

Import the submodules directly (``stars_ai.core.models``, ``stars_ai.core.agent``,
``stars_ai.core.memory``, ``stars_ai.core.memory_services``, and
``stars_ai.core.persona``). This package initializer stays empty so loading a
contract does not pull the turn engine back through the compatibility shims.

Strategy, native encoding, and the hosted-game lifecycle stay outside this
package. Existing imports from ``stars_ai.models``, ``stars_ai.agent``,
``stars_ai.memory``, ``stars_ai.memory_services``, and ``stars_ai.persona``
remain valid through those shims.
"""
