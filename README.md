# bitwig-ears

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant work inside **Bitwig Studio**:
read the project, control the transport and mixer, **hear individual tracks**, and write MIDI clips into the
arrangement.

It has three parts that talk to each other on `127.0.0.1`:

```
 Claude / MCP client
        │ stdio (MCP)
        ▼
 server/   (Python)  ──── TCP 7878 ◄──── plugin/   CLAP "Bitwig Ears Tap"   (audio, per track)
        │
        │ OSC over UDP: commands → 7879, replies ← 7880
        ▼
 bitwig/   Bitwig controller script  (transport, tracks, clips, devices)
```

- **`plugin/`**: a small Rust CLAP plugin (built with [nih-plug](https://github.com/robbert-vdh/nih-plug)). Put it
  on any track. It passes audio through unchanged and mirrors it to the server. Bitwig's own API cannot read
  audio, so this is how tracks are "heard". Only tracks that carry the plugin can be listened to.
- **`server/`**: the MCP server (Python, [FastMCP](https://github.com/modelcontextprotocol/python-sdk)). Keeps the
  last 60 seconds of audio per tap and exposes the tools below.
- **`bitwig/`**: a Bitwig controller script (JavaScript, API 18). It exposes the parts of Bitwig that the
  Controller API allows.

## Status

Built and exercised on **macOS (Apple Silicon) with Bitwig Studio 5.3.13**. It works end to end there. Nothing
has been tried on Windows or Linux. The plugin is only ad-hoc signed.

## Tools

Audio (needs the tap plugin on the track)

| Tool | What it does |
|---|---|
| `list_tracks` | Audio streams currently connected from tap plugins |
| `get_levels(track, seconds)` | Peak, RMS (dBFS) and dominant frequency of a stream |
| `get_audio(track, seconds)` | The last N seconds (max 30) as a WAV |
| `tap_status(track)` | Does a Bitwig track carry the tap plugin? |
| `ensure_tap(track)` | Add the tap plugin to a track (changes the project) |

Transport and mixer

| Tool | What it does |
|---|---|
| `transport(action)` | `play`, `stop`, `restart`, `status` |
| `transport_settings(...)` | Read or set tempo, loop, metronome, record, overdub, position |
| `bitwig_tracks` | All tracks: name, type, color, mute/solo/arm, volume, pan |
| `set_bitwig_track` | Change volume, pan, mute, solo, arm |
| `bitwig_meters` | Bitwig's own peak/RMS meters (0–1 scale) |

Instruments and clips

| Tool | What it does |
|---|---|
| `track_instrument(track)` | Devices on a track (name, type, preset, drum pads) |
| `drum_pads(track)` | The real pad map of a drum device (kits differ; never assume GM) |
| `find_presets(query)` | Search the local Bitwig library for `.bwpreset` files |
| `create_instrument_track(name, preset_path)` | New instrument track, optionally with a preset |
| `add_clip_to_arrangement(track, description, notes, start_beats, length_beats)` | Write a MIDI clip into the **arrangement** |

`add_clip_to_arrangement` takes concrete notes. The calling assistant turns a natural-language request into
notes after looking at `track_instrument` / `drum_pads`.

## How clips get into the arrangement

Bitwig's API cannot create arranger clips. The script writes the notes into a temporary launcher clip, turns on
arranger record with punch in/out over exactly the clip's span (with the loop off), launches the clip, then
returns the track to the arrangement and deletes the temporary clip. It takes about the clip length plus three
seconds, briefly plays the transport, and restores loop/punch/record settings and the playhead.
**Recording replaces anything already in that time range on that track.**

## Setup

Requirements: Bitwig Studio 5.3 or newer, Rust (`cargo`), [`uv`](https://docs.astral.sh/uv/), macOS.

1. **Plugin.** From `plugin/`: `./bundle-macos.sh --install`. This builds, wraps it as a `.clap` bundle,
   signs it ad hoc, and copies it to `~/Library/Audio/Plug-Ins/CLAP`. Rescan plugins in Bitwig if it is not
   listed.
2. **Controller script.** Copy `bitwig/bitwig-ears.control.js` into
   `~/Documents/Bitwig Studio/Controller Scripts/bitwig-ears/`. In Bitwig: Settings → Controllers → Add
   controller → vendor `bitwig-ears`, model `Transport Bridge`. A "loaded" popup confirms it. After you change
   the script, remove and re-add the controller to reload it.
3. **MCP server.** Register it with your client, for example with Claude Code:
   ```
   claude mcp add bitwig-ears -- uv run --directory /path/to/bitwig-ears/server server.py
   ```
4. Put **Bitwig Ears Tap** on any track you want to hear, or use `ensure_tap`.

Ports used (all localhost): TCP 7878 (plugin → server), UDP 7879 (server → script), UDP 7880 (script → server).
Only one server instance can run at a time. `BITWIG_EARS_INGEST_PORT` changes the TCP port for tests.

## Limitations and things to know

- **Slot ids.** Bitwig does not expose the plugin's `Slot` parameter to scripts, so taps cannot be given unique
  slots automatically. Several taps with the same Slot get ids like `slot-1`, `slot-1-2`, `slot-1-3` in connect
  order, and `ensure_tap` identifies a new tap by which stream appears. Set distinct Slots by hand if you need
  stable ids across restarts.
- **Listening is measurement, not hearing.** `get_levels` and `get_audio` give levels, frequency and a WAV file.
  They show whether a signal is present and roughly what pitch, not how it sounds. Pitch estimates on short
  synth notes can be off by an octave.
- **Mute does not silence a tap.** A track's mute acts after its device chain, so a tap inside the chain still
  hears a muted track.
- **Device and track limits.** Track listing covers the first 64 tracks; device inspection reads the first
  three top-level devices of a track.
- **The `bitwig_meters` scale** is Bitwig's 0–1 value, not dB, and needs the transport playing.

## Tests

From `server/`, with `uv run`: `e2e_test.py` (audio path with a fake plugin), `transport_test.py`,
`new_tools_test.py`, `add_clip_test.py`, `tap_tools_test.py`, `drum_pads_test.py`. They use stub scripts on
alternate ports and do not touch a real Bitwig. The `bw_*.py` files in `server/` are small helpers that talk
to a real, running Bitwig.

## Protocol

See [PROTOCOL.md](PROTOCOL.md).

## License

MIT, see [LICENSE](LICENSE).
