import QtQuick
import Quickshell
import Quickshell.Bluetooth
import Quickshell.Io
import "Model.js" as Model

// Owns the conversation with the headphones. The helper's `watch` mode holds
// the Bluetooth link open and prints one JSON object per state change, so this
// is mostly a matter of keeping that process alive and folding its lines in.
Item {
  id: root

  property string helperPath: ""
  property string address: ""
  // The plugin setting the helper boots from; the panel may change the running
  // daemon's level, but a fresh daemon starts here again.
  property string logging: "errors"
  // The control-session policy and idle delay. Like the log level, a fresh
  // daemon boots from these and a running one is changed through setSession.
  property string sessionPolicy: "hold"
  property int idleSeconds: 30

  property var state: ({ connected: false })
  property string lastError: ""
  property bool starting: true

  readonly property string mode: String(state.nc_mode || "off")
  readonly property int ambientLevel: state.ambient_level === null || state.ambient_level === undefined
    ? Model.MAX_AMBIENT_LEVEL : state.ambient_level
  readonly property bool focusOnVoice: !!state.focus_on_voice
  readonly property bool supportsWind: state.supports_wind !== false
  readonly property string session: String(state.session || "held")
  // A deliberate release keeps the last reported values, so the panel and the
  // bar keep drawing a device: `showing` is the predicate for presenting one.
  readonly property bool showing: Model.hasState(state)
  readonly property bool busy: setProcess.running || sessionProcess.running

  // True while the pinned headset is connected at the Bluetooth level. It is
  // read from Quickshell's Bluetooth service so connect/disconnect arrive as
  // events rather than through the helper's poll. This is an extra signal for
  // the release/reclaim flow, not a replacement for the helper: the helper's
  // state is still what says what the device is and can do, and its
  // reconnect/backoff/poll stay the fallback when the service is unavailable.
  readonly property bool present: Model.present(root.bluetoothDevices(), root.address)

  // Flatten Quickshell's Bluetooth devices into the plain objects the pure
  // Model.present rule takes. Reading each device's connected/address/name here,
  // while `present` evaluates, is what makes the binding re-run on a connect or
  // disconnect — the decision itself lives in Model.js so the node harness can
  // test it.
  function bluetoothDevices() {
    var model = Bluetooth.devices ? Bluetooth.devices.values : []
    var devices = []
    for (var i = 0; i < model.length; i++) {
      var device = model[i]
      if (!device) continue
      devices.push({
        address: device.address,
        name: device.deviceName || device.name,
        connected: device.connected
      })
    }
    return devices
  }

  // Whether the session currently released was released because presence was
  // lost. Only that release is reclaimed when the headset comes back; a release
  // the user asked for is never undone by a presence gain.
  property bool presenceReleased: false

  signal changed()

  // The shell is long-lived and inherits whatever environment started it, so
  // neither the interpreter nor its environment is taken from there: python3 is
  // bound by absolute path, run isolated (-I: no PYTHON* variables, no user
  // site-packages, no script directory on sys.path), and handed only the
  // variables the helper reads.
  readonly property string interpreter: "/usr/bin/python3"
  readonly property var helperEnvironment: {
    var env = { PATH: "/usr/bin:/bin" }
    var passed = ["HOME", "XDG_RUNTIME_DIR", "XDG_CACHE_HOME", "SONY_HEADPHONES_DEMO"]
    for (var i = 0; i < passed.length; i++) {
      var value = Quickshell.env(passed[i])
      if (value !== undefined && value !== null && String(value) !== "") env[passed[i]] = String(value)
    }
    // The plugin setting, not the shell's environment, picks the starting
    // level: a level chosen here then survives a restart.
    env.SONY_HEADPHONES_LOG = root.logging
    // The session policy is a daemon setting in the same way: a fresh daemon
    // boots from these, a running one is changed through setSession.
    env.SONY_HEADPHONES_SESSION = root.sessionPolicy
    env.SONY_HEADPHONES_SESSION_IDLE = String(root.idleSeconds)
    return env
  }

  function argv(args) {
    var command = [interpreter, "-I", helperPath]
    if (address !== "") command = command.concat(["--address", address])
    return command.concat(args)
  }

  // Paint the change immediately and let the device's own notification correct
  // us a moment later — the headphones always get the last word.
  function optimistic(patch) {
    var next = {}
    for (var key in state) next[key] = state[key]
    for (var patched in patch) next[patched] = patch[patched]
    state = next
    changed()
  }

  // The optimistic half of write-then-verify: the written setting joins the
  // pending list (and drops any earlier refusal), so the row shows it as in
  // flight until the helper's own state line confirms or refuses it.
  function pendingPatch(key) {
    var pending = (state.pending || []).slice()
    if (pending.indexOf(key) === -1) pending.push(key)
    var refused = {}
    for (var name in (state.refused || {})) {
      if (name !== key) refused[name] = state.refused[name]
    }
    return { pending: pending, refused: refused }
  }

  function set(key, value, patch) {
    // A write while the session is released is allowed: the daemon reclaims
    // automatically, which is exactly the "any command reclaims" promise.
    if (!showing || helperPath === "") return
    var next = pendingPatch(key)
    for (var name in (patch || {})) next[name] = patch[name]
    optimistic(next)
    // One spawn: the --json reply carries the authoritative state, so the
    // widget parses it rather than running a second status for it.
    setProcess.command = argv(["--json", "set", key, String(value)])
    setProcess.running = true
  }

  function cycleMode() {
    var next = Model.nextMode(mode)
    set("nc", next, { nc_mode: next })
  }

  function setMode(next) {
    if (next !== mode) set("nc", next, { nc_mode: next })
  }

  function setAmbientLevel(level) {
    var clamped = Model.clampLevel(level)
    var patch = { ambient_level: clamped }
    // The helper switches to ambient sound when the level moves; mirror that
    // here so the panel does not flicker back to "noise cancelling" first.
    if (mode !== "ambient-sound" && mode !== "off") patch.nc_mode = "ambient-sound"
    set("ambient-level", clamped, patch)
  }

  function toggle(key, stateKey) {
    var patch = {}
    patch[stateKey] = !state[stateKey]
    set(key, patch[stateKey] ? "on" : "off", patch)
  }

  function choose(key, stateKey, value) {
    var patch = {}
    patch[stateKey] = value
    set(key, value, patch)
  }

  // The log level lives in the daemon, not the headphones, so this works
  // whether or not a device is connected.
  function setLogging(level) {
    if (helperPath === "") return
    optimistic({ logging: level })
    loggingProcess.command = argv(["--json", "logging", String(level)])
    loggingProcess.running = true
  }

  // The control session belongs to the daemon too, so releasing and reclaiming
  // work without the headphones being connected. Paint the switch immediately
  // and let the daemon's next state line be authoritative.
  function release() {
    if (helperPath === "") return
    // A release the user asked for is not a presence release: a later presence
    // gain must leave it alone.
    presenceReleased = false
    optimistic({ session: "released" })
    sessionProcess.command = argv(["--json", "release"])
    sessionProcess.running = true
  }

  function reclaim() {
    if (helperPath === "") return
    presenceReleased = false
    optimistic({ session: "connecting" })
    sessionProcess.command = argv(["--json", "reclaim"])
    sessionProcess.running = true
  }

  // Push a runtime policy change to the running daemon, matching how logging
  // does it; the value crosses the socket as {"cmd":"session", ...}.
  function setSession(policy, idle) {
    if (helperPath === "") return
    optimistic({ session_policy: policy, session_idle: idle })
    sessionProcess.command = argv(["--json", "session", String(policy), "--idle", String(idle)])
    sessionProcess.running = true
  }

  // A settings change reaches the daemon only once its state has arrived, and
  // only when it differs, so startup never spawns a redundant session call.
  function pushSession() {
    if (helperPath === "" || state.session_policy === undefined) return
    if (state.session_policy === sessionPolicy && state.session_idle === idleSeconds) return
    setSession(sessionPolicy, idleSeconds)
  }

  onSessionPolicyChanged: root.pushSession()
  onIdleSecondsChanged: root.pushSession()

  // The event half of the session policy. When the headset leaves, an
  // on-demand daemon releases the one control session so a phone can take it;
  // when it returns, we reclaim — but only the release presence caused. A
  // release the user made by hand, or the daemon's own idle release, leaves
  // `presenceReleased` false and is never undone here. `hold` does nothing
  // automatically; the manual toggle stands. This is a property change, not a
  // timer, so no new polling is added.
  onPresentChanged: {
    if (sessionPolicy !== "on-demand") return
    if (!present) {
      if (session === "released") return
      release()
      presenceReleased = true
    } else if (presenceReleased) {
      reclaim()
    }
  }

  function refresh() {
    if (helperPath === "") return
    refreshProcess.command = argv(["--json", "status"])
    refreshProcess.running = true
  }

  function applyLine(line) {
    var text = String(line || "").trim()
    if (text === "" || text.charAt(0) !== "{") return
    try {
      state = JSON.parse(text)
      starting = false
      lastError = String(state.error || "")
      changed()
    } catch (e) {
      // A partial line is not worth surfacing; the next one will be whole.
    }
  }

  onHelperPathChanged: if (helperPath !== "") watchProcess.running = true

  Process {
    id: watchProcess
    running: false
    clearEnvironment: true
    environment: root.helperEnvironment
    command: root.argv(["watch"])
    stdout: SplitParser { onRead: function(line) { root.applyLine(line) } }
    stderr: SplitParser { onRead: function(line) {
      var text = String(line || "").trim()
      if (text !== "") root.lastError = text
    } }
    onExited: {
      root.starting = false
      // The daemon exits when the helper cannot run at all, or when its own
      // socket goes away. Either way, come back rather than going quiet.
      restartTimer.restart()
    }
  }

  Process {
    id: setProcess
    running: false
    clearEnvironment: true
    environment: root.helperEnvironment
    command: []
    stdout: SplitParser { onRead: function(line) { root.applyLine(line) } }
    stderr: SplitParser { onRead: function(line) {
      var text = String(line || "").trim()
      if (text !== "") root.lastError = text
    } }
    // The --json reply is authoritative: applyLine folds the new state in and
    // clears (or carries) the error, so a successful action costs exactly one
    // helper — no follow-up status. Only a failure refreshes, to recover a
    // state the reply left uncertain; the stderr handler still runs so a
    // human-readable message reaches lastError.
    onExited: function(exitCode) {
      if (exitCode === 0) root.lastError = ""
      else root.refresh()
    }
  }

  Process {
    id: loggingProcess
    running: false
    clearEnvironment: true
    environment: root.helperEnvironment
    command: []
    stdout: SplitParser { onRead: function(line) { root.applyLine(line) } }
    stderr: SplitParser { onRead: function(line) {
      var text = String(line || "").trim()
      if (text !== "") root.lastError = text
    } }
    // Same one-spawn rule as setProcess: the --json logging reply is the
    // authoritative state, and only a failure falls back to a status refresh.
    onExited: function(exitCode) {
      if (exitCode === 0) root.lastError = ""
      else root.refresh()
    }
  }

  Process {
    id: sessionProcess
    running: false
    clearEnvironment: true
    environment: root.helperEnvironment
    command: []
    stdout: SplitParser { onRead: function(line) { root.applyLine(line) } }
    stderr: SplitParser { onRead: function(line) {
      var text = String(line || "").trim()
      if (text !== "") root.lastError = text
    } }
    // The --json reply is authoritative: applyLine folds the new session into
    // state and clears (or carries) the error, so a successful action costs
    // exactly one helper — no follow-up status. Only a failure refreshes, to
    // recover a state the reply left uncertain; the stderr handler still runs
    // so a human-readable message reaches lastError.
    onExited: function(exitCode) {
      if (exitCode === 0) root.lastError = ""
      else root.refresh()
    }
  }

  Process {
    id: refreshProcess
    running: false
    clearEnvironment: true
    environment: root.helperEnvironment
    command: []
    stdout: SplitParser { onRead: function(line) { root.applyLine(line) } }
  }

  Timer {
    id: restartTimer
    interval: 5000
    repeat: false
    onTriggered: if (root.helperPath !== "") watchProcess.running = true
  }

  Component.onCompleted: if (helperPath !== "") watchProcess.running = true

  // Saving a file hot-reloads the plugin, which builds a new Service; without
  // this the old one's helper lingers, and a pile of them race to own the
  // Bluetooth link.
  Component.onDestruction: {
    restartTimer.stop()
    watchProcess.running = false
  }
}
