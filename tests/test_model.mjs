// Tests for Model.js, the panel's decisions with no QML engine around them.
//
// Model.js is written for the QML engine, whose only intrusion is the
// `.pragma library` line: stripped, the rest is plain ECMAScript, so it can
// be wrapped in a function and asked for the handful of names under test.
//
// Run with: node tests/test_model.mjs   (or: deno run --allow-read tests/test_model.mjs)

import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, join } from "node:path"
import assert from "node:assert/strict"

const here = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(here, "..", "Model.js"), "utf8")
  .replace(/^\.pragma library\s*$/m, "")

const names = [
  "MAX_AMBIENT_LEVEL", "LOG_LEVELS", "rowsFor", "hasRow", "supports",
  "eqPresets", "autoPowerOffOptions", "nextLogging", "loggingLabel",
  "nextMode", "clampLevel", "deviceOptions", "playbackSource", "sessionLabel",
  "present", "hasState", "SESSION_IDLE_MIN", "SESSION_IDLE_MAX",
  "SESSION_IDLE_DEFAULT", "clampSessionIdle", "settingKeyForRow", "isPending",
  "refusedReason", "refusedSummary"
]
const Model = new Function(source + `\nreturn { ${names.join(", ")} }`)()

let failures = 0

function test(name, body) {
  try {
    body()
    console.log("ok - " + name)
  } catch (error) {
    failures += 1
    console.error("not ok - " + name)
    console.error(String((error && error.stack) || error).split("\n").map(function(line) {
      return "    " + line
    }).join("\n"))
  }
}

// The rows a device speaks but the other generation does not, used to prove
// the two protocols never bleed into each other.
const V1_ONLY_ROWS = ["touch", "voice", "stc-focus"]
const V2_ONLY_ROWS = ["listening-mode", "bgm-room-size", "connection-quality", "playback-source"]

const XM4_FEATURES = [
  "battery", "equalizer", "dsee", "speak-to-chat", "speak-to-chat-focus",
  "pause-when-taken-off", "voice-notifications", "auto-power-off", "nc-optimizer"
]
const XM6_FEATURES = [
  "battery", "equalizer", "dsee", "connection-quality", "speak-to-chat",
  "pause-when-taken-off", "auto-power-off", "listening-mode", "multipoint"
]
const UNKNOWN_V1_FEATURES = [
  "battery", "equalizer", "dsee", "speak-to-chat", "speak-to-chat-focus",
  "pause-when-taken-off", "voice-notifications", "auto-power-off",
  "auto-power-off-timer", "touch-sensor", "nc-optimizer"
]
const UNKNOWN_V2_FEATURES = [
  "battery", "equalizer", "dsee", "connection-quality", "speak-to-chat",
  "pause-when-taken-off", "auto-power-off", "listening-mode", "multipoint"
]

const DEVICES = [
  { address: "AA:BB:CC:DD:EE:01", name: "Phone", connected: true, playback: true },
  { address: "AA:BB:CC:DD:EE:02", name: "Laptop", connected: false, playback: false }
]

function xm4(overrides) {
  return Object.assign({
    connected: true,
    protocol: "v1",
    nc_mode: "ambient-sound",
    features: XM4_FEATURES,
    devices: null,
    speak_to_chat: true
  }, overrides)
}

function xm6(overrides) {
  return Object.assign({
    connected: true,
    protocol: "v2",
    nc_mode: "ambient-sound",
    features: XM6_FEATURES,
    devices: DEVICES,
    speak_to_chat: true,
    listening_mode: "background-music"
  }, overrides)
}

test("rowsFor is empty until a device is connected", function() {
  assert.deepEqual(Model.rowsFor(null), [])
  assert.deepEqual(Model.rowsFor(undefined), [])
  assert.deepEqual(Model.rowsFor(xm4({ connected: false })), [])
})

test("rowsFor on a v1 WH-1000XM4", function() {
  assert.deepEqual(Model.rowsFor(xm4()), [
    "mode", "level", "focus", "eq", "dsee", "stc", "stc-sensitivity",
    "stc-timeout", "stc-focus", "pause", "voice", "apo", "session"
  ])
})

