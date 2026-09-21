// Pure helpers for the Sony headphones widget: no QML objects, no side
// effects, so they can be reasoned about (and read) on their own.
.pragma library

var MAX_AMBIENT_LEVEL = 20

var MODE_LABELS = {
  "noise-cancelling": "Noise cancelling",
  "ambient-sound": "Ambient sound",
  "wind-noise-reduction": "Wind noise reduction",
  "off": "Off"
}

// Nerd Font glyphs (md-headphones, md-ear_hearing, md-headphones_off): solid
// headphones while noise cancelling, an ear for ambient sound, and a struck
// through pair when the processing is off or nothing is connected — those two
// are told apart by colour and by the battery reading next to the icon.
var ICON_HEADPHONES = "󰋋"
var ICON_EAR = "󰟅"
var ICON_OFF = "󰟎"

var EQ_PRESETS = [
  { value: "off", label: "Off" },
  { value: "bright", label: "Bright" },
  { value: "excited", label: "Excited" },
  { value: "mellow", label: "Mellow" },
  { value: "relaxed", label: "Relaxed" },
  { value: "vocal", label: "Vocal" },
  { value: "treble-boost", label: "Treble boost" },
  { value: "bass-boost", label: "Bass boost" },
  { value: "speech", label: "Speech" },
  { value: "manual", label: "Manual" }
]

// The v2 generation dropped the classic preset table: firmware accepts only
// these codes, plus Custom and Off.
var EQ_PRESETS_V2 = [
  { value: "off", label: "Off" },
  { value: "heavy", label: "Heavy" },
  { value: "clear", label: "Clear" },
  { value: "hard", label: "Hard" },
  { value: "soft", label: "Soft" },
  { value: "custom", label: "Custom" }
]

function eqPresets(protocol) {
  return protocol === "v2" ? EQ_PRESETS_V2 : EQ_PRESETS
}

var AUTO_POWER_OFF = [
  { value: "off", label: "Never" },
  { value: "when-taken-off", label: "When taken off" },
  { value: "5-min", label: "After 5 minutes" },
  { value: "30-min", label: "After 30 minutes" },
  { value: "1-hour", label: "After 1 hour" },
  { value: "3-hour", label: "After 3 hours" }
]

var STC_SENSITIVITY = [
  { value: "auto", label: "Auto" },
  { value: "high", label: "High" },
  { value: "low", label: "Low" }
]

var STC_TIMEOUT = [
  { value: "short", label: "Short" },
  { value: "standard", label: "Standard" },
  { value: "long", label: "Long" },
  { value: "off", label: "Until I stop" }
]

// Trade bitrate for a stronger link, or the other way around.
var CONNECTION_QUALITY = [
  { value: "sound-quality", label: "Sound quality (LDAC)" },
  { value: "stable", label: "Stable connection" }
]

// How the headphones colour the sound: plain, or tuned for what is around you.
var LISTENING_MODE = [
  { value: "standard", label: "Standard" },
  { value: "background-music", label: "Background music" },
  { value: "cinema", label: "Cinema" }
]

// Only meaningful while listening mode is background music.
var BGM_ROOM_SIZE = [
  { value: "my-room", label: "My Room" },
  { value: "living-room", label: "Living Room" },
  { value: "cafe", label: "Cafe" }
]

// The timer options exist only on models that honour them; a WH-1000XM4
// answers "when taken off" whatever timer you ask for.
function autoPowerOffOptions(features) {
  if (features && features.indexOf("auto-power-off-timer") !== -1) return AUTO_POWER_OFF
  return AUTO_POWER_OFF.slice(0, 2)
}

function supports(features, feature) {
  return !!features && features.indexOf(feature) !== -1
}

// The Bluetooth name families that count as a Sony headset when no address is
// pinned. BlueZ reports names lower case, but the rule lowercases anyway so it
// does not depend on that.
function looksLikeSony(name) {
  var text = String(name || "").toLowerCase()
  return text.indexOf("sony") !== -1
    || /^(wh|wf|wi)-/.test(text)
    || text.indexOf("linkbuds") !== -1
}

// The presence rule behind the widget's `present`: is the headset on the link
// right now? `devices` is a plain list of { address, name, connected }, already
// flattened from Quickshell's Bluetooth service by Service.qml. With a pinned
// `pinnedAddress` the name is ignored and only that exact MAC counts, compared
// case-insensitively (BlueZ reports lower case). With no address pinned the name
// is the only signal, so any connected device in Sony's headset families counts:
// the WH-/WF-/WI- product lines (WH-1000XM*, WF-1000XM*, WH-CH*, WF-C*…) and the
// LinkBuds family, plus any name mentioning "Sony". It is deliberately broad,
// because a missed match only means the helper's own reconnect and poll decide
// instead.
function present(devices, pinnedAddress) {
  var pinned = String(pinnedAddress || "").toLowerCase()
  var list = devices || []
  for (var i = 0; i < list.length; i++) {
    var device = list[i]
    if (!device || !device.connected) continue
    if (pinned !== "") {
      if (String(device.address || "").toLowerCase() === pinned) return true
    } else if (looksLikeSony(device.name)) {
      return true
    }
  }
  return false
}

