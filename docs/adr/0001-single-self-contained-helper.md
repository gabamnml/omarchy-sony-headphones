# 0001 — One self-contained helper, launched with the system interpreter

## Context

The plugin has to speak Sony's RFCOMM protocol from an Omarchy bar widget. A
Python package with dependencies would need an install step, a virtualenv or
system packages the user may not have, and the widget's hot-reload makes a
multi-file runtime fragile. Nothing may be fetched at runtime.

## Decision

All of the protocol lives in one runnable file, `bin/sony-headphones`, using
only the standard library and `bluetoothctl`. Its shebang is
`/usr/bin/python3`, and `Service.qml` always launches it as
`["/usr/bin/python3", "-I", helperPath]` — never a `python3` resolved through
`PATH`, and never with `PYTHON*` variables or user site-packages in play.

## Consequences

- Installing the plugin is copying the directory; there is no build or install
  step, and no dependency can drift out from under it.
- The helper is one large file (several thousand lines), navigated by its
  section banners rather than by modules. The test suite compensates by loading
  it by path and exercising each section directly.
- `-I` isolation means the helper cannot rely on the environment the shell
  happened to have; anything it needs must be passed explicitly (see ADR 0002).
