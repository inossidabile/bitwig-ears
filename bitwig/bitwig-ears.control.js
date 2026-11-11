// Bitwig controller script: lets the bitwig-ears MCP server start/stop the transport.
// Uses Bitwig's built-in OSC module: UDP on 127.0.0.1:7879.
// Send OSC /play /stop /restart /status to 7879; the script answers "/reply" (JSON string) to UDP 7880.
loadAPI(18);
host.setShouldFailOnDeprecatedUse(false);
host.defineController("bitwig-ears", "Transport Bridge", "0.23.0",
    "6f1d4a52-3b0e-4c7e-9a41-5d0f2a7b9c11", "bitwig-ears");

var PORT = 7879;        // commands in (UDP)
var REPLY_PORT = 7880;  // replies out (UDP, the MCP server listens here)
var transport;


var items = [];   // every track we can address: {id, kind, track}
var meters = {};  // id -> {peak, rms} (0..1)
var setupError = null;
var application = null;
var cursorTrack = null, cursorDevice = null, padBank = null, cursorRemote = null;

function hex2(v) { var h = Math.round(v * 255).toString(16); return h.length < 2 ? "0" + h : h; }
function colorHex(c) { return "#" + hex2(c.red()) + hex2(c.green()) + hex2(c.blue()); }
function r3(x) { return Math.round(x * 1000) / 1000; }

function setupTracks() {
    // Flat bank: includes tracks inside groups AND effect + master tracks (no separate banks needed).
    var main = host.createTrackBank(64, 0, 4, true);
    function watch(track, id, kind) {
        track.exists().markInterested();
        track.name().markInterested();
        track.trackType().markInterested();
        track.isGroup().markInterested();
        track.color().markInterested();
        track.mute().markInterested();
        track.solo().markInterested();
        track.arm().markInterested();
        track.isActivated().markInterested();
        track.volume().value().markInterested();
        track.volume().value().displayedValue().markInterested();
        track.pan().value().markInterested();
        track.pan().value().displayedValue().markInterested();
        var slots = track.clipLauncherSlotBank();
        for (var k = 0; k < 4; k++) slots.getItemAt(k).hasContent().markInterested();
        var devs = track.createDeviceBank(3);
        for (var d = 0; d < 3; d++) {
            var dev = devs.getItemAt(d);
            dev.exists().markInterested(); dev.name().markInterested(); dev.isEnabled().markInterested();
            dev.deviceType().markInterested(); dev.hasDrumPads().markInterested();
            dev.isPlugin().markInterested(); dev.presetName().markInterested();
        }
        meters[id] = { peak: 0, rms: 0 };
        track.addVuMeterObserver(128, -1, true, function (v) { meters[id].peak = v / 127; });
        track.addVuMeterObserver(128, -1, false, function (v) { meters[id].rms = v / 127; });
        items.push({ id: id, kind: kind, track: track, devices: devs });
    }
    for (var i = 0; i < main.getSizeOfBank(); i++) watch(main.getItemAt(i), "t" + i, "track");
}

function describe(it) {
    var t = it.track;
    return {
        id: it.id, name: String(t.name().get()), type: String(t.trackType().get()),
        group: !!t.isGroup().get(), color: colorHex(t.color()),
        mute: !!t.mute().get(), solo: !!t.solo().get(), arm: !!t.arm().get(),
        active: !!t.isActivated().get(),
        volume: r3(t.volume().value().get()), volume_text: String(t.volume().value().displayedValue().get()),
        pan: r3(t.pan().value().get()), pan_text: String(t.pan().value().displayedValue().get())
    };
}

function existing() {
    var out = [];
    for (var i = 0; i < items.length; i++) if (items[i].track.exists().get()) out.push(items[i]);
    return out;
}

function findTrack(ref) {
    var ex = existing(), i;
    for (i = 0; i < ex.length; i++) if (ex[i].id === String(ref)) return ex[i];
    var low = String(ref).toLowerCase();
    for (i = 0; i < ex.length; i++) if (String(ex[i].track.name().get()).toLowerCase() === low) return ex[i];
    return null;
}

