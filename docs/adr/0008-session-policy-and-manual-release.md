# 0008 — One control session with a hold/on-demand policy and a manual toggle

## Context

A Sony headset holds exactly one control session at a time. Whoever opens it
owns the headphones' attention: while this plugin's daemon holds the RFCOMM
link, the phone app paired over multipoint cannot take it, and while the
phone holds it, the widget's writes go nowhere. Before this decision the
daemon behaved as if the link were its own property — it connected at start,
reconnected on failure, and never let go. That is the right behaviour when
the widget is the only controller, and the wrong one when the user reaches
for their phone: the phone then fights a link that never yields, with no
visible explanation and no way to hand it over except stopping the widget.

The headphones also drop the session on their own after a while, announcing
nothing, so the daemon already had to distinguish a dead link (reconnect and
carry on) from a deliberate absence. Any release mechanism has to join that
distinction rather than blur it: the panel must still draw a device whose
session was handed over, and a command issued into a released session must do
the useful thing rather than fail in the user's hands.

## Decision

The daemon owns a session with three states — `held`, `connecting`,
`released` — and a policy with two values: `hold` (the default) keeps the
session open as it always has; `on-demand` releases it after `idleSeconds`
quiet seconds (default 30, whole seconds from 1 to 86400) so a phone can take
over. The idle timer measures only client commands that touched the device
(`Daemon.with_link` stamps `last_activity`); the periodic liveness poll and a
subscriber reading the state deliberately never feed it, or `on-demand` would
never fire. The timer is a timestamp comparison on the run loop's one-second
tick (`Daemon.release_if_idle`), not a thread and not a sleep.

Release is deliberate and never an error (`Daemon.release`): it closes the
transport, keeps the last known values so the panel still draws the device,
sets no error, and keeps the cached RFCOMM channel so reclaiming skips
discovery. Reclaim (`Daemon.reclaim`) returns through the normal connect path
with its backoff rules and refreshes; on an already-held link it is a no-op
success. Any device command on a released session reclaims implicitly, so the
user never has to press reclaim before changing a setting.

The reconnect backs off instead of fighting (`Daemon.retry_delay`,
`RETRY_MIN` 2 s to `RETRY_MAX` 30 s, `STABLE_AFTER` 10 s,
`Daemon.connected_since`). A link that held past `STABLE_AFTER` resets
the delay to the minimum; a link that dies young doubles it, so a phone
holding the one session is not re-challenged every few seconds. A
deliberate release always resets to the minimum. A busy RFCOMM open
(`EBUSY`) reports `another device may be using the control channel`
without burning the remaining candidates, and a set/cycle the confirmed
generation cannot carry is refused by `Daemon.precheck_setting` before
any link is opened, so a bad key never steals the session.

The policy boots from the environment (`SONY_HEADPHONES_SESSION`,
`SONY_HEADPHONES_SESSION_IDLE`), forwarded from the widget's `sessionPolicy`
and `idleSeconds` settings exactly the way the log level is, and a running
daemon is read and changed through the `session` socket command, whose values
are validated the same way socket input always is. The live policy, delay
and session stamp every state line, and the panel carries the session as its
own row for every connected device — `Session: held`, `Session: released` or
`Session: connecting` — flipping either way on activation. Under `on-demand`,
the widget's Bluetooth presence signal releases the session when the headset
leaves range and reclaims it on return, but only a release presence itself
caused; a release made by hand is never undone by a connect event, and `hold`
never releases on its own. The helper's own reconnect, backoff and poll stay
the fallback when the Bluetooth service is unavailable.

## Consequences

- The phone and the widget share the headset without stopping anything: the
  widget yields under `on-demand` and the user yields by hand under either
  policy, and both directions are one row or one command away.
- A released session is visible, not blank: the panel keeps the last reported
  values, the session row says `released`, and the next command just works.
- `on-demand` cannot starve itself: because polls and subscribes never count
  as activity, a quiet daemon always releases on schedule, and because any
  command reclaims, a released daemon always answers.
- A hand release is stable: presence events and the idle timer never override
  what the user asked for.
- The demo stand-in keeps its values across a release/reclaim the way a real
  headset's flash does, so the whole lifecycle is exercisable without
  hardware.
