# 0007 — Local-only rotating logging with runtime levels

## Context

When a setting does not take or the link drops, the frame trace is a byte-level
view of one run, but there was no durable record to work out a puzzle after
the fact. Logging has two hard constraints: stdout is parsed as JSON by the
shell, and the widget reads the helper's stderr as an error message, so neither
stream is available. The log also must not grow without bound or leak a
Bluetooth address.

## Decision

Use the standard library's logging with one named logger (`sony-headphones`,
never the root logger) and one `RotatingFileHandler` in the private cache
directory: `sony-headphones.log`, mode 0600, rotated at 256 KiB with two
backups. The handler opens the file `O_NOFOLLOW` and checks ownership; the
formatter redacts Bluetooth addresses. The level is one of `off`, `errors`
(the default) or `all`, read at startup from `SONY_HEADPHONES_LOG` and
changeable at runtime over the control socket (`configure_logging` only moves
the handler's level). The level rides in the state so the panel can read and
cycle it and a fresh daemon starts where the widget setting says.

## Consequences

- There is always, by default, an on-disk record of failures without any
  configuration, and it can never grow past roughly 768 KiB.
- A logging failure can never corrupt the conversation: a file that cannot be
  opened falls back to a `NullHandler`, `raiseExceptions` is off, and nothing
  is ever written to stdout or stderr.
- The byte-level `SONY_HEADPHONES_TRACE` channel remains separate and opt-in;
  the log is human-readable and redacted, not a raw frame dump.