// Read-only: list Bitwig actions whose id or name contains the filter (case-insensitive).
function actionsList(a) {
    var f = String(a.filter || "").toLowerCase(), lim = Number(a.limit || 80), out = [], total = 0;
    var all = application.getActions();
    for (var i = 0; i < all.length; i++) {
        var id = String(all[i].getId()), name = String(all[i].getName());
        if (f && (id + " " + name).toLowerCase().indexOf(f) < 0) continue;
        total++;
        if (out.length < lim) {
            var cat = ""; try { cat = String(all[i].getCategory().getName()); } catch (e) {}
            out.push({ id: id, name: name, category: cat });
        }
    }
    return { ok: true, total_matching: total, actions: out };
}


// ---- break-to-arrangement experiment: launcher clip -> copy -> paste at playhead ----
var launcherCursor = null, arrangerCursor = null;
var jobLog = [], jobState = "idle";

// 16 sixteenth-note steps: [step, pitch, velocity, length_beats]
var BREAK = [
    [0, 36, 120, 0.25], [7, 36, 100, 0.25], [10, 36, 110, 0.25],      // kick
    [4, 38, 115, 0.25], [12, 38, 118, 0.25], [9, 38, 55, 0.2], [15, 38, 50, 0.2], // snare + ghosts
    [0, 42, 95, 0.2], [2, 42, 70, 0.2], [4, 42, 90, 0.2], [6, 42, 70, 0.2],
    [8, 42, 90, 0.2], [10, 42, 70, 0.2], [12, 42, 90, 0.2], [14, 46, 85, 0.4]  // hats, open on 14
];

function jlog(m) { jobLog.push(m); println("break job: " + m); }
function after(ms, fn) {
    host.scheduleTask(function () {
        try { fn(); } catch (e) { jlog("ERROR " + e); jobState = "failed"; }
    }, ms);
}

function barBeats() {
    var ts = String(transport.timeSignature().get()), m = ts.match(/(\d+)\s*\/\s*(\d+)/);
    return m ? Number(m[1]) * 4 / Number(m[2]) : 4;
}

function startBreakJob(a) {
    if (jobState === "running") return { ok: false, error: "a job is already running" };
    var it = findTrack(a.track);
    if (!it) return { ok: false, error: "no such track: " + a.track };
    var t = it.track, slots = t.clipLauncherSlotBank(), idx = -1;
    for (var k = 0; k < 4; k++) if (!slots.getItemAt(k).hasContent().get()) { idx = k; break; }
    if (idx < 0) return { ok: false, error: "no empty launcher slot among the first 4 on " + a.track };
    var slot = slots.getItemAt(idx), bar = barBeats(), steps = Math.round(bar * 4);
    var doPaste = a.paste !== false, pos = Number(a.position_beats || 0);
    jobLog = []; jobState = "running";
    jlog("track " + it.id + " slot " + idx + " bar=" + bar + " beats (" + transport.timeSignature().get() + ")");

    slot.createEmptyClip(bar);
    slot.select();
    after(500, function () {
        launcherCursor.setStepSize(0.25);
        launcherCursor.clearSteps();
        var n = 0;
        for (var i = 0; i < BREAK.length; i++) {
            if (BREAK[i][0] >= steps) continue;
            launcherCursor.setStep(0, BREAK[i][0], BREAK[i][1], BREAK[i][2], BREAK[i][3]); n++;
        }
        jlog("wrote " + n + " notes; cursor clip exists=" + launcherCursor.exists().get() +
             " loop=" + launcherCursor.getLoopLength().get() + " on " + launcherCursor.getTrack().name().get());
        if (!doPaste) { jobState = "done"; jlog("paste skipped (launcher clip only)"); return; }
        after(500, function () {
            slot.select();
            application.copy();
            jlog("copied");
            after(300, function () {
                transport.getPosition().set(pos);
                t.selectInEditor();
                application.getAction("focus_or_toggle_arranger").invoke();
                jlog("playhead -> " + pos + ", track selected, arranger focus requested");
                after(500, function () {
                    application.paste();
                    jlog("pasted");
                    after(700, function () {
                        var ok = arrangerCursor.exists().get() && String(arrangerCursor.getTrack().name().get()) === String(t.name().get());
                        jlog("arranger cursor clip exists=" + arrangerCursor.exists().get() + " track=" +
                             arrangerCursor.getTrack().name().get() + " loop=" + arrangerCursor.getLoopLength().get());
                        if (ok) { slots.deleteClip(idx); jlog("verified on " + t.name().get() + "; temporary launcher clip deleted"); jobState = "done"; }
                        else { jlog("NOT verified; launcher clip left in slot " + idx + " (drag it, or Cmd+Z)"); jobState = "unverified"; }
                    });
                });
            });
        });
    });
    return { ok: true, started: true, slot: idx };
}