// The daemon keeps the last reported values in state while it deliberately
// releases the control session, so a released device is still worth drawing;
// a hard disconnect (session "connecting", nothing retained) is not.
function hasState(state) {
  return !!state && (!!state.connected || state.session === "released")
}

// The rows the panel draws, in order. A row exists only while the model
// honours it and the current state makes it meaningful, so the panel binds
// visibility to this list instead of repeating the conditions row by row.
function rowsFor(state) {
  if (!hasState(state)) return []
  var features = state.features || []
  var mode = state.nc_mode || "off"
  var rows = ["mode"]
  if (mode === "ambient-sound" || mode === "off") rows.push("level", "focus")
  if (supports(features, "equalizer")) rows.push("eq")
  if (supports(features, "listening-mode")) {
    rows.push("listening-mode")
    if (state.listening_mode === "background-music") rows.push("bgm-room-size")
  }
  if (supports(features, "dsee")) rows.push("dsee")
  if (supports(features, "connection-quality")) rows.push("connection-quality")
  if (supports(features, "speak-to-chat")) {
    rows.push("stc")
    if (state.speak_to_chat) {
      rows.push("stc-sensitivity", "stc-timeout")
      if (supports(features, "speak-to-chat-focus")) rows.push("stc-focus")
    }
  }
  if (supports(features, "pause-when-taken-off")) rows.push("pause")
  if (supports(features, "touch-sensor")) rows.push("touch")
  if (supports(features, "voice-notifications")) rows.push("voice")
  if (supports(features, "auto-power-off")) rows.push("apo")
  // The control session belongs to the daemon, not the headphones, so its row
  // is offered for every connected device whatever features it reports.
  rows.push("session")
  if (supports(features, "multipoint") && connectedDeviceCount(state) > 0) rows.push("playback-source")
  return rows
}

function hasRow(rows, key) {
  return !!rows && rows.indexOf(key) !== -1
}

// A peer can be listed but offline; only a connected one can be the source.
function connectedDeviceCount(state) {
  var devices = (state && state.devices) || []
  var count = 0
  for (var i = 0; i < devices.length; i++) {
    if (devices[i].connected) count++
  }
  return count
}

// The multipoint peers the panel offers as a playback source: connected ones
// only, named by their friendly name where there is one.
function deviceOptions(state) {
  var devices = (state && state.devices) || []
  var options = []
  for (var i = 0; i < devices.length; i++) {
    if (!devices[i].connected) continue
    options.push({ value: devices[i].address, label: devices[i].name || devices[i].address })
  }
  return options
}

// The peer currently playing: the source the device reported wins, else the
// peer the list flagged as playing.
function playbackSource(state) {
  if (state && state.playback_source) return state.playback_source
  var devices = (state && state.devices) || []
  for (var i = 0; i < devices.length; i++) {
    if (devices[i].playback) return devices[i].address
  }
  return ""
}

function modeLabel(mode) {
  return MODE_LABELS[mode] || "Unknown"
}

function modeIcon(mode, connected) {
  if (!connected || mode === "off") return ICON_OFF
  if (mode === "ambient-sound" || mode === "wind-noise-reduction") return ICON_EAR
  return ICON_HEADPHONES
}

// The cycle the earcup button walks, so clicking the widget and tapping the
// headphones land in the same place.
function nextMode(mode) {
  var order = ["noise-cancelling", "ambient-sound", "off"]
  var index = order.indexOf(mode)
  return index === -1 ? order[0] : order[(index + 1) % order.length]
}

function clampLevel(level) {
  return Math.max(0, Math.min(MAX_AMBIENT_LEVEL, Math.round(level)))
}

// The helper's log file: nothing, only rejected commands, or everything it
// does. The order is the cycle the panel's link walks.
var LOG_LEVELS = ["off", "errors", "all"]

function nextLogging(current) {
  var level = LOG_LEVELS.indexOf(current) === -1 ? "errors" : current
  return LOG_LEVELS[(LOG_LEVELS.indexOf(level) + 1) % LOG_LEVELS.length]
}

function loggingLabel(current) {
  var level = LOG_LEVELS.indexOf(current) === -1 ? "errors" : current
  if (level === "off") return "no logging"
  if (level === "all") return "all logging"
  return "only error logging"
}

