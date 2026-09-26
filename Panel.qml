import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  moduleName: "gabamnml.sony-headphones"
  ipcTarget: "gabamnml.sony-headphones"
  manageIpc: false

  // Third-party plugins cannot lean on OMARCHY_PATH, which points at the
  // packaged shell; the helper sits next to this file.
  readonly property string helperPath: String(Qt.resolvedUrl("bin/sony-headphones")).replace(/^file:\/\//, "")

  // The value may arrive as a real boolean from a hand-edited shell.json or as
  // a string from `omarchy bar set`; accept either.
  readonly property bool showBattery: {
    var value = setting("showBattery", true)
    if (value === true || value === false) return value
    var text = String(value).toLowerCase()
    return !(text === "off" || text === "false" || text === "no" || text === "0")
  }
  readonly property string pinnedAddress: String(setting("address", ""))

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool vertical: bar ? bar.vertical : false

  // What this model actually honours, as reported by the helper. Rows for
  // anything else are not drawn: a control that silently does nothing is worse
  // than one that is not offered.
  readonly property var features: sony.state.features || []
  function supports(feature) { return Model.supports(features, feature) }
  // From PipeWire, not the headphones, so any model can have it.
  readonly property bool hasCodecs: !!sony.state.codecs && sony.state.codecs.length > 0
  readonly property bool hasSoundRows: supports("equalizer") || supports("dsee") || hasCodecs
                                       || supports("connection-priority")

  property int cursorIndex: -1
  property bool cursorActive: false
  property real wheelAccumulator: 0

  Service {
    id: sony
    helperPath: root.helperPath
    address: root.pinnedAddress
    onChanged: root.ensureCursor()
  }

  // -- rows ----------------------------------------------------------------
  //
  // One flat list drives keyboard navigation. Rebuilding it from the current
  // state keeps the cursor honest: rows that are not on screen — the ambient
  // slider while noise cancelling, the speak-to-chat detail while it is off —
  // are not in the list, so they cannot be landed on.

  readonly property var rows: {
    if (!sony.connected) return []
    var list = ["mode"]
    if (sony.mode === "ambient-sound" || sony.mode === "off") {
      list.push("level")
      list.push("focus")
    }
    if (supports("equalizer")) list.push("eq")
    if (supports("dsee")) list.push("dsee")
    if (hasCodecs) list.push("codec")
    if (supports("connection-priority")) list.push("priority")
    if (supports("speak-to-chat")) {
      list.push("stc")
      if (sony.state.speak_to_chat) {
        list.push("stc-sensitivity")
        list.push("stc-timeout")
        if (supports("speak-to-chat-voice-focus")) list.push("stc-focus")
      }
    }
    if (supports("pause-when-taken-off")) list.push("pause")
    if (supports("touch-sensor")) list.push("touch")
    if (supports("voice-notifications")) list.push("voice")
    if (supports("auto-power-off")) list.push("apo")
    return list
  }

  function hasCursorFor(key) {
    return cursorActive && cursorIndex >= 0 && rows[cursorIndex] === key
  }

  function ensureCursor() {
    if (rows.length === 0) {
      cursorIndex = -1
      return
    }
    if (cursorIndex < 0) cursorIndex = 0
    else if (cursorIndex >= rows.length) cursorIndex = rows.length - 1
  }

  function setCursor(key) {
    var index = rows.indexOf(key)
    if (index === -1) return
    cursorActive = true
    cursorIndex = index
  }

  function moveCursor(dx, dy) {
    cursorActive = true
    ensureCursor()
    if (dy !== 0) {
      cursorIndex = Math.max(0, Math.min(rows.length - 1, cursorIndex + dy))
      return
    }
    if (dx !== 0) adjustCursorRow(dx)
  }

  function stepOption(options, current, direction) {
    var index = 0
    for (var i = 0; i < options.length; i++) {
      if (options[i].value === current) index = i
    }
    return options[Math.max(0, Math.min(options.length - 1, index + direction))].value
  }

  // Left / right nudges the row under the cursor: a mode along its cycle, the
  // ambient level by one step, a dropdown to its neighbouring option.
  function adjustCursorRow(direction) {
    var key = rows[cursorIndex]
    if (key === "mode") {
      var modes = root.modeOptions()
      sony.setMode(stepOption(modes, sony.mode, direction))
    } else if (key === "level") {
      sony.setAmbientLevel(sony.ambientLevel + direction)
    } else if (key === "eq") {
      sony.choose("eq", "eq_preset", stepOption(Model.EQ_PRESETS, sony.state.eq_preset, direction))
    } else if (key === "codec") {
      sony.choose("codec", "a2dp_codec", stepOption(Model.codecOptions(sony.state.codecs), sony.state.a2dp_codec, direction))
    } else if (key === "priority") {
      sony.choose("priority", "priority", stepOption(Model.PRIORITY, sony.state.priority, direction))
    } else if (key === "stc-sensitivity") {
      sony.choose("stc-sensitivity", "stc_sensitivity", stepOption(Model.STC_SENSITIVITY, sony.state.stc_sensitivity, direction))
    } else if (key === "stc-timeout") {
      sony.choose("stc-timeout", "stc_timeout", stepOption(Model.STC_TIMEOUT, sony.state.stc_timeout, direction))
    } else if (key === "apo") {
      sony.choose("auto-power-off", "auto_power_off",
                  stepOption(Model.autoPowerOffOptions(root.features), sony.state.auto_power_off, direction))
    } else {
      activateRow(key)
    }
  }

  function activateRow(key) {
    if (key === "mode") sony.cycleMode()
    else if (key === "focus") sony.toggle("focus-on-voice", "focus_on_voice")
    else if (key === "dsee") sony.toggle("dsee", "dsee")
    else if (key === "stc") sony.toggle("speak-to-chat", "speak_to_chat")
    else if (key === "stc-focus") sony.toggle("stc-focus-on-voice", "stc_focus_on_voice")
    else if (key === "pause") sony.toggle("pause-when-taken-off", "pause_when_taken_off")
    else if (key === "touch") sony.toggle("touch-sensor", "touch_sensor")
    else if (key === "voice") sony.toggle("voice-notifications", "voice_notifications")
  }

  function modeOptions() {
    var options = [
      { value: "noise-cancelling", label: "NC" },
      { value: "ambient-sound", label: "Ambient" },
      { value: "off", label: "Off" }
    ]
    if (sony.supportsWind) options.splice(1, 0, { value: "wind-noise-reduction", label: "Wind" })
    return options
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onOpenedChanged: if (opened) {
    cursorActive = false
    cursorIndex = 0
    if (panelFlick) panelFlick.contentY = 0
    sony.refresh()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function cycle(): string { sony.cycleMode(); return sony.mode }
    function mode(value: string): string { sony.setMode(value); return value }
    function ambient(level: string): string { sony.setAmbientLevel(parseInt(level, 10)); return level }
    function status(): string { return JSON.stringify(sony.state) }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: Model.barText(sony.state, root.showBattery, root.vertical)
    slotSize: Style.bar.iconSlot * (root.showBattery && !root.vertical && sony.connected ? 2 : 1)
    tooltipText: Model.tooltip(sony.state)
    foreground: sony.connected ? root.barForeground : Qt.darker(root.barForeground, 1.6)

    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) sony.cycleMode()
      else if (buttonCode === Qt.MiddleButton) sony.refresh()
      else root.toggle()
    }

    onWheelMoved: function(delta) {
      if (!sony.connected) return
      var wheel = Util.wheelSteps(root.wheelAccumulator, delta)
      root.wheelAccumulator = wheel.remainder
      if (wheel.steps === 0) return
      sony.setAmbientLevel(sony.ambientLevel + wheel.steps)
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(360))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(600))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (!root.cursorActive) { root.cursorActive = true; root.ensureCursor(); return }
        root.moveCursor(dx, dy)
      }
      onActivateRequested: if (root.cursorActive && root.cursorIndex >= 0) root.activateRow(root.rows[root.cursorIndex])
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        var key = String(t).toLowerCase()
        if (key === "n") sony.cycleMode()
        else if (key === "r") sony.refresh()
        else if (key === "d") sony.toggle("dsee", "dsee")
        else if (key === "s") sony.toggle("speak-to-chat", "speak_to_chat")
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          PanelHero {
            id: hero
            width: parent.width
            title: Model.deviceLabel(sony.state)
            meta: Model.heroMeta(sony.state)
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconOpacity: sony.connected ? 1.0 : 0.5
            iconComponent: Component {
              Text {
                textFormat: Text.PlainText
                text: Model.modeIcon(sony.mode, sony.connected)
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            visible: !sony.connected
            width: parent.width
            text: sony.starting
              ? "Looking for headphones…"
              : (sony.lastError !== "" ? sony.lastError
                                       : "Connect your Sony headphones over Bluetooth to control them here.")
            color: sony.lastError !== "" && !sony.starting ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          // -- listening ------------------------------------------------

          Column {
            visible: sony.connected
            width: parent.width
            spacing: Style.space(8)

            PanelSectionHeader {
              text: "LISTENING"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            CursorSurface {
              width: parent.width
              hasCursor: root.hasCursorFor("mode")
              foreground: root.foreground
              implicitHeight: modeGroup.implicitHeight + Style.spacing.rowPaddingX

              ButtonGroup {
                id: modeGroup
                anchors.centerIn: parent
                options: root.modeOptions()
                value: sony.mode
                foreground: root.foreground
                background: root.bar ? root.bar.background : Color.background
                fontFamily: root.fontFamily
                focusable: false
                onChanged: function(value) { root.setCursor("mode"); sony.setMode(value) }
                onHovered: function(index, isHovered) { if (isHovered) root.setCursor("mode") }
              }
            }

            CursorSurface {
              visible: sony.mode === "ambient-sound" || sony.mode === "off"
              width: parent.width
              hasCursor: root.hasCursorFor("level")
              foreground: root.foreground
              implicitHeight: Style.spacing.controlHeight

              MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.NoButton
                onEntered: root.setCursor("level")
              }

              RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Style.space(10)
                anchors.rightMargin: Style.space(10)
                spacing: Style.space(10)

                Text {
                  textFormat: Text.PlainText
                  text: "Ambient"
                  color: root.foreground
                  opacity: 0.6
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  Layout.alignment: Qt.AlignVCenter
                }

                PanelSlider {
                  bar: root.bar
                  Layout.fillWidth: true
                  Layout.alignment: Qt.AlignVCenter
                  minimum: 0
                  maximum: Model.MAX_AMBIENT_LEVEL
                  step: 1
                  integer: true
                  value: sony.ambientLevel
                  onMoved: function(v) { root.setCursor("level"); sony.setAmbientLevel(v) }
                }

                Text {
                  textFormat: Text.PlainText
                  text: sony.ambientLevel + "/" + Model.MAX_AMBIENT_LEVEL
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  Layout.alignment: Qt.AlignVCenter
                }
              }
            }

            ToggleRow {
              visible: sony.mode === "ambient-sound" || sony.mode === "off"
              rowKey: "focus"
              label: "Focus on voice"
              hint: "Let voices through, filter the rest"
              checked: sony.focusOnVoice
            }
          }

          PanelSeparator {
            visible: sony.connected && root.hasSoundRows
            foreground: root.foreground
          }

          // -- sound ----------------------------------------------------

          Column {
            visible: sony.connected && root.hasSoundRows
            width: parent.width
            spacing: Style.space(8)

            PanelSectionHeader {
              text: "SOUND"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            DropdownRow {
              rowKey: "eq"
              visible: root.supports("equalizer")
              label: "Equalizer"
              options: Model.EQ_PRESETS
              value: String(sony.state.eq_preset || "off")
              onPicked: function(value) { sony.choose("eq", "eq_preset", value) }
            }

            ToggleRow {
              rowKey: "dsee"
              visible: root.supports("dsee")
              label: "DSEE Extreme"
              hint: "Upscale compressed audio"
              checked: !!sony.state.dsee
            }

            DropdownRow {
              rowKey: "codec"
              visible: root.hasCodecs
              label: "Codec"
              options: Model.codecOptions(sony.state.codecs)
              value: String(sony.state.a2dp_codec || "")
              onPicked: function(value) { sony.choose("codec", "a2dp_codec", value) }
            }

            DropdownRow {
              rowKey: "priority"
              visible: root.supports("connection-priority")
              label: "Priority"
              options: Model.PRIORITY
              value: String(sony.state.priority || "sound-quality")
              onPicked: function(value) { sony.choose("priority", "priority", value) }
            }
          }

          PanelSeparator {
            visible: sony.connected && root.supports("speak-to-chat")
            foreground: root.foreground
          }

          // -- speak to chat --------------------------------------------

          Column {
            visible: sony.connected && root.supports("speak-to-chat")
            width: parent.width
            spacing: Style.space(8)

            PanelSectionHeader {
              text: "SPEAK-TO-CHAT"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            ToggleRow {
              rowKey: "stc"
              label: "Speak-to-Chat"
              hint: "Pause playback when you start talking"
              checked: !!sony.state.speak_to_chat
            }

            DropdownRow {
              rowKey: "stc-sensitivity"
              visible: !!sony.state.speak_to_chat
              label: "Sensitivity"
              options: Model.STC_SENSITIVITY
              value: String(sony.state.stc_sensitivity || "auto")
              onPicked: function(value) { sony.choose("stc-sensitivity", "stc_sensitivity", value) }
            }

            DropdownRow {
              rowKey: "stc-timeout"
              visible: !!sony.state.speak_to_chat
              label: "Resume after"
              options: Model.STC_TIMEOUT
              value: String(sony.state.stc_timeout || "standard")
              onPicked: function(value) { sony.choose("stc-timeout", "stc_timeout", value) }
            }

            ToggleRow {
              rowKey: "stc-focus"
              visible: !!sony.state.speak_to_chat && root.supports("speak-to-chat-voice-focus")
              label: "Voice focus while chatting"
              checked: !!sony.state.stc_focus_on_voice
            }
          }

          PanelSeparator {
            visible: sony.connected && (root.supports("pause-when-taken-off") || root.supports("touch-sensor")
                                        || root.supports("voice-notifications") || root.supports("auto-power-off"))
            foreground: root.foreground
          }

          // -- behaviour ------------------------------------------------

          Column {
            visible: sony.connected && (root.supports("pause-when-taken-off") || root.supports("touch-sensor")
                                        || root.supports("voice-notifications") || root.supports("auto-power-off"))
            width: parent.width
            spacing: Style.space(8)

            PanelSectionHeader {
              text: "HEADPHONES"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            ToggleRow {
              rowKey: "pause"
              visible: root.supports("pause-when-taken-off")
              label: "Pause when taken off"
              checked: !!sony.state.pause_when_taken_off
            }

            ToggleRow {
              rowKey: "touch"
              visible: root.supports("touch-sensor")
              label: "Touch controls"
              hint: sony.state.protocol === 2 ? "Switching off briefly reconnects" : ""
              checked: !!sony.state.touch_sensor
            }

            ToggleRow {
              rowKey: "voice"
              visible: root.supports("voice-notifications")
              label: "Voice guidance"
              checked: !!sony.state.voice_notifications
            }

            DropdownRow {
              rowKey: "apo"
              visible: root.supports("auto-power-off")
              label: "Power off"
              options: Model.autoPowerOffOptions(root.features)
              value: String(sony.state.auto_power_off || "off")
              onPicked: function(value) { sony.choose("auto-power-off", "auto_power_off", value) }
            }
          }

          Text {
            textFormat: Text.PlainText
            visible: sony.connected
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            // A change that did not take says why here, in place of the usual
            // codec and firmware line, until the next change is made.
            text: sony.setError !== "" ? sony.setError
              : [sony.state.codec, sony.state.firmware ? "firmware " + sony.state.firmware : ""]
                  .filter(function(part) { return !!part }).join(" · ")
            color: sony.setError !== "" ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: sony.setError !== "" ? Text.WordWrap : Text.NoWrap
            elide: sony.setError !== "" ? Text.ElideNone : Text.ElideRight
          }
        }
      }
    }
  }

  // -- row components ------------------------------------------------------

  component ToggleRow: CursorSurface {
    id: toggleRow
    property string rowKey: ""
    property string label: ""
    property string hint: ""
    property bool checked: false

    width: column.width
    hasCursor: root.hasCursorFor(rowKey)
    foreground: root.foreground
    implicitHeight: toggleContent.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onEntered: root.setCursor(toggleRow.rowKey)
      onClicked: root.activateRow(toggleRow.rowKey)
    }

    RowLayout {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(8)

      ColumnLayout {
        id: toggleContent
        Layout.fillWidth: true
        spacing: Style.space(1)

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: toggleRow.label
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }

        Text {
          textFormat: Text.PlainText
          visible: toggleRow.hint !== ""
          Layout.fillWidth: true
          text: toggleRow.hint
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      ToggleSwitch {
        checked: toggleRow.checked
        busy: sony.busy
        hasCursor: toggleRow.hasCursor
        foreground: root.foreground
        Layout.alignment: Qt.AlignVCenter
        onToggled: root.activateRow(toggleRow.rowKey)
        onHovered: function(on) { if (on) root.setCursor(toggleRow.rowKey) }
      }
    }
  }

  component DropdownRow: CursorSurface {
    id: dropdownRow
    property string rowKey: ""
    property string label: ""
    property var options: []
    property string value: ""
    signal picked(string value)

    width: column.width
    hasCursor: root.hasCursorFor(rowKey)
    foreground: root.foreground
    implicitHeight: Style.spacing.controlHeight + Style.spacing.rowPaddingX

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      acceptedButtons: Qt.NoButton
      onEntered: root.setCursor(dropdownRow.rowKey)
    }

    RowLayout {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(8)

      Text {
        textFormat: Text.PlainText
        text: dropdownRow.label
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        Layout.alignment: Qt.AlignVCenter
      }

      Item { Layout.fillWidth: true }

      Dropdown {
        Layout.preferredWidth: Style.space(150)
        Layout.alignment: Qt.AlignVCenter
        showLabel: false
        options: dropdownRow.options
        value: dropdownRow.value
        fontFamily: root.fontFamily
        hasCursor: dropdownRow.hasCursor
        onChanged: function(value) { dropdownRow.picked(value) }
        onHovered: function(on) { if (on) root.setCursor(dropdownRow.rowKey) }
      }
    }
  }
}