// ---- alternative: RECORD a launcher clip into the arrangement (punch in/out over a 1-bar loop) ----
function startRecordJob(a) {
    if (jobState === "running") return { ok: false, error: "a job is already running" };
    var it = findTrack(a.track);
    if (!it) return { ok: false, error: "no such track: " + a.track };
    var t = it.track, idx = Number(a.slot || 0), slot = t.clipLauncherSlotBank().getItemAt(idx);
    if (!slot.hasContent().get()) return { ok: false, error: "launcher slot " + idx + " on " + a.track + " has no clip" };
    var bar = barBeats(), bpm = Number(transport.tempo().value().getRaw());
    var S = {   // state to restore
        pos: Number(transport.getPosition().get()), loop: !!transport.isArrangerLoopEnabled().get(),
        ls: Number(transport.arrangerLoopStart().get()), ld: Number(transport.arrangerLoopDuration().get()),
        pi: !!transport.isPunchInEnabled().get(), po: !!transport.isPunchOutEnabled().get(),
        rec: !!transport.isArrangerRecordEnabled().get()
    };
    jobLog = []; jobState = "running";
    jlog("record " + it.id + " slot " + idx + " bar=" + bar + " bpm=" + bpm + " saved=" + JSON.stringify(S));
    transport.stop();
    transport.getPosition().set(0);
    transport.arrangerLoopStart().set(0);
    transport.arrangerLoopDuration().set(bar);
    transport.isArrangerLoopEnabled().set(true);
    transport.isPunchInEnabled().set(true);
    transport.isPunchOutEnabled().set(true);
    transport.isArrangerRecordEnabled().set(true);
    after(400, function () {
        jlog("armed: record+punch, loop 0.." + bar + "; launching slot");
        slot.launchWithOptions("none", "from_start");
        after(100, function () {
            if (!transport.isPlaying().get()) { transport.play(); jlog("transport.play() (launch did not start it)"); }
            var ms = Math.round(bar * 60000 / bpm) + 900;
            after(ms, function () {
                transport.stop();
                t.stop();
                t.returnToArrangement();
                transport.isArrangerRecordEnabled().set(S.rec);
                transport.isPunchInEnabled().set(S.pi);
                transport.isPunchOutEnabled().set(S.po);
                transport.arrangerLoopStart().set(S.ls);
                transport.arrangerLoopDuration().set(S.ld);
                transport.isArrangerLoopEnabled().set(S.loop);
                after(300, function () {
                    transport.getPosition().set(S.pos);
                    jlog("stopped; transport settings and playhead restored. Check foo's arrangement at 1.1.");
                    jobState = "done";
                });
            });
        });
    });
    return { ok: true, started: true, slot: idx };
}

// Hand a track back from launcher playback to the arrangement (fixes faded, silent arranger clips).
function returnTrack(a) {
    var it = findTrack(a.track);
    if (!it) return { ok: false, error: "no such track: " + a.track };
    it.track.returnToArrangement();
    return { ok: true, track: it.id };
}

// Which of a track's first 4 launcher slots hold a clip.
function launcherSlots(a) {
    var it = findTrack(a.track);
    if (!it) return { ok: false, error: "no such track: " + a.track };
    var slots = it.track.clipLauncherSlotBank(), out = [];
    for (var k = 0; k < 4; k++) out.push({ slot: k, has_clip: !!slots.getItemAt(k).hasContent().get() });
    return { ok: true, track: it.id, slots: out };
}

// Delete the clip in one launcher slot (refuses if the slot is empty).
function deleteLauncherClip(a) {
    var it = findTrack(a.track);
    if (!it) return { ok: false, error: "no such track: " + a.track };
    var k = Number(a.slot), slots = it.track.clipLauncherSlotBank();
    if (!(k >= 0 && k < 4)) return { ok: false, error: "slot must be 0-3" };
    if (!slots.getItemAt(k).hasContent().get()) return { ok: false, error: "slot " + k + " is already empty" };
    slots.deleteClip(k);
    return { ok: true, deleted: { track: it.id, slot: k } };
}

