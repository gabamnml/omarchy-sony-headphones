# 0005 — Protocol-aware capability ceilings

## Context

The two command sets do not merely encode differently; they support different
controls. A WH-1000XM4 has no connection quality and a WH-1000XM6 has no touch
panel. Sending a command a model ignores is harmless on the wire but leaves a
dead control on screen, and the panel had no way to know which controls were
real without hard-coding model checks everywhere.

## Decision

Features are data in two layers:

- `V1_FEATURES` and `V2_FEATURES` are the **ceilings**: every control a device
  speaking that protocol can carry.
- `FEATURE_SETS` refines a ceiling per known model. The refinement is validated
  at import against its own generation's ceiling, so a set that strays fails at
  startup rather than by sending an unrouteable opcode.

`features_for(name, protocol)` searches the confirmed protocol's models first,
falls back to that protocol's ceiling for an unrecognised device, and offers
the union only when no protocol is known at all. The confirmed init handshake
replaces the `bluetoothctl` hint and is what feeds this call. The same feature
list then gates the panel rows (`Model.rowsFor`) and the setting builders; the
builders also refuse `V1_ONLY_SETTINGS`/`V2_ONLY_SETTINGS` directly, so a
stale feature list cannot let a write cross protocols.

## Consequences

- Adding a model is a data edit plus a test; no wire logic changes.
- An unknown device of a known protocol is offered everything that protocol
  can do, not everything the plugin can do.
- The panel can never draw a row the device will ignore, and the CLI rejects
  the setting with a message instead of sending it into the void.
