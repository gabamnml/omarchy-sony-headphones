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
  // The daemon's starting log level. The link at the foot of the panel changes
  // the running level; this is what a fresh daemon boots with.
  readonly property string loggingSetting: {
    var value = String(setting("logging", "errors")).toLowerCase()
    return Model.LOG_LEVELS.indexOf(value) !== -1 ? value : "errors"
  }
  // The daemon's control-session policy and idle delay. A fresh daemon boots
  // from these; Service pushes a runtime change over the socket.
  readonly property string sessionPolicy: {
    var value = String(setting("sessionPolicy", "hold")).toLowerCase()
    return Model.SESSION_POLICIES.indexOf(value) !== -1 ? value : "hold"
  }
  // Clamped to the daemon's bounds so a hand-edited shell.json (0, 200000)
  // cannot forward a value the daemon refuses.
  readonly property int idleSeconds: Model.clampSessionIdle(setting("idleSeconds", Model.SESSION_IDLE_DEFAULT))

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

  property int cursorIndex: -1
  property bool cursorActive: false
  property real wheelAccumulator: 0

  Service {
    id: sony
    helperPath: root.helperPath
    address: root.pinnedAddress
    logging: root.loggingSetting
    sessionPolicy: root.sessionPolicy
    idleSeconds: root.idleSeconds
    onChanged: root.ensureCursor()
  }

  // -- rows ----------------------------------------------------------------
  //
  // One flat list drives keyboard navigation. Rebuilding it from the current
  // state keeps the cursor honest: rows that are not on screen — the ambient
  // slider while noise cancelling, the speak-to-chat detail while it is off —
  // are not in the list, so they cannot be landed on.

  readonly property var rows: Model.rowsFor(sony.state)

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
    // A greyed row stays put under the cursor: nudging it is a no-op, so no
    // write leaves for the helper and nothing lands in pending.
    if (!Model.availabilityFor(sony.state, key).available) return
    if (key === "mode") {
      var modes = root.modeOptions()
      sony.setMode(stepOption(modes, sony.mode, direction))
    } else if (key === "level") {
      sony.setAmbientLevel(sony.ambientLevel + direction)
    } else if (key === "eq") {
      sony.choose("eq", "eq_preset", stepOption(Model.eqPresets(sony.state.protocol), sony.state.eq_preset, direction))
    } else if (key === "listening-mode") {
      sony.choose("listening-mode", "listening_mode", stepOption(Model.LISTENING_MODE, sony.state.listening_mode, direction))
    } else if (key === "bgm-room-size") {
      sony.choose("bgm-room-size", "bgm_room_size", stepOption(Model.BGM_ROOM_SIZE, sony.state.bgm_room_size, direction))
    } else if (key === "connection-quality") {
      sony.choose("connection-quality", "connection_quality", stepOption(Model.CONNECTION_QUALITY, sony.state.connection_quality, direction))
    } else if (key === "stc-sensitivity") {
      sony.choose("stc-sensitivity", "stc_sensitivity", stepOption(Model.STC_SENSITIVITY, sony.state.stc_sensitivity, direction))
    } else if (key === "stc-timeout") {
      sony.choose("stc-timeout", "stc_timeout", stepOption(Model.STC_TIMEOUT, sony.state.stc_timeout, direction))
    } else if (key === "apo") {
      sony.choose("auto-power-off", "auto_power_off",
                  stepOption(Model.autoPowerOffOptions(root.features), sony.state.auto_power_off, direction))
    } else if (key === "playback-source") {
      sony.choose("playback-source", "playback_source",
                  stepOption(Model.deviceOptions(sony.state), Model.playbackSource(sony.state), direction))
    } else {
      activateRow(key)
    }
  }

  function activateRow(key) {
    // Activating a greyed row is a no-op: no write, no pending.
    if (!Model.availabilityFor(sony.state, key).available) return
    if (key === "mode") sony.cycleMode()
    else if (key === "focus") sony.toggle("focus-on-voice", "focus_on_voice")
    else if (key === "dsee") sony.toggle("dsee", "dsee")
    else if (key === "stc") sony.toggle("speak-to-chat", "speak_to_chat")
    else if (key === "stc-focus") sony.toggle("stc-focus-on-voice", "stc_focus_on_voice")
    else if (key === "pause") sony.toggle("pause-when-taken-off", "pause_when_taken_off")
    else if (key === "touch") sony.toggle("touch-sensor", "touch_sensor")
    else if (key === "voice") sony.toggle("voice-notifications", "voice_notifications")
    // Releasing hands the one control session to a phone; reclaiming takes it
    // back. A connecting session is mid-hand-off, so activation is a no-op.
    else if (key === "session") {
      if (sony.session === "released") sony.reclaim()
      else if (sony.session === "held") sony.release()
    }
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
    // The watch subscription carries the current state on subscribe and every
    // change after it, so opening the panel is not a resync point. Middle
    // click and `r` are the explicit refreshes.
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
    function release(): string { sony.release(); return sony.session }
    function reclaim(): string { sony.reclaim(); return sony.session }
    function status(): string { return JSON.stringify(sony.state) }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: Model.barText(sony.state, root.showBattery, root.vertical)
    slotSize: Style.bar.iconSlot * (root.showBattery && !root.vertical && sony.showing ? 2 : 1)
    tooltipText: Model.tooltip(sony.state) + "\n"
      + "Click: open panel · Right-click: cycle mode · Middle-click: refresh"
      + (sony.showing ? " · Wheel: ambient level" : "")
    foreground: sony.showing ? root.barForeground : Qt.darker(root.barForeground, 1.6)

    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) sony.cycleMode()
      else if (buttonCode === Qt.MiddleButton) sony.refresh()
      else root.toggle()
    }

    onWheelMoved: function(delta) {
      if (!sony.showing) return
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
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

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
            iconOpacity: sony.showing ? 1.0 : 0.5
            iconComponent: Component {
              Text {
                textFormat: Text.PlainText
                text: Model.modeIcon(sony.mode, sony.showing)
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            visible: !sony.showing
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

          // A control the headphones refused says so instead of silently
          // snapping back. One quiet line, sized like the caption.
          Text {
            textFormat: Text.PlainText
            visible: sony.showing && sony.lastError !== ""
            width: parent.width
            text: sony.lastError
            color: root.urgent
            opacity: 0.8
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          // A write the headphones acknowledged without changing: the helper's
          // own read-back check records one reason per setting, and this line
          // keeps the association the connection-level error must not carry.
          // It clears when a later write to the same setting moves the value.
          Text {
            textFormat: Text.PlainText
            visible: sony.showing && Model.refusedSummary(sony.state) !== ""
            width: parent.width
            text: Model.refusedSummary(sony.state)
            color: root.urgent
            opacity: 0.8
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          // -- listening ------------------------------------------------

          Column {
            visible: sony.showing
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
              ToolTip.text: "Noise cancelling mode — currently " + Model.modeLabel(sony.mode)
              ToolTip.visible: modeMouse.containsMouse && ToolTip.text !== ""

              MouseArea {
                id: modeMouse
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.NoButton
                onEntered: root.setCursor("mode")
              }

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
              visible: Model.hasRow(rows, "level")
              width: parent.width
              hasCursor: root.hasCursorFor("level")
              foreground: root.foreground
              implicitHeight: Style.spacing.controlHeight
              ToolTip.text: "Ambient sound level — currently " + sony.ambientLevel
                + "/" + Model.MAX_AMBIENT_LEVEL + ". Drag or scroll to change."
              ToolTip.visible: levelMouse.containsMouse && ToolTip.text !== ""

              MouseArea {
                id: levelMouse
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
              visible: Model.hasRow(rows, "focus")
              rowKey: "focus"
              label: "Focus on voice"
              hint: "Let voices through, filter the rest"
              checked: sony.focusOnVoice
            }
          }

          PanelSeparator {
            visible: sony.showing && (root.supports("equalizer") || root.supports("listening-mode") || root.supports("dsee") || root.supports("connection-quality"))
            foreground: root.foreground
          }

          // -- sound ----------------------------------------------------

          Column {
            visible: sony.showing && (root.supports("equalizer") || root.supports("listening-mode") || root.supports("dsee") || root.supports("connection-quality"))
            width: parent.width
            spacing: Style.space(8)

            PanelSectionHeader {
              text: "SOUND"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            DropdownRow {
              rowKey: "eq"
              visible: Model.hasRow(rows, "eq")
              label: "Equalizer"
              options: Model.eqPresets(sony.state.protocol)
              value: String(sony.state.eq_preset || "off")
              onPicked: function(value) { sony.choose("eq", "eq_preset", value) }
              tip: "Equalizer preset — the tone curve the headphones apply"
            }

            DropdownRow {
              rowKey: "listening-mode"
              visible: Model.hasRow(rows, "listening-mode")
              label: "Listening mode"
              options: Model.LISTENING_MODE
              value: String(sony.state.listening_mode || "standard")
              onPicked: function(value) { sony.choose("listening-mode", "listening_mode", value) }
              tip: "Listening mode — how the headphones colour sound for what is around you"
            }

            DropdownRow {
              rowKey: "bgm-room-size"
              visible: Model.hasRow(rows, "bgm-room-size")
              label: "Room"
              options: Model.BGM_ROOM_SIZE
              value: String(sony.state.bgm_room_size || "living-room")
              onPicked: function(value) { sony.choose("bgm-room-size", "bgm_room_size", value) }
              tip: "Room — the space Background Music simulates"
            }

            ToggleRow {
              rowKey: "dsee"
              visible: Model.hasRow(rows, "dsee")
              label: "DSEE Extreme"
              hint: "Upscale compressed audio"
              checked: !!sony.state.dsee
            }

            DropdownRow {
              rowKey: "connection-quality"
              visible: Model.hasRow(rows, "connection-quality")
              label: "Bluetooth quality"
              options: Model.CONNECTION_QUALITY
              value: String(sony.state.connection_quality || "sound-quality")
              onPicked: function(value) { sony.choose("connection-quality", "connection_quality", value) }
              tip: "Bluetooth quality — trade bitrate for a stronger link, or the other way around"
            }
          }

          PanelSeparator {
            visible: sony.showing && root.supports("speak-to-chat")
            foreground: root.foreground
          }

          // -- speak to chat --------------------------------------------

          Column {
            visible: sony.showing && root.supports("speak-to-chat")
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
              visible: Model.hasRow(rows, "stc-sensitivity")
              label: "Sensitivity"
              options: Model.STC_SENSITIVITY
              value: String(sony.state.stc_sensitivity || "auto")
              onPicked: function(value) { sony.choose("stc-sensitivity", "stc_sensitivity", value) }
              tip: "Speak-to-Chat sensitivity — how readily your voice pauses playback"
            }

            DropdownRow {
              rowKey: "stc-timeout"
              visible: Model.hasRow(rows, "stc-timeout")
              label: "Resume after"
              options: Model.STC_TIMEOUT
              value: String(sony.state.stc_timeout || "standard")
              onPicked: function(value) { sony.choose("stc-timeout", "stc_timeout", value) }
              tip: "Resume delay — how long after you stop talking playback returns"
            }

            ToggleRow {
              rowKey: "stc-focus"
              visible: Model.hasRow(rows, "stc-focus")
              label: "Voice focus while chatting"
              checked: !!sony.state.stc_focus_on_voice
              tip: "Voice focus while chatting — pass voices through while Speak-to-Chat holds playback"
            }
          }

          PanelSeparator {
            // The session row belongs to the daemon, so this section stays
            // reachable whenever the headphones are connected.
            visible: sony.showing
            foreground: root.foreground
          }

          // -- behaviour ------------------------------------------------

          Column {
            visible: sony.showing
            width: parent.width
            spacing: Style.space(8)

            PanelSectionHeader {
              text: "HEADPHONES"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            ToggleRow {
              rowKey: "pause"
              visible: Model.hasRow(rows, "pause")
              label: "Pause when taken off"
              checked: !!sony.state.pause_when_taken_off
              tip: "Pause when taken off — stop playback when the headphones leave your head"
            }

            ToggleRow {
              rowKey: "touch"
              visible: Model.hasRow(rows, "touch")
              label: "Touch controls"
              checked: !!sony.state.touch_sensor
              tip: "Touch controls — the earcup touch panel for play, volume and calls"
            }

            ToggleRow {
              rowKey: "voice"
              visible: Model.hasRow(rows, "voice")
              label: "Voice guidance"
              checked: !!sony.state.voice_notifications
              tip: "Voice guidance — spoken status announcements from the headphones"
            }

            DropdownRow {
              rowKey: "apo"
              visible: Model.hasRow(rows, "apo")
              label: "Power off"
              options: Model.autoPowerOffOptions(root.features)
              value: String(sony.state.auto_power_off || "off")
              onPicked: function(value) { sony.choose("auto-power-off", "auto_power_off", value) }
              tip: "Auto power-off — when the headphones turn themselves off"
            }

            // The one control session a Sony headset allows: held by the
            // daemon, or handed to a phone until it is reclaimed. The switch
            // reads "held" and flips on activation, in either direction.
            ToggleRow {
              rowKey: "session"
              visible: Model.hasRow(rows, "session")
              label: Model.sessionLabel(sony.state)
              hint: sony.session === "released"
                ? "Take the control session back"
                : "Hand the control session to a phone"
              checked: sony.session === "held"
              tip: sony.session === "released" ? "Control session — now released to a phone. Activate to take it back." : "Control session — now held by the widget. Activate to hand it to a phone."
            }
          }

          PanelSeparator {
            visible: sony.showing && root.supports("multipoint") && Model.deviceOptions(sony.state).length > 0
            foreground: root.foreground
          }

          // -- multipoint -----------------------------------------------

          Column {
            visible: sony.showing && root.supports("multipoint") && Model.deviceOptions(sony.state).length > 0
            width: parent.width
            spacing: Style.space(8)

            PanelSectionHeader {
              text: "MULTIPOINT"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            DropdownRow {
              rowKey: "playback-source"
              visible: Model.hasRow(rows, "playback-source")
              label: "Playback source"
              options: Model.deviceOptions(sony.state)
              value: String(Model.playbackSource(sony.state))
              onPicked: function(value) { sony.choosePlaybackSource(value) }
              tip: "Playback source — which paired Bluetooth device owns playback"
            }
          }

          // The daemon's log level belongs to the daemon, not the headphones,
          // so this row stays reachable with no device connected; the codec and
          // firmware caption beside it only says something while one is.
          RowLayout {
            width: parent.width
            spacing: Style.space(6)

            Item { Layout.fillWidth: true }

            Text {
              id: caption
              textFormat: Text.PlainText
              visible: sony.showing
              text: [sony.state.codec, sony.state.firmware ? "firmware " + sony.state.firmware : ""]
                .filter(function(part) { return !!part }).join(" · ")
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              elide: Text.ElideRight
              Layout.maximumWidth: column.width
            }

            Text {
              visible: caption.visible && caption.text !== ""
              text: "·"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }

            // The daemon's own log file: click to walk errors → all → off.
            Text {
              textFormat: Text.PlainText
              text: Model.loggingLabel(sony.state.logging)
              color: loggingLinkMouse.containsMouse ? root.foreground : root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.underline: loggingLinkMouse.containsMouse
              ToolTip.text: "Daemon log level — currently " + Model.loggingLabel(sony.state)
                + ". Click to cycle errors → all → off."
              ToolTip.visible: loggingLinkMouse.containsMouse

              MouseArea {
                id: loggingLinkMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: sony.setLogging(Model.nextLogging(sony.state.logging))
              }
            }

            Item { Layout.fillWidth: true }
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
    property string tip: ""
    // A row the model lacks stays hidden (rowsFor); a row that exists but is
    // blocked by another setting is shown with its control greyed, the label
    // at full contrast, and the reason in a tooltip — no persistent line.
    // An available row's tooltip says what the setting does and where it is
    // now; `tip` overrides the composed hint-and-state text for rows whose
    // label alone would not carry the purpose.
    readonly property var availability: Model.availabilityFor(sony.state, rowKey)
    readonly property bool available: availability.available

    width: column.width
    hasCursor: root.hasCursorFor(rowKey)
    foreground: root.foreground
    implicitHeight: toggleContent.implicitHeight + Style.spacing.rowPaddingX

    ToolTip.text: !toggleRow.available
      ? toggleRow.availability.reason
      : (toggleRow.tip !== ""
         ? toggleRow.tip
         : (toggleRow.hint !== "" ? toggleRow.hint : toggleRow.label)
           + " — currently " + (toggleRow.checked ? "on" : "off"))
    ToolTip.visible: rowMouse.containsMouse && ToolTip.text !== ""

    MouseArea {
      id: rowMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: toggleRow.available ? Qt.PointingHandCursor : Qt.ArrowCursor
      onEntered: root.setCursor(toggleRow.rowKey)
      onClicked: {
        if (!toggleRow.available) return
        root.activateRow(toggleRow.rowKey)
      }
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
          text: toggleRow.label + (Model.isPending(sony.state, toggleRow.rowKey) ? " …" : "")
          color: Model.refusedReason(sony.state, toggleRow.rowKey) !== "" ? root.urgent : root.foreground
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
        enabled: toggleRow.available
        opacity: toggleRow.available ? 1.0 : 0.5
        Layout.alignment: Qt.AlignVCenter
        onToggled: {
          if (!toggleRow.available) return
          root.activateRow(toggleRow.rowKey)
        }
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
    property string tip: ""
    signal picked(string value)
    // Same availability contract as ToggleRow: the label stays at full
    // contrast, only the control dims, and the reason rides in a tooltip.
    // The composed text names the option list's label for the current value,
    // so the tooltip reads "Sound quality (LDAC)" rather than the wire token.
    readonly property var availability: Model.availabilityFor(sony.state, rowKey)
    readonly property bool available: availability.available

    width: column.width
    hasCursor: root.hasCursorFor(rowKey)
    foreground: root.foreground
    implicitHeight: Style.spacing.controlHeight + Style.spacing.rowPaddingX

    ToolTip.text: !dropdownRow.available
      ? dropdownRow.availability.reason
      : (dropdownRow.tip !== "" ? dropdownRow.tip : dropdownRow.label)
        + " — currently: " + Model.optionLabel(dropdownRow.options, dropdownRow.value)
    ToolTip.visible: dropdownMouse.containsMouse && ToolTip.text !== ""

    MouseArea {
      id: dropdownMouse
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
        text: dropdownRow.label + (Model.isPending(sony.state, dropdownRow.rowKey) ? " …" : "")
        color: Model.refusedReason(sony.state, dropdownRow.rowKey) !== "" ? root.urgent : root.foreground
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
        enabled: dropdownRow.available
        opacity: dropdownRow.available ? 1.0 : 0.5
        onChanged: function(value) {
          if (!dropdownRow.available) return
          dropdownRow.picked(value)
        }
        onHovered: function(on) { if (on) root.setCursor(dropdownRow.rowKey) }
      }
    }
  }
}