// ---- one-shot: notes -> arrangement clip (temp launcher clip, record, return track, delete temp) ----
// args: track, notes [{s: start_beats, p: pitch, v: velocity, d: duration_beats}], start_beats, length_beats (integer 1-16)
function startAddClipJob(a) {
    if (jobState === "running") return { ok: false, error: "a job is already running" };
    var it = findTrack(a.track);
    if (!it) return { ok: false, error: "no such track: " + a.track };
    var type = String(it.track.trackType().get());
    if (type !== "Instrument" && type !== "Hybrid") return { ok: false, error: "track '" + a.track + "' is type " + type + "; notes need an Instrument track" };
    var notes = a.notes || [];
    if (!notes.length) return { ok: false, error: "no notes given" };
    var len = Math.round(Number(a.length_beats || 4)), start = Number(a.start_beats || 0);
    if (!(len >= 1 && len <= 16)) return { ok: false, error: "length_beats must be an integer 1-16" };
    if (!(start >= 0)) return { ok: false, error: "start_beats must be >= 0" };
    var t = it.track, slots = t.clipLauncherSlotBank(), idx = -1;
    for (var k = 0; k < 4; k++) if (!slots.getItemAt(k).hasContent().get()) { idx = k; break; }
    if (idx < 0) return { ok: false, error: "no empty launcher slot among the first 4 on " + a.track };
    var slot = slots.getItemAt(idx), bpm = Number(transport.tempo().value().getRaw());
    var S = {
        pos: Number(transport.getPosition().get()), loop: !!transport.isArrangerLoopEnabled().get(),
        ls: Number(transport.arrangerLoopStart().get()), ld: Number(transport.arrangerLoopDuration().get()),
        pi: !!transport.isPunchInEnabled().get(), po: !!transport.isPunchOutEnabled().get(),
        rec: !!transport.isArrangerRecordEnabled().get()
    };
    jobLog = []; jobState = "running";
    jlog("add clip on " + it.id + " (" + notes.length + " notes, " + len + " beats at beat " + start + ", temp slot " + idx + ")");
    transport.stop();
    slot.createEmptyClip(len);
    slot.select();
    after(500, function () {
        launcherCursor.setStepSize(0.25);
        launcherCursor.clearSteps();
        if (a.name) launcherCursor.setName(String(a.name).substring(0, 60));
        var n = 0;
        for (var i = 0; i < notes.length; i++) {
            var x = Math.round(Number(notes[i].s) / 0.25), y = Math.round(Number(notes[i].p));
            var v = Math.max(1, Math.min(127, Math.round(Number(notes[i].v || 100))));
            if (x < 0 || x >= len * 4 || y < 0 || y > 127) continue;
            launcherCursor.setStep(0, x, y, v, Number(notes[i].d || 0.25)); n++;
        }
        jlog("wrote " + n + "/" + notes.length + " notes into temp clip (cursor on " + launcherCursor.getTrack().name().get() + ")");
        jlog("temp clip BEFORE fix: loop start=" + launcherCursor.getLoopStart().get() + " length=" + launcherCursor.getLoopLength().get() +
             " play " + launcherCursor.getPlayStart().get() + ".." + launcherCursor.getPlayStop().get());
        launcherCursor.getLoopStart().set(0);
        launcherCursor.getLoopLength().set(len);
        launcherCursor.getPlayStart().set(0);
        launcherCursor.getPlayStop().set(len);
        transport.getPosition().set(start);
        transport.arrangerLoopStart().set(start);
        transport.arrangerLoopDuration().set(len);
        // Loop OFF: the loop range still defines the punch region, but playback must not wrap back and
        // punch in again (that recorded a second piece while we waited to stop).
        transport.isArrangerLoopEnabled().set(false);
        transport.isPunchInEnabled().set(true);
        transport.isPunchOutEnabled().set(true);
        transport.isArrangerRecordEnabled().set(true);
        after(400, function () {
            jlog("temp clip AFTER fix: loop start=" + launcherCursor.getLoopStart().get() + " length=" + launcherCursor.getLoopLength().get() +
                 " play " + launcherCursor.getPlayStart().get() + ".." + launcherCursor.getPlayStop().get());
            jlog("record armed; launching temp clip");
            slot.launchWithOptions("none", "from_start");
            after(100, function () {
                if (!transport.isPlaying().get()) transport.play();
                after(Math.round(len * 60000 / bpm) + 600, function () {
                    transport.stop();
                    t.stop();
                    t.returnToArrangement();
                    transport.isArrangerRecordEnabled().set(S.rec);
                    transport.isPunchInEnabled().set(S.pi);
                    transport.isPunchOutEnabled().set(S.po);
                    transport.arrangerLoopStart().set(S.ls);
                    transport.arrangerLoopDuration().set(S.ld);
                    transport.isArrangerLoopEnabled().set(S.loop);
                    after(300, function () {
                        transport.getPosition().set(S.pos);
                        slots.deleteClip(idx);
                        jlog("recorded; track returned to arrangement; temp clip deleted; transport restored");
                        jobState = "done";
                    });
                });
            });
        });
    });
    return { ok: true, started: true, slot: idx };
}

