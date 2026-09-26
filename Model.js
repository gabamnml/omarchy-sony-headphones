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

var PRIORITY = [
  { value: "sound-quality", label: "Sound quality" },
  { value: "connection", label: "Stable connection" }
]

// The codecs PipeWire can switch to right now, as dropdown options. They come
// from the helper best first; nothing is listed that is not on offer.
function codecOptions(codecs) {
  var options = []
  for (var i = 0; i < (codecs || []).length; i++) options.push({ value: codecs[i], label: codecs[i] })
  return options
}

// The timer options exist only on models that honour them; a WH-1000XM4
// answers "when taken off" whatever timer you ask for.
function autoPowerOffOptions(features) {
  if (features && features.indexOf("auto-power-off-timer") !== -1) return AUTO_POWER_OFF
  return AUTO_POWER_OFF.slice(0, 2)
}

function supports(features, feature) {
  return !!features && features.indexOf(feature) !== -1
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
  if (!state || !state.connected) return state && state.error ? "Disconnected" : "Not connected"
  var parts = [modeLabel(state.nc_mode)]
  if (state.nc_mode === "ambient-sound" && state.ambient_level !== null && state.ambient_level !== undefined)
    parts[0] = "Ambient " + state.ambient_level + "/" + MAX_AMBIENT_LEVEL
  var battery = batteryText(state.battery, state.charging)
  if (battery !== "") parts.push(battery)
  return parts.join(" · ")
}

function barText(state, showBattery, vertical) {
  var icon = modeIcon(state ? state.nc_mode : null, state && state.connected)
  if (!showBattery || vertical || !state || !state.connected) return icon
  var battery = batteryText(state.battery, false)
  return battery === "" ? icon : battery + " " + icon
}

function tooltip(state) {
  if (!state || !state.connected) return state && state.error ? state.error : "Sony headphones not connected"
  return deviceLabel(state) + " — " + heroMeta(state)
}

function optionLabel(options, value) {
  for (var i = 0; i < options.length; i++) {
    if (options[i].value === value) return options[i].label
  }
  return value || ""
}

function bandLabel(index) {
  return ["400", "1k", "2.5k", "6.3k", "16k"][index] || ""
}
