# Sony Headphones for Omarchy — wiki

A bar widget for the [Omarchy](https://omarchy.org) shell that controls Sony
WH-1000X headphones the way the phone app does — noise cancelling, ambient
sound, the equalizer, DSEE Extreme, Speak-to-Chat and battery — without a
phone, a vendor app, or a desktop GUI. It talks Sony's own Bluetooth protocol
directly over RFCOMM, in one self-contained Python file depending only on the
standard library and `bluetoothctl`.

This wiki is the reader-facing layer: how to use the plugin, how it is built,
and what an agent needs to change it. The historical layer — the architecture
decision records — sits beside it in [`../adr/`](../adr/) and is kept, not
replaced.

## Pages

| Page | For | What it covers |
|---|---|---|
| [usage.md](usage.md) | Owners | Install, the bar gestures and panel, every setting in plain language, the control session, troubleshooting and logs |
| [architecture.md](architecture.md) | Contributors | The widget/helper split, the RFCOMM framing and ACK discipline, the v1/v2 seams, capability ceilings, security posture, the test gate |
| [../agents.md](../agents.md) | Agents | File roles, the full helper symbol map, the wire protocol, every setting's per-model availability, and how to verify a change |

## The historical layer

- [../adr/](../adr/) holds the architecture decision records (ADR 0001–0009):
  the security invariants, the two seams, the capability model, the session
  policy and the threat model. These are the source of truth for wire-format
  and security rules.

## Direction

The owner-facing entry point is the repository [README](../../README.md). The
plugin metadata, install command and settings schema are in
[`manifest.json`](../../manifest.json). Contributions arrive as pull requests
against the upstream repository; the layout and agent workflow are described
in [the agent reference](../agents.md).