// What is on a track: its first 3 top-level devices, so a caller can write music that suits the instrument.
function instrumentInfo(a) {
    var it = findTrack(a.track);
    if (!it) return { ok: false, error: "no such track: " + a.track };
    var out = [];
    for (var d = 0; d < 3; d++) {
        var dev = it.devices.getItemAt(d);
        if (!dev.exists().get()) continue;
        out.push({ name: String(dev.name().get()), type: String(dev.deviceType().get()),
                   plugin: !!dev.isPlugin().get(), enabled: !!dev.isEnabled().get(),
                   drum_pads: !!dev.hasDrumPads().get(), preset: String(dev.presetName().get()) });
    }
    return { ok: true, track: it.id, name: String(it.track.name().get()), type: String(it.track.trackType().get()), devices: out };
}

// ---- create an Instrument track at the end of the track list, name it, optionally load a preset file ----
function startCreateTrackJob(a) {
    if (jobState === "running") return { ok: false, error: "a job is already running" };
    var name = String(a.name || "").substring(0, 60), preset = a.preset_path ? String(a.preset_path) : "";
    var before = {}, ex = existing(), i;
    for (i = 0; i < ex.length; i++) before[String(ex[i].track.name().get())] = true;
    var count = ex.length;
    jobLog = []; jobState = "running";
    jlog("creating instrument track (" + count + " tracks now)");
    application.createInstrumentTrack(-1);
    after(800, function () {
        var now = existing(), made = null;
        for (var k = 0; k < now.length; k++) {
            if (!before[String(now[k].track.name().get())]) { made = now[k]; break; }
        }
        if (!made) { jlog("ERROR could not find the new track (" + now.length + " tracks now)"); jobState = "failed"; return; }
        jlog("new track " + made.id + " was '" + made.track.name().get() + "'");
        if (name) made.track.setName(name);
        if (preset) {
            made.track.startOfDeviceChainInsertionPoint().insertFile(preset);
            jlog("insertFile requested: " + preset);
        }
        after(1200, function () {
            var d = [];
            for (var j = 0; j < 3; j++) {
                var dev = made.devices.getItemAt(j);
                if (dev.exists().get()) d.push(String(dev.name().get()) + " (" + String(dev.presetName().get()) + ")");
            }
            jlog("track is '" + made.track.name().get() + "' devices: " + (d.length ? d.join(", ") : "none"));
            jobState = "done";
        });
    });
    return { ok: true, started: true };
}