test("rowsFor on a v2 WH-1000XM6", function() {
  assert.deepEqual(Model.rowsFor(xm6()), [
    "mode", "level", "focus", "eq", "listening-mode", "bgm-room-size",
    "dsee", "connection-quality", "stc", "stc-sensitivity", "stc-timeout",
    "pause", "apo", "session", "playback-source"
  ])
})

test("rowsFor on an unrecognised v1 device", function() {
  const state = xm4({ nc_mode: "noise-cancelling", features: UNKNOWN_V1_FEATURES, speak_to_chat: false })
  assert.deepEqual(Model.rowsFor(state), [
    "mode", "eq", "dsee", "stc", "pause", "touch", "voice", "apo", "session"
  ])
})

test("rowsFor on an unrecognised v2 device", function() {
  const state = xm6({ nc_mode: "noise-cancelling", features: UNKNOWN_V2_FEATURES })
  assert.deepEqual(Model.rowsFor(state), [
    "mode", "eq", "listening-mode", "bgm-room-size", "dsee",
    "connection-quality", "stc", "stc-sensitivity", "stc-timeout", "pause",
    "apo", "session", "playback-source"
  ])
})

test("the session row is offered for any connected device", function() {
  // It is a daemon control, not a headphone feature, so it survives an empty
  // feature list and both protocols; the disconnected list stays empty.
  assert.ok(Model.hasRow(Model.rowsFor(xm4()), "session"))
  assert.ok(Model.hasRow(Model.rowsFor(xm6()), "session"))
  assert.ok(Model.hasRow(Model.rowsFor(xm4({ features: [] })), "session"))
  assert.ok(Model.hasRow(Model.rowsFor(xm6({ features: [] })), "session"))
  assert.deepEqual(Model.rowsFor(xm4({ connected: false })), [])
})

test("a released session keeps the last state drawable", function() {
  // Release closes the link but the daemon keeps the last reported values, so
  // the panel can still show the device and offer the reclaim flip. A hard
  // disconnect (session "connecting", nothing retained) stays empty.
  const released = xm4({ connected: false, session: "released" })
  assert.ok(Model.hasRow(Model.rowsFor(released), "session"))
  assert.ok(Model.hasState(released))
  assert.deepEqual(Model.rowsFor(xm4({ connected: false, session: "connecting" })), [])
  assert.equal(Model.hasState(xm4({ connected: false, session: "connecting" })), false)
  assert.equal(Model.hasState(null), false)
})

test("present matches a pinned address, case-insensitively, and ignores other connected devices", function() {
  const devices = [
    { address: "AA:BB:CC:DD:EE:01", name: "WH-1000XM6", connected: true },
    { address: "11:22:33:44:55:66", name: "WH-1000XM6", connected: true }
  ]
  assert.equal(Model.present(devices, "aa:bb:cc:dd:ee:01"), true)
  // Both sides fold case: an upper-case pin still matches a lower-case address.
  assert.equal(Model.present([
    { address: "aa:bb:cc:dd:ee:01", name: "WH-1000XM6", connected: true }
  ], "AA:BB:CC:DD:EE:01"), true)
  // Another connected device — even a Sony one — cannot satisfy the pin: with
  // an address pinned the name is ignored.
  assert.equal(Model.present(devices, "99:88:77:66:55:44"), false)
})

test("present matches a connected Sony-family name when no address is pinned", function() {
  const name = function(text) {
    return Model.present([{ address: "AA:BB:CC:DD:EE:01", name: text, connected: true }], "")
  }
  assert.equal(name("WH-1000XM4"), true)
  assert.equal(name("WF-1000XM5"), true)
  assert.equal(name("WI-1000XM2"), true)
  assert.equal(name("LinkBuds S"), true)
  assert.equal(name("Sony WH-1000XM6"), true)
  assert.equal(name("WH-1000XM6"), true)
})

