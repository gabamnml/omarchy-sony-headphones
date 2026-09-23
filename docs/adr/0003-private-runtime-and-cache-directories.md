# 0003 — Private runtime and cache directories, opened without following symlinks

## Context

The control socket is an authority boundary: anything that can write it can
drive the headphones and read their state. `XDG_RUNTIME_DIR` is inherited and
can point anywhere; the old fallback `/tmp/user-<uid>` is a predictable name an
attacker on a shared machine can create first. The channel cache decides where
the next connection is dialled, and the log file is written on every daemon
run, so both are worth protecting too.

## Decision

- A directory is usable only if `lstat` says it is a real directory owned by
  this user with mode exactly 0700. A supplied `XDG_RUNTIME_DIR` is checked
  rather than trusted; otherwise the helper creates its own directory under the
  temporary directory and verifies it the same way, refusing to proceed if it
  cannot.
- The lock is created relative to a descriptor for the verified directory,
  opened `O_NOFOLLOW` and never truncated; the stale-socket check and unlink
  are descriptor-relative too. The socket is bound through that same
  descriptor, at `/proc/self/fd/<dir_fd>/<socket>`, so the inode the kernel
  binds is the inode the ownership and mode check covered even if the path is
  swapped for a symlink between the check and the bind. A stale socket is
  unlinked only after confirming it is a socket owned by this user.
- The cache directory is created 0700, a loose one is tightened if it is ours,
  and a symlink or someone else's directory declines the cache entirely. The
  channel file is opened `O_NOFOLLOW` and read the same way.
- The log file is opened `O_NOFOLLOW`, checked to be a regular file owned by
  this user, and `fchmod`ed to 0600.

## Consequences

- A hostile local user cannot create the runtime or cache path first, redirect
  a write through a symlink, or read the log.
- Security failures degrade rather than crash where possible: a cache that
  cannot be made private is skipped and SDP discovery simply runs again.
- The socket cannot be shared across users by design; `XDG_RUNTIME_DIR` is per
  user, which is exactly the intended audience.