// ---- read-only (but selects the track's first drum device in the UI): list a drum device's pads ----
function startDrumPadsJob(a) {
    if (jobState === "running") return { ok: false, error: "a job is already running" };
    var it = findTrack(a.track);
    if (!it) return { ok: false, error: "no such track: " + a.track };
    var dev = null;
    for (var d = 0; d < 3; d++) {
        var x = it.devices.getItemAt(d);
        if (x.exists().get() && x.hasDrumPads().get()) { dev = x; break; }
    }
    if (!dev) return { ok: false, error: "no drum device among the first 3 devices on " + a.track };
    jobLog = []; jobState = "running";
    cursorTrack.selectChannel(it.track);
    after(500, function () {
        jlog("cursor track = " + cursorTrack.name().get());
        cursorDevice.selectDevice(dev);
        after(900, function () {
            var pads = [];
            for (var q = 0; q < 32; q++) {
                var pad = padBank.getItemAt(q);
                if (pad.exists().get()) pads.push({ index: q, name: String(pad.name().get()) });
            }
            jlog("device=" + cursorDevice.name().get() + " hasDrumPads=" + cursorDevice.hasDrumPads().get() +
                 " scroll=" + padBank.scrollPosition().get());
            jlog(JSON.stringify(pads));
            jobState = "done";
        });
    });
    return { ok: true, started: true };
}

// ---- tap plugin management: is "Bitwig Ears Tap" on a track, which Slot does it use, add it ----
var TAP_NAME = "Bitwig Ears Tap", TAP_CLAP_ID = "io.bitwig-ears.tap";

function findTap(it) {
    for (var d = 0; d < 3; d++) {
        var dev = it.devices.getItemAt(d);
        if (dev.exists().get() && String(dev.name().get()).indexOf(TAP_NAME) >= 0) return { dev: dev, index: d };
    }
    return null;
}

// Needs cursorDevice to be on the tap already. Slot is the plugin's IntParam 1-64 (normalized (n-1)/63).
function slotParam() {
    for (var r = 0; r < 8; r++) {
        var rc = cursorRemote.getParameter(r);
        if (rc.exists().get() && String(rc.name().get()).toLowerCase().indexOf("slot") >= 0) return rc;
    }
    return null;
}

function remoteDump() {
    var out = [];
    for (var r = 0; r < 8; r++) {
        var rc = cursorRemote.getParameter(r);
        out.push({ i: r, exists: !!rc.exists().get(), name: String(rc.name().get()) });
    }
    return out;
}

function startTapStatusJob(a) {
    if (jobState === "running") return { ok: false, error: "a job is already running" };
    var it = findTrack(a.track);
    if (!it) return { ok: false, error: "no such track: " + a.track };
    jobLog = []; jobState = "running";
    var tap = findTap(it);
    if (!tap) { jlog(JSON.stringify({ has_tap: false })); jobState = "done"; return { ok: true, started: true }; }
    cursorTrack.selectChannel(it.track);
    after(500, function () {
        cursorDevice.selectDevice(tap.dev);
        after(1000, function () {
            var rc = slotParam();
            jlog(JSON.stringify({ has_tap: true, device_index: tap.index,
                slot: rc ? Math.round(Number(rc.value().get()) * 63) + 1 : null, slot_param_found: !!rc,
                cursor_device: String(cursorDevice.name().get()), remote_controls: remoteDump() }));
            jobState = "done";
        });
    });
    return { ok: true, started: true };
}

function startAddTapJob(a) {
    if (jobState === "running") return { ok: false, error: "a job is already running" };
    var it = findTrack(a.track), slot = Math.round(Number(a.slot));
    if (!it) return { ok: false, error: "no such track: " + a.track };
    if (!(slot >= 1 && slot <= 64)) return { ok: false, error: "slot must be 1-64" };
    if (findTap(it)) return { ok: false, error: "track already has a tap plugin" };
    jobLog = []; jobState = "running";
    it.track.endOfDeviceChainInsertionPoint().insertCLAPDevice(TAP_CLAP_ID);
    jlog("insertCLAPDevice requested on " + it.id);
    after(1500, function () {
        var tap = findTap(it);
        if (!tap) { jlog(JSON.stringify({ added: false, error: "tap not found after insert" })); jobState = "failed"; return; }
        cursorTrack.selectChannel(it.track);
        after(500, function () {
            cursorDevice.selectDevice(tap.dev);
            after(1000, function () {
                var rc = slotParam();
                if (!rc) {   // plugin is in; Bitwig just doesn't expose its Slot parameter to scripts
                    jlog(JSON.stringify({ added: true, slot: null, note: "Slot parameter not exposed to scripts",
                                          cursor_device: String(cursorDevice.name().get()), remote_controls: remoteDump() }));
                    jobState = "done"; return;
                }
                rc.value().set((slot - 1) / 63);
                after(600, function () {
                    jlog(JSON.stringify({ added: true, device_index: tap.index, slot: Math.round(Number(rc.value().get()) * 63) + 1 }));
                    jobState = "done";
                });
            });
        });
    });
    return { ok: true, started: true };
}