test("present ignores disconnected and non-Sony devices", function() {
  assert.equal(Model.present([{ address: "AA:BB:CC:DD:EE:01", name: "WH-1000XM4", connected: false }], ""), false)
  assert.equal(Model.present([{ address: "AA:BB:CC:DD:EE:01", name: "WH-1000XM4", connected: false }], "aa:bb:cc:dd:ee:01"), false)
  assert.equal(Model.present([{ address: "AA:BB:CC:DD:EE:01", name: "Keyboard", connected: true }], ""), false)
  assert.equal(Model.present([{ address: "AA:BB:CC:DD:EE:01", name: "Sony speaker", connected: false }], ""), false)
})

test("present is false with no devices, and an empty pin falls through to the name rule", function() {
  assert.equal(Model.present([], ""), false)
  assert.equal(Model.present([], "AA:BB:CC:DD:EE:FF"), false)
  assert.equal(Model.present(null, ""), false)
  assert.equal(Model.present(undefined, undefined), false)
  const devices = [{ address: "AA:BB:CC:DD:EE:01", name: "WF-1000XM5", connected: true }]
  assert.equal(Model.present(devices, ""), true)
  assert.equal(Model.present(devices, null), true)
})

test("v1 never yields v2-only rows", function() {
  const states = [
    xm4(),
    xm4({ features: UNKNOWN_V1_FEATURES }),
    xm4({ nc_mode: "noise-cancelling", speak_to_chat: false })
  ]
  for (const state of states) {
    const rows = Model.rowsFor(state)
    for (const key of V2_ONLY_ROWS) assert.ok(!Model.hasRow(rows, key), key + " in " + rows)
  }
})

test("v2 never yields v1-only rows", function() {
  const states = [
    xm6(),
    xm6({ features: UNKNOWN_V2_FEATURES }),
    xm6({ nc_mode: "noise-cancelling", speak_to_chat: true })
  ]
  for (const state of states) {
    const rows = Model.rowsFor(state)
    for (const key of V1_ONLY_ROWS) assert.ok(!Model.hasRow(rows, key), key + " in " + rows)
  }
})

test("the rows that depend on state follow it", function() {
  assert.ok(!Model.hasRow(Model.rowsFor(xm4({ speak_to_chat: false })), "stc-sensitivity"))
  assert.ok(!Model.hasRow(Model.rowsFor(xm4({ nc_mode: "noise-cancelling" })), "level"))
  assert.ok(!Model.hasRow(Model.rowsFor(xm6({ listening_mode: "standard" })), "bgm-room-size"))
  assert.ok(!Model.hasRow(Model.rowsFor(xm6({ devices: [] })), "playback-source"))
  assert.ok(!Model.hasRow(Model.rowsFor(xm6({ devices: [{ address: "x", connected: false }] })), "playback-source"))
  assert.ok(!Model.hasRow(Model.rowsFor(xm4({ features: UNKNOWN_V1_FEATURES, speak_to_chat: false })), "playback-source"))
})

test("hasRow reads a row list", function() {
  assert.equal(Model.hasRow(["mode", "eq"], "mode"), true)
  assert.equal(Model.hasRow(["mode", "eq"], "apo"), false)
  assert.equal(Model.hasRow([], "mode"), false)
  assert.equal(Model.hasRow(null, "mode"), false)
})

test("deviceOptions offers connected peers only", function() {
  assert.deepEqual(Model.deviceOptions(null), [])
  assert.deepEqual(Model.deviceOptions({ devices: [] }), [])
  assert.deepEqual(Model.deviceOptions({ devices: null }), [])
  assert.deepEqual(Model.deviceOptions(xm6()), [{ value: "AA:BB:CC:DD:EE:01", label: "Phone" }])
  assert.deepEqual(Model.deviceOptions(xm6({
    devices: [{ address: "AA:BB:CC:DD:EE:09", connected: true }]
  })), [{ value: "AA:BB:CC:DD:EE:09", label: "AA:BB:CC:DD:EE:09" }])
  assert.deepEqual(Model.deviceOptions(xm6({
    devices: [{ address: "AA:BB:CC:DD:EE:02", connected: false }]
  })), [])
})