// Whether the daemon currently holds the headset's one control session. The
// daemon stamps the live value on every state line; a missing key falls back to
// the neutral "held" a bare link reports (see initial_state in the helper).
var SESSION_LABELS = {
  "held": "Session: held",
  "released": "Session: released",
  "connecting": "Session: connecting"
}

// The policies the daemon accepts, mirrored from the helper so a hand-edited
// shell.json cannot hand it anything else.
var SESSION_POLICIES = ["hold", "on-demand"]

// The idle-delay bounds the daemon accepts, mirrored from the helper
// (SESSION_IDLE_MIN/MAX/DEFAULT): a hand-edited 0 or 200000 would otherwise
// reach the daemon and be refused as an illegal runtime command.
var SESSION_IDLE_MIN = 1
var SESSION_IDLE_MAX = 24 * 60 * 60
var SESSION_IDLE_DEFAULT = 30

function clampSessionIdle(value) {
  var seconds = parseInt(value, 10)
  if (isNaN(seconds)) return SESSION_IDLE_DEFAULT
  return Math.max(SESSION_IDLE_MIN, Math.min(SESSION_IDLE_MAX, seconds))
}

function sessionLabel(state) {
  var session = state && state.session
  return SESSION_LABELS[session] || SESSION_LABELS.held
}

function batteryText(battery, charging) {
  if (battery === null || battery === undefined) return ""
  return battery + "%" + (charging ? " ⚡" : "")
}

function deviceLabel(state) {
  if (!state) return "Sony headphones"
  return state.name || state.address || "Sony headphones"
}

// One line under the device name: what it is doing right now.
function heroMeta(state) {
  if (!hasState(state)) return state && state.error ? "Disconnected" : "Not connected"
  var parts = [modeLabel(state.nc_mode)]
  if (state.nc_mode === "ambient-sound" && state.ambient_level !== null && state.ambient_level !== undefined)
    parts[0] = "Ambient " + state.ambient_level + "/" + MAX_AMBIENT_LEVEL
  var battery = batteryText(state.battery, state.charging)
  if (battery !== "") parts.push(battery)
  return parts.join(" · ")
}

function barText(state, showBattery, vertical) {
  var icon = modeIcon(state ? state.nc_mode : null, hasState(state))
  if (!showBattery || vertical || !hasState(state)) return icon
  var battery = batteryText(state.battery, false)
  return battery === "" ? icon : battery + " " + icon
}

function tooltip(state) {
  if (!hasState(state)) return state && state.error ? state.error : "Sony headphones not connected"
  return deviceLabel(state) + " — " + heroMeta(state)
}

function optionLabel(options, value) {
  for (var i = 0; i < options.length; i++) {
    if (options[i].value === value) return options[i].label
  }
  return value || ""
}

// -- write-then-verify ------------------------------------------------------

// A row is a UI name ("focus", "stc") while the helper keys pending/refused by
// the setting it was asked to change ("focus-on-voice", "speak-to-chat"), so a
// row translates before it reads either map. A few names match already.
var ROW_SETTING_KEYS = {
  "mode": "nc",
  "level": "ambient-level",
  "focus": "focus-on-voice",
  "eq": "eq",
  "listening-mode": "listening-mode",
  "bgm-room-size": "bgm-room-size",
  "dsee": "dsee",
  "connection-quality": "connection-quality",
  "stc": "speak-to-chat",
  "stc-sensitivity": "stc-sensitivity",
  "stc-timeout": "stc-timeout",
  "stc-focus": "stc-focus-on-voice",
  "pause": "pause-when-taken-off",
  "touch": "touch-sensor",
  "voice": "voice-notifications",
  "apo": "auto-power-off",
  "playback-source": "playback-source"
}

function settingKeyForRow(rowKey) {
  return ROW_SETTING_KEYS[rowKey] || rowKey
}

// True while the helper has written the setting behind `rowKey` but the
// device's own read-back has not moved yet. A missing pending list reads as
// empty, so older helpers stay drawable.
function isPending(state, rowKey) {
  var pending = state && state.pending
  return !!pending && pending.indexOf(settingKeyForRow(rowKey)) !== -1
}

// The reason the headphones refused the setting behind `rowKey`, or "" when it
// was accepted. A missing refused map reads as empty.
function refusedReason(state, rowKey) {
  var refused = state && state.refused
  var reason = refused ? refused[settingKeyForRow(rowKey)] : ""
  return reason ? String(reason) : ""
}

// Every refusal on the current state, one reason per sentence, for the panel's
// quiet refusal line. Empty when nothing has been refused.
function refusedSummary(state) {
  var refused = (state && state.refused) || {}
  var parts = []
  for (var name in refused) {
    if (refused[name]) parts.push(String(refused[name]))
  }
  return parts.join("; ")
}