function trackFail() { return { ok: false, error: "track features unavailable: " + setupError }; }

function argsOf(msg) {
    try { var s = msg.getString(0); return s ? JSON.parse(String(s)) : {}; }
    catch (e) { return {}; }
}

function transportState() {
    return {
        ok: true, playing: !!transport.isPlaying().get(),
        tempo_bpm: r3(transport.tempo().value().getRaw()),
        loop: !!transport.isArrangerLoopEnabled().get(),
        loop_start_beats: r3(transport.arrangerLoopStart().get()),
        loop_length_beats: r3(transport.arrangerLoopDuration().get()),
        metronome: !!transport.isMetronomeEnabled().get(),
        record: !!transport.isArrangerRecordEnabled().get(),
        overdub: !!transport.isArrangerOverdubEnabled().get(),
        position_beats: r3(transport.getPosition().get())
    };
}

function transportSet(a) {
    if (a.tempo_bpm !== undefined) transport.tempo().value().setRaw(Number(a.tempo_bpm));
    if (a.loop !== undefined) transport.isArrangerLoopEnabled().set(!!a.loop);
    if (a.loop_start_beats !== undefined) transport.arrangerLoopStart().set(Number(a.loop_start_beats));
    if (a.loop_length_beats !== undefined) transport.arrangerLoopDuration().set(Number(a.loop_length_beats));
    if (a.metronome !== undefined) transport.isMetronomeEnabled().set(!!a.metronome);
    if (a.record !== undefined) transport.isArrangerRecordEnabled().set(!!a.record);
    if (a.overdub !== undefined) transport.isArrangerOverdubEnabled().set(!!a.overdub);
    if (a.position_beats !== undefined) transport.getPosition().set(Number(a.position_beats));
    return { ok: true, requested: a };  // values apply asynchronously; call /transport_get to read back
}

function tracksList(a) {
    var ex = existing(), off = Number(a.offset || 0), lim = Number(a.limit || 40), out = [];
    for (var i = off; i < ex.length && out.length < lim; i++) out.push(describe(ex[i]));
    return { ok: true, total: ex.length, offset: off, next_offset: off + out.length < ex.length ? off + out.length : null, tracks: out };
}

function trackSet(a) {
    var it = findTrack(a.track);
    if (!it) return { ok: false, error: "no such track: " + a.track };
    var t = it.track;
    if (a.volume !== undefined) t.volume().value().set(Math.max(0, Math.min(1, Number(a.volume))));
    if (a.pan !== undefined) t.pan().value().set((Math.max(-1, Math.min(1, Number(a.pan))) + 1) / 2);
    if (a.mute !== undefined) t.mute().set(!!a.mute);
    if (a.solo !== undefined) t.solo().set(!!a.solo);
    if (a.arm !== undefined) t.arm().set(!!a.arm);
    return { ok: true, requested: a, track: it.id };
}

function metersNow() {
    var out = [], ex = existing();
    for (var i = 0; i < ex.length; i++) {
        var m = meters[ex[i].id];
        out.push({ id: ex[i].id, name: String(ex[i].track.name().get()), peak: r3(m.peak), rms: r3(m.rms) });
    }
    return { ok: true, scale: "bitwig 0-1 (not dB)", playing: !!transport.isPlaying().get(), meters: out };
}