test("playbackSource reads the reported source, then the playing flag", function() {
  assert.equal(Model.playbackSource(null), "")
  assert.equal(Model.playbackSource({ devices: [] }), "")
  assert.equal(Model.playbackSource(xm6()), "AA:BB:CC:DD:EE:01")
  assert.equal(Model.playbackSource(xm6({ playback_source: "AA:BB:CC:DD:EE:03" })),
               "AA:BB:CC:DD:EE:03")
  assert.equal(Model.playbackSource(xm6({
    playback_source: null,
    devices: [{ address: "AA:BB:CC:DD:EE:04", connected: true, playback: true }]
  })), "AA:BB:CC:DD:EE:04")
  assert.equal(Model.playbackSource(xm6({
    playback_source: null,
    devices: [{ address: "AA:BB:CC:DD:EE:05", connected: true, playback: false }]
  })), "")
})

test("eqPresets follows the protocol", function() {
  const values = function(presets) { return presets.map(function(preset) { return preset.value }) }
  assert.deepEqual(values(Model.eqPresets("v1")), [
    "off", "bright", "excited", "mellow", "relaxed", "vocal", "treble-boost",
    "bass-boost", "speech", "manual"
  ])
  assert.deepEqual(values(Model.eqPresets("v2")), [
    "off", "heavy", "clear", "hard", "soft", "custom"
  ])
  assert.deepEqual(values(Model.eqPresets(null)), values(Model.eqPresets("v1")))
})

test("autoPowerOffOptions follows the timer feature", function() {
  const values = function(options) { return options.map(function(option) { return option.value }) }
  assert.deepEqual(values(Model.autoPowerOffOptions(["auto-power-off", "auto-power-off-timer"])), [
    "off", "when-taken-off", "5-min", "30-min", "1-hour", "3-hour"
  ])
  assert.deepEqual(values(Model.autoPowerOffOptions(["auto-power-off"])), ["off", "when-taken-off"])
  assert.deepEqual(values(Model.autoPowerOffOptions(null)), ["off", "when-taken-off"])
})

test("supports reads the feature list", function() {
  assert.equal(Model.supports(["dsee", "equalizer"], "equalizer"), true)
  assert.equal(Model.supports(["dsee"], "equalizer"), false)
  assert.equal(Model.supports(null, "dsee"), false)
  assert.equal(Model.supports(undefined, "dsee"), false)
})

test("logging cycles errors -> all -> off -> errors", function() {
  assert.deepEqual(Model.LOG_LEVELS, ["off", "errors", "all"])
  assert.equal(Model.nextLogging("errors"), "all")
  assert.equal(Model.nextLogging("all"), "off")
  assert.equal(Model.nextLogging("off"), "errors")
  assert.equal(Model.nextLogging(undefined), "all")
  assert.equal(Model.nextLogging("nonsense"), "all")
})

test("loggingLabel names every level", function() {
  assert.equal(Model.loggingLabel("errors"), "only error logging")
  assert.equal(Model.loggingLabel("all"), "all logging")
  assert.equal(Model.loggingLabel("off"), "no logging")
  assert.equal(Model.loggingLabel(undefined), "only error logging")
  assert.equal(Model.loggingLabel("nonsense"), "only error logging")
})

test("sessionLabel names the control-session state", function() {
  assert.equal(Model.sessionLabel({ session: "held" }), "Session: held")
  assert.equal(Model.sessionLabel({ session: "released" }), "Session: released")
  assert.equal(Model.sessionLabel({ session: "connecting" }), "Session: connecting")
  // A state with no session key falls back to the neutral default the daemon
  // reports for a bare link, matching initial_state().
  assert.equal(Model.sessionLabel({}), "Session: held")
  assert.equal(Model.sessionLabel(null), "Session: held")
  assert.equal(Model.sessionLabel({ session: "nonsense" }), "Session: held")
})

test("nextMode walks the earcup cycle", function() {
  assert.equal(Model.nextMode("noise-cancelling"), "ambient-sound")
  assert.equal(Model.nextMode("ambient-sound"), "off")
  assert.equal(Model.nextMode("off"), "noise-cancelling")
  assert.equal(Model.nextMode("wind-noise-reduction"), "noise-cancelling")
  assert.equal(Model.nextMode(null), "noise-cancelling")
})

