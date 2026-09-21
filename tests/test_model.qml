import QtQuick
import QtTest
import "../Model.js" as Model

// QtTest cover for the pure Model.js helpers, exercised in the real engine:
//   QT_QPA_PLATFORM=offscreen qmltestrunner tests/test_model.qml
// The JS import climbs one directory because this file lives in tests/ while
// Model.js lives at the repo root, so run it from the repo root.
TestCase {
    name: "Model"

    function test_present_pinned_address_is_case_insensitive() {
        var devices = [
            { address: "AA:BB:CC:DD:EE:01", name: "WH-1000XM6", connected: true },
            { address: "11:22:33:44:55:66", name: "WH-1000XM6", connected: true }
        ]
        verify(Model.present(devices, "aa:bb:cc:dd:ee:01"))
        verify(Model.present(
            [{ address: "aa:bb:cc:dd:ee:01", name: "WH-1000XM6", connected: true }],
            "AA:BB:CC:DD:EE:01"))
        verify(!Model.present(devices, "99:88:77:66:55:44"))
    }

    function test_present_sony_name_when_unpinned() {
        var names = ["WH-1000XM4", "WF-1000XM5", "WI-1000XM2", "LinkBuds S", "Sony WH-1000XM6"]
        for (var i = 0; i < names.length; i++)
            verify(Model.present(
                [{ address: "AA:BB:CC:DD:EE:01", name: names[i], connected: true }], ""),
                names[i])
    }

    function test_present_rejects_disconnected_and_non_sony() {
        verify(!Model.present(
            [{ address: "AA:BB:CC:DD:EE:01", name: "WH-1000XM4", connected: false }], ""))
        verify(!Model.present(
            [{ address: "AA:BB:CC:DD:EE:01", name: "WH-1000XM4", connected: false }],
            "aa:bb:cc:dd:ee:01"))
        verify(!Model.present(
            [{ address: "AA:BB:CC:DD:EE:01", name: "Keyboard", connected: true }], ""))
        verify(!Model.present([], ""))
    }

    function test_has_state() {
        verify(Model.hasState({ connected: true }))
        verify(Model.hasState({ connected: false, session: "released" }))
        verify(!Model.hasState({ connected: false, session: "connecting" }))
        verify(!Model.hasState(null))
    }

    function test_clamp_session_idle() {
        compare(Model.clampSessionIdle(0), Model.SESSION_IDLE_MIN)
        compare(Model.clampSessionIdle(200000), Model.SESSION_IDLE_MAX)
        compare(Model.clampSessionIdle(45), 45)
        compare(Model.clampSessionIdle("nonsense"), Model.SESSION_IDLE_DEFAULT)
    }

    function test_pending_and_refused_helpers() {
        verify(Model.isPending({ pending: ["nc"] }, "mode"))
        verify(Model.isPending({ pending: ["focus-on-voice"] }, "focus"))
        verify(!Model.isPending({ pending: ["nc"] }, "level"))
        verify(!Model.isPending({}, "mode"))
        compare(Model.refusedReason({ refused: { "nc": "no move" } }, "mode"), "no move")
        compare(Model.refusedReason({ refused: { "nc": "no move" } }, "level"), "")
        var summary = Model.refusedSummary({ refused: { "nc": "no nc", "eq": "no eq" } })
        verify(summary.indexOf("no nc") !== -1, summary)
        verify(summary.indexOf("no eq") !== -1, summary)
        compare(Model.refusedSummary({}), "")
    }

    function test_availability_eq_needs_sbc_on_xm2() {
        var marker = ["battery", "equalizer", "dsee", "nc-optimizer", "eq-sbc-only"]
        var blocked = Model.availabilityFor({ connected: true, features: marker, codec: "LDAC" }, "eq")
        verify(!blocked.available)
        compare(blocked.reason, "Equalizer needs the SBC codec")
        // Case-insensitive: lowercase ldac blocks too.
        verify(!Model.availabilityFor({ connected: true, features: marker, codec: "ldac" }, "eq").available)
        // SBC, an unknown codec, and a missing codec leave the row usable.
        verify(Model.availabilityFor({ connected: true, features: marker, codec: "SBC" }, "eq").available)
        verify(Model.availabilityFor({ connected: true, features: marker, codec: "unknown" }, "eq").available)
        verify(Model.availabilityFor({ connected: true, features: marker }, "eq").available)
        // No marker (XM4 on LDAC): available. Other rows on a blocked XM2: available.
        var xm4 = { connected: true, features: ["battery", "equalizer", "dsee"], codec: "LDAC" }
        verify(Model.availabilityFor(xm4, "eq").available)
        verify(Model.availabilityFor({ connected: true, features: marker, codec: "LDAC" }, "dsee").available)
    }
}