function handle(addr, args) {
    switch (addr) {
        case "/play": transport.play(); return { ok: true, requested: "play" };
        case "/stop": transport.stop(); return { ok: true, requested: "stop" };
        case "/restart": transport.restart(); return { ok: true, requested: "restart" };
        case "/status": return { ok: true, playing: transport.isPlaying().get() };
        case "/transport_get": return transportState();
        case "/transport_set": return transportSet(args);
        case "/tracks": return setupError ? trackFail() : tracksList(args);
        case "/track_set": return setupError ? trackFail() : trackSet(args);
        case "/actions": return actionsList(args);
        case "/make_break": return setupError ? trackFail() : startBreakJob(args);
        case "/record_break": return setupError ? trackFail() : startRecordJob(args);
        case "/return_to_arrangement": return setupError ? trackFail() : returnTrack(args);
        case "/launcher_slots": return setupError ? trackFail() : launcherSlots(args);
        case "/delete_launcher_clip": return setupError ? trackFail() : deleteLauncherClip(args);
        case "/add_clip": return setupError ? trackFail() : startAddClipJob(args);
        case "/instrument": return setupError ? trackFail() : instrumentInfo(args);
        case "/create_track": return setupError ? trackFail() : startCreateTrackJob(args);
        case "/drum_pads": return setupError ? trackFail() : startDrumPadsJob(args);
        case "/tap_status": return setupError ? trackFail() : startTapStatusJob(args);
        case "/add_tap": return setupError ? trackFail() : startAddTapJob(args);
        case "/job": return { ok: true, state: jobState, log: jobLog };
        case "/meters": return setupError ? trackFail() : metersNow();
        default: return { ok: false, error: "unknown address " + addr };
    }
}

function init() {
    transport = host.createTransport();
    application = host.createApplication();
    transport.timeSignature().markInterested();
    cursorTrack = host.createCursorTrack("bitwig-ears", "bitwig-ears", 0, 0, false);
    cursorDevice = cursorTrack.createCursorDevice();
    cursorTrack.name().markInterested();
    cursorDevice.name().markInterested();
    cursorDevice.hasDrumPads().markInterested();
    padBank = cursorDevice.createDrumPadBank(32);
    for (var q = 0; q < 32; q++) {
        var pad = padBank.getItemAt(q);
        pad.exists().markInterested(); pad.name().markInterested();
        pad.mute().markInterested(); pad.solo().markInterested();
    }
    padBank.scrollPosition().markInterested();
    cursorRemote = cursorDevice.createCursorRemoteControlsPage(8);
    for (var r = 0; r < 8; r++) {
        var rc = cursorRemote.getParameter(r);
        rc.name().markInterested(); rc.value().markInterested(); rc.exists().markInterested();
    }
    transport.isPunchInEnabled().markInterested();
    transport.isPunchOutEnabled().markInterested();
    launcherCursor = host.createLauncherCursorClip(64, 128);
    launcherCursor.exists().markInterested();
    launcherCursor.getLoopLength().markInterested();
    launcherCursor.getLoopStart().markInterested();
    launcherCursor.getPlayStart().markInterested();
    launcherCursor.getPlayStop().markInterested();
    launcherCursor.getTrack().name().markInterested();
    arrangerCursor = host.createArrangerCursorClip(16, 128);
    arrangerCursor.exists().markInterested();
    arrangerCursor.getLoopLength().markInterested();
    arrangerCursor.getTrack().name().markInterested();
    transport.isPlaying().markInterested();
    transport.tempo().value().markInterested();
    transport.isArrangerLoopEnabled().markInterested();
    transport.arrangerLoopStart().markInterested();
    transport.arrangerLoopDuration().markInterested();
    transport.isMetronomeEnabled().markInterested();
    transport.isArrangerRecordEnabled().markInterested();
    transport.isArrangerOverdubEnabled().markInterested();
    transport.getPosition().markInterested();
    try { setupTracks(); }
    catch (e) {
        setupError = String(e);
        host.showPopupNotification("bitwig-ears: track setup failed (transport still works): " + e);
    }

    var osc = host.getOscModule();
    var space = osc.createAddressSpace();
    var out = osc.connectToUdpServer("127.0.0.1", REPLY_PORT, space);
    space.registerDefaultMethod(function (source, msg) {
        var reply;
        try { reply = handle(String(msg.getAddressPattern()), argsOf(msg)); }
        catch (e) { reply = { ok: false, error: String(e) }; }
        try { out.sendMessage("/reply", JSON.stringify(reply)); }
        catch (e) { host.showPopupNotification("bitwig-ears: reply failed " + e); }
    });
    osc.createUdpServer(PORT, space);

    println("bitwig-ears transport bridge (OSC/UDP) on port " + PORT);
    host.showPopupNotification("bitwig-ears v0.23 loaded (OSC in " + PORT + ", out " + REPLY_PORT + ")");
}

function flush() {}
function exit() {}