test("clampLevel keeps the slider inside its range", function() {
  assert.equal(Model.clampLevel(25), Model.MAX_AMBIENT_LEVEL)
  assert.equal(Model.clampLevel(-1), 0)
  assert.equal(Model.clampLevel(4.6), 5)
  assert.equal(Model.clampLevel(0), 0)
  assert.equal(Model.clampLevel(20), 20)
})

test("clampSessionIdle mirrors the daemon's idle bounds", function() {
  assert.equal(Model.SESSION_IDLE_MIN, 1)
  assert.equal(Model.SESSION_IDLE_MAX, 86400)
  assert.equal(Model.SESSION_IDLE_DEFAULT, 30)
  assert.equal(Model.clampSessionIdle(0), 1)
  assert.equal(Model.clampSessionIdle(200000), 86400)
  assert.equal(Model.clampSessionIdle(45), 45)
  assert.equal(Model.clampSessionIdle(NaN), 30)
  assert.equal(Model.clampSessionIdle("nonsense"), 30)
  assert.equal(Model.clampSessionIdle(1), 1)
  assert.equal(Model.clampSessionIdle(86400), 86400)
})

test("settingKeyForRow translates a UI row to the helper's setting key", function() {
  assert.equal(Model.settingKeyForRow("mode"), "nc")
  assert.equal(Model.settingKeyForRow("level"), "ambient-level")
  assert.equal(Model.settingKeyForRow("focus"), "focus-on-voice")
  assert.equal(Model.settingKeyForRow("stc"), "speak-to-chat")
  assert.equal(Model.settingKeyForRow("stc-focus"), "stc-focus-on-voice")
  assert.equal(Model.settingKeyForRow("pause"), "pause-when-taken-off")
  assert.equal(Model.settingKeyForRow("voice"), "voice-notifications")
  assert.equal(Model.settingKeyForRow("apo"), "auto-power-off")
  // A row whose names already agree, and an unknown row, pass through.
  assert.equal(Model.settingKeyForRow("dsee"), "dsee")
  assert.equal(Model.settingKeyForRow("mystery"), "mystery")
})

test("isPending reads the helper's pending list through the row key", function() {
  assert.equal(Model.isPending({ pending: ["nc"] }, "mode"), true)
  assert.equal(Model.isPending({ pending: ["focus-on-voice"] }, "focus"), true)
  assert.equal(Model.isPending({ pending: ["speak-to-chat"] }, "stc"), true)
  assert.equal(Model.isPending({ pending: ["ambient-level"] }, "level"), true)
  // A different setting's pending does not leak onto this row.
  assert.equal(Model.isPending({ pending: ["nc"] }, "level"), false)
  assert.equal(Model.isPending({ pending: [] }, "mode"), false)
  // A missing pending list from an older helper reads as not pending.
  assert.equal(Model.isPending({}, "mode"), false)
  assert.equal(Model.isPending(null, "mode"), false)
})

test("refusedReason names the reason through the row key", function() {
  const state = { refused: { "nc": "the headphones did not change nc" } }
  assert.equal(Model.refusedReason(state, "mode"), "the headphones did not change nc")
  assert.equal(Model.refusedReason({ refused: { "eq": "no" } }, "eq"), "no")
  // A refusal on another setting does not leak onto this row.
  assert.equal(Model.refusedReason(state, "level"), "")
  assert.equal(Model.refusedReason({ refused: {} }, "mode"), "")
  assert.equal(Model.refusedReason({}, "mode"), "")
  assert.equal(Model.refusedReason(null, "mode"), "")
})

test("refusedSummary joins every refusal into one line", function() {
  assert.equal(Model.refusedSummary({ refused: {} }), "")
  assert.equal(Model.refusedSummary({ refused: { "nc": "no nc" } }), "no nc")
  const summary = Model.refusedSummary({ refused: { "nc": "no nc", "eq": "no eq" } })
  assert.ok(summary.indexOf("no nc") !== -1, summary)
  assert.ok(summary.indexOf("no eq") !== -1, summary)
  assert.equal(Model.refusedSummary({}), "")
  assert.equal(Model.refusedSummary(null), "")
})

if (failures > 0) {
  console.error(failures + " test(s) failed")
  process.exit(1)
}
console.log("all model tests passed")
