# Tap protocol (plugin -> server), TCP 127.0.0.1:7878

Frame: [u8 type][u32 LE payload_len][payload]

| type | payload |
|------|---------|
| 1 HELLO | JSON: {"slot": int, "sample_rate": float, "channels": 2, "plugin_version": str} |
| 2 AUDIO | interleaved f32 LE samples, channels per HELLO |

One TCP connection per plugin instance. Plugin reconnects on drop.
A track is "visible" iff a connection is open. Track id = "slot-<n>" (n = plugin's Slot param).
Audio thread never blocks: it writes to a lock-free ring; a worker thread sends. Overflow = dropped samples.

## Control protocol (server <-> Bitwig controller script)

The script uses Bitwig's OSC module. The server sends an OSC message to UDP `127.0.0.1:7879`; the script
replies with a single OSC message `/reply` carrying one JSON string, sent to UDP `127.0.0.1:7880`.
Arguments are one JSON string (some commands take none).

| Address | Argument | Reply |
|---|---|---|
| `/play` `/stop` `/restart` `/status` | none | `{ok, playing}` |
| `/transport_get`, `/transport_set` | `{tempo_bpm, loop, loop_start_beats, loop_length_beats, metronome, record, overdub, position_beats}` | transport state |
| `/tracks` | `{offset, limit}` | `{total, tracks[]}` |
| `/track_set` | `{track, volume, pan, mute, solo, arm}` | `{ok}` |
| `/meters` | none | `{meters[]}` |
| `/instrument`, `/drum_pads` | `{track}` | devices / pads |
| `/actions` | `{filter, limit}` | matching Bitwig action ids (read-only) |
| `/return_to_arrangement` | `{track}` | `{ok}` |
| `/launcher_slots`, `/delete_launcher_clip` | `{track[, slot]}` | slot state / `{ok}` |

Long operations are jobs: the command returns `{ok, started}` immediately and the result is polled with `/job`
(`{state: running|done|failed|unverified, log[]}`):

| Address | Argument |
|---|---|
| `/add_clip` | `{track, notes[{s,p,v,d}], start_beats, length_beats, name}`: record notes into the arrangement |
| `/create_track` | `{name, preset_path}` |
| `/tap_status`, `/add_tap` | `{track}` / `{track, slot}` |

Datagrams must stay under ~9 KB (macOS loopback limit), which bounds the number of notes per call (the server
caps it at 200) and the page size of `/tracks`.
