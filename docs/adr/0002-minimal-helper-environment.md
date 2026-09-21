# 0002 — A minimal environment for the helper and for bluetoothctl

## Context

The bar shell is long-lived and inherits whatever environment started it, which
on a desktop can include a user-writable `PATH`, locale variables that change
parsed output, and `PYTHON*` knobs. The helper parses `bluetoothctl` output, so
a hijacked binary or a different locale is a correctness and security problem,
not just a preference.

## Decision

`Service.qml` runs every helper process with `clearEnvironment: true` and
passes only `HOME`, `XDG_RUNTIME_DIR`, `XDG_CACHE_HOME` and
`SONY_HEADPHONES_DEMO`, plus a fixed `PATH=/usr/bin:/bin` and
`SONY_HEADPHONES_LOG` set from the widget's logging setting. Inside the helper,
`bluetoothctl` is resolved once at import from an absolute candidate list
(`/usr/bin`, `/bin`, `/usr/local/bin`) and run with
`env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}`.

## Consequences

- Locale and binary resolution are deterministic; output parsing cannot break
  because of an inherited `LC_ALL` or a shadowed executable.
- The helper cannot be configured through arbitrary environment variables.
  Every variable the widget passes is named in `Service.qml` and in the tests,
  which read the QML as text to keep the two launch lines in step.
  `SONY_HEADPHONES_TRACE` is the deliberate exception: it is read only when the
  helper is run by hand from the command line, never passed by the widget,
  whose daemon would read a trace on stderr as an error message.
- Debugging a running widget means changing the plugin setting or the daemon's
  logging level over the socket, not exporting a variable into the shell.
