"""MCP server: sees only tracks that carry the tap plugin, serves their audio."""
import asyncio, base64, io, json, struct, threading, time, wave
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import numpy as np
from mcp.server.fastmcp import FastMCP
from mcp.types import AudioContent, TextContent

import os
INGEST_PORT = int(os.environ.get("BITWIG_EARS_INGEST_PORT", "7878"))  # default unchanged
KEEP_SECONDS = 60

@dataclass
class Track:
    slot: int
    sample_rate: float
    channels: int
    connected_at: float = field(default_factory=time.time)
    buf: np.ndarray = None
    total_frames: int = 0   # frames ever received
    connected: bool = True

    def __post_init__(self):
        self.buf = np.zeros((0, self.channels), dtype=np.float32)

    def push(self, samples: np.ndarray):
        frames = samples.reshape(-1, self.channels)
        self.total_frames += len(frames)
        keep = int(self.sample_rate * KEEP_SECONDS)
        self.buf = np.concatenate([self.buf, frames])[-keep:]

    def last(self, seconds: float) -> np.ndarray:
        return self.buf[-int(self.sample_rate * seconds):]

tracks: dict[str, Track] = {}

async def handle_plugin(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    tid = None
    try:
        while True:
            t, n = struct.unpack("<BI", await reader.readexactly(5))
            payload = await reader.readexactly(n)
            if t == 1:
                h = json.loads(payload)
                tid = f"slot-{h['slot']}"
                n = 2
                while tid in tracks and tracks[tid].connected:  # same Slot on two tracks
                    tid = f"slot-{h['slot']}-{n}"; n += 1
                tracks[tid] = Track(h["slot"], h["sample_rate"], h.get("channels", 2))
            elif t == 2 and tid:
                tracks[tid].push(np.frombuffer(payload, dtype="<f4"))
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        if tid in tracks:
            tracks[tid].connected = False
        writer.close()

def to_wav(a: np.ndarray, sr: int) -> bytes:
    pcm = (np.clip(a, -1, 1) * 32767).astype("<i2")
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(a.shape[1]); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return bio.getvalue()

def db(x: float) -> float:
    return round(float(20 * np.log10(max(x, 1e-9))), 1)

@asynccontextmanager
async def lifespan(_app):
    server = await asyncio.start_server(handle_plugin, "127.0.0.1", INGEST_PORT)
    async with server:
        yield

mcp = FastMCP("bitwig-ears", lifespan=lifespan)

def get(track: str) -> Track:
    if track not in tracks:
        raise ValueError(f"unknown track '{track}'; call list_tracks")
    return tracks[track]

@mcp.tool()
def list_tracks() -> list[dict]:
    """Tracks that currently have the tap plugin loaded (the only ones visible)."""
    return [{"track": k, "sample_rate": t.sample_rate, "channels": t.channels,
             "connected": t.connected, "buffered_seconds": round(len(t.buf) / t.sample_rate, 2)}
            for k, t in tracks.items()]

@mcp.tool()
def get_levels(track: str, seconds: float = 1.0) -> dict:
    """Peak and RMS (dBFS) over the last N seconds, plus dominant frequency."""
    t = get(track); a = t.last(seconds)
    if not len(a):
        return {"error": "no audio buffered"}
    mono = a.mean(axis=1)
    spec = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
    f = np.fft.rfftfreq(len(mono), 1 / t.sample_rate)
    return {"peak_db": db(float(np.abs(a).max())), "rms_db": db(float(np.sqrt((a ** 2).mean()))),
            "dominant_hz": round(float(f[spec.argmax()]), 1) if len(spec) > 1 else 0.0}

@mcp.tool()
def get_audio(track: str, seconds: float = 5.0):
    """Last N seconds (max 30) from a track as 16-bit WAV audio."""
    t = get(track); a = t.last(min(seconds, 30))
    if not len(a):
        return [TextContent(type="text", text="no audio buffered (is the transport playing?)")]
    return [AudioContent(type="audio", mimeType="audio/wav",
                         data=base64.b64encode(to_wav(a, int(t.sample_rate))).decode())]

CONTROL_PORT = 7879  # Bitwig controller script listens here (OSC/UDP)
REPLY_PORT = 7880    # ...and answers to this port
_osc_lock = threading.Lock()

def _osc_pad(b: bytes) -> bytes:
    b += b"\0"
    return b + b"\0" * (-len(b) % 4)

def _osc_read_str(data: bytes, i: int):
    j = data.index(b"\0", i)
    return data[i:j].decode(), (j + 4) // 4 * 4

def osc_send_recv(address: str, arg: dict | None = None, timeout: float = 2.0) -> dict:
    """Send an OSC message (optional JSON string argument) and parse the '/reply' JSON-string answer."""
    import socket
    with _osc_lock:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", REPLY_PORT))
        s.settimeout(timeout)
        try:
            if arg is None:
                pkt = _osc_pad(address.encode()) + _osc_pad(b",")
            else:
                pkt = _osc_pad(address.encode()) + _osc_pad(b",s") + _osc_pad(json.dumps(arg).encode())
            s.sendto(pkt, ("127.0.0.1", CONTROL_PORT))
            data, _ = s.recvfrom(65536)
        finally:
            s.close()
    addr, i = _osc_read_str(data, 0)
    tags, i = _osc_read_str(data, i)
    if addr != "/reply" or not tags.startswith(",s"):
        raise ValueError(f"unexpected OSC reply {addr} {tags}")
    return json.loads(_osc_read_str(data, i)[0])

@mcp.tool()
async def transport(action: str = "status") -> dict:
    """Control Bitwig's transport: 'play', 'stop', 'restart' or 'status'.
    Needs the bitwig-ears controller script enabled in Bitwig (Settings > Controllers)."""
    if action not in ("play", "stop", "restart", "status"):
        raise ValueError("action must be play, stop, restart or status")
    try:
        reply = await asyncio.to_thread(osc_send_recv, "/" + action)
        if action != "status" and reply.get("ok"):
            await asyncio.sleep(0.15)  # let Bitwig apply it, then report the real state
            reply["playing"] = (await asyncio.to_thread(osc_send_recv, "/status")).get("playing")
        return reply
    except (OSError, ValueError) as e:  # includes socket timeout
        return {"ok": False, "error": "Bitwig controller script did not answer on UDP port "
                f"{CONTROL_PORT} ({e}). Add 'bitwig-ears / Transport Bridge' in Bitwig and reload it."}

NO_SCRIPT = ("Bitwig controller script did not answer on UDP port {port} ({e}). "
             "Add 'bitwig-ears / Transport Bridge' in Bitwig (v0.8+) and reload it.")

async def _bitwig(address: str, arg: dict | None = None) -> dict:
    try:
        return await asyncio.to_thread(osc_send_recv, address, arg)
    except (OSError, ValueError) as e:  # includes socket timeout
        return {"ok": False, "error": NO_SCRIPT.format(port=CONTROL_PORT, e=e)}

@mcp.tool()
async def transport_settings(tempo_bpm: float | None = None, loop: bool | None = None,
                             loop_start_beats: float | None = None, loop_length_beats: float | None = None,
                             metronome: bool | None = None, record: bool | None = None,
                             overdub: bool | None = None, position_beats: float | None = None) -> dict:
    """Read or change Bitwig transport settings. With no arguments, returns the current
    state (playing, tempo, loop, metronome, record, overdub, position). Any argument given
    is applied, then the state is read back."""
    changes = {k: v for k, v in dict(tempo_bpm=tempo_bpm, loop=loop, loop_start_beats=loop_start_beats,
               loop_length_beats=loop_length_beats, metronome=metronome, record=record,
               overdub=overdub, position_beats=position_beats).items() if v is not None}
    if changes:
        r = await _bitwig("/transport_set", changes)
        if not r.get("ok"):
            return r
        await asyncio.sleep(0.2)  # let Bitwig apply, then report the real state
    return await _bitwig("/transport_get")

@mcp.tool()
async def bitwig_tracks(offset: int = 0, limit: int = 40) -> dict:
    """List ALL Bitwig tracks (incl. group children, effect tracks, master): id, name, type,
    color, mute/solo/arm, volume and pan. Ids ('t0', 't1', ...) follow Bitwig's track order and shift if
    tracks are added or moved, so prefer names for anything long-lived. Separate from list_tracks, which shows only tracks carrying the tap plugin."""
    return await _bitwig("/tracks", {"offset": offset, "limit": limit})

@mcp.tool()
async def set_bitwig_track(track: str, volume: float | None = None, pan: float | None = None,
                           mute: bool | None = None, solo: bool | None = None,
                           arm: bool | None = None) -> dict:
    """Change a track's mixer state. 'track' is an id from bitwig_tracks or the exact track
    name. volume is Bitwig's 0-1 fader scale (0.8 is about 0 dB), pan is -1 (left) to 1 (right)."""
    changes = {k: v for k, v in dict(volume=volume, pan=pan, mute=mute, solo=solo, arm=arm).items()
               if v is not None}
    if not changes:
        return {"ok": False, "error": "nothing to change: pass volume, pan, mute, solo or arm"}
    return await _bitwig("/track_set", {"track": track, **changes})

@mcp.tool()
async def bitwig_meters() -> dict:
    """Bitwig's own peak and RMS meter per track (0-1 scale, not dB; needs the audio engine
    running and the transport playing). No plugin needed. For dB and spectrum use get_levels."""
    return await _bitwig("/meters")

@mcp.tool()
async def track_instrument(track: str) -> dict:
    """What is on a Bitwig track: its first 3 top-level devices (name, type, plugin, preset,
    whether it has drum pads). Call this BEFORE writing a clip, so the notes suit the instrument.
    If a device has drum_pads, call drum_pads(track) too: kits differ, so never assume a General-MIDI
    layout (a "V9 Breaks Kit" has snare on 37, closed hat on 38 and a rimshot on 42). A synth, bass or
    sampler wants pitched notes in a sensible register."""
    return await _bitwig("/instrument", {"track": track})

@mcp.tool()
async def drum_pads(track: str) -> dict:
    """The real pad map of the Bitwig Drum Machine (or other drum device) on a track: MIDI note and pad
    name, e.g. {note: 37, name: 'Breaks Snare'}. ALWAYS call this before writing drum notes: kits
    differ and nothing here assumes a General-MIDI layout. Briefly selects the drum device in Bitwig's
    UI (about 2 seconds); changes nothing in the project."""
    started = await _bitwig("/drum_pads", {"track": track})
    if not started.get("ok"):
        return started
    for _ in range(20):
        await asyncio.sleep(0.4)
        job = await _bitwig("/job")
        if not job.get("ok"):
            return job
        if job["state"] == "running":
            continue
        if job["state"] != "done":
            return {"ok": False, "state": job["state"], "log": job["log"]}
        try:
            info, pads = job["log"][-2], json.loads(job["log"][-1])
            scroll = int(info.split("scroll=")[1].split()[0])
        except (IndexError, ValueError):
            return {"ok": False, "error": "could not read the pad list", "log": job["log"]}
        device = info.split("device=")[1].split(" hasDrumPads")[0]
        return {"ok": True, "device": device, "first_note": scroll,
                "pads": [{"note": scroll + p["index"], "name": p["name"]} for p in pads]}
    return {"ok": False, "error": "timed out reading pads"}

@mcp.tool()
async def add_clip_to_arrangement(track: str, description: str, notes: list[dict], start_beats: float = 0,
                                  length_beats: int = 4) -> dict:
    """Create a MIDI clip directly in Bitwig's ARRANGEMENT on an Instrument track.
    Workflow: take the user's natural-language request, call track_instrument to see what is on the track,
    then write the notes yourself to fit that instrument and the requested length, and pass them here.
    `description` is a short label for the clip (it names it in Bitwig). `notes` is a list of
    {start, pitch, velocity, duration}: start and duration in beats from the clip start, pitch 0-127,
    velocity 1-127 (default 100), at most 200 notes; notes starting outside the clip are dropped. The clip
    is placed at `start_beats` (0 = bar 1.1) and is `length_beats` long (integer 1-16). Takes about
    length + 3 seconds: it briefly plays the transport, records the clip into the arrangement, then
    restores loop/punch/record settings and the playhead.
    WARNING: recording replaces anything already in that time range on that track."""
    if not notes:
        return {"ok": False, "error": "`notes` is empty"}
    if len(notes) > 200:
        return {"ok": False, "error": "too many notes (max 200)"}
    try:
        wire = [{"s": float(n["start"]), "p": int(n["pitch"]), "v": int(n.get("velocity", 100)),
                 "d": float(n.get("duration", 0.25))} for n in notes]
    except (KeyError, TypeError, ValueError) as e:
        return {"ok": False, "error": f"each note needs start and pitch (optional velocity, duration): {e}"}
    if not 1 <= length_beats <= 16 or start_beats < 0:
        return {"ok": False, "error": "length_beats must be 1-16 and start_beats >= 0"}
    started = await _bitwig("/add_clip", {"track": track, "notes": wire, "start_beats": start_beats,
                                          "length_beats": length_beats, "name": description})
    if not started.get("ok"):
        return started
    deadline = time.time() + length_beats * 2 + 15   # generous: even a slow tempo finishes well inside this
    while time.time() < deadline:
        await asyncio.sleep(0.6)
        job = await _bitwig("/job")
        if not job.get("ok"):
            return job
        if job["state"] != "running":
            return {"ok": job["state"] == "done", "state": job["state"], "log": job["log"]}
    return {"ok": False, "state": "timeout", "log": job.get("log"),
            "error": "job did not finish in time; check Bitwig and the transport settings"}

PRESET_DIRS = [
    "/Applications/Bitwig Studio.app/Contents/Resources/Library/presets",
    os.path.expanduser("~/Library/Application Support/Bitwig/Bitwig Studio/installed-packages"),
    os.path.expanduser("~/Documents/Bitwig Studio/Library"),
]

@mcp.tool()
def find_presets(query: str, limit: int = 25) -> dict:
    """Search the local Bitwig library for preset files (*.bwpreset) whose path contains ALL the words
    in `query` (case-insensitive), e.g. 'bass mono'. Returns absolute paths usable as `preset_path` in
    create_instrument_track. Reads file names only."""
    words = [w.lower() for w in query.split() if w]
    if not words:
        return {"ok": False, "error": "give at least one search word"}
    hits = []
    for root in PRESET_DIRS:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.lower().endswith(".bwpreset"):
                    full = os.path.join(dirpath, f)
                    if all(w in full.lower() for w in words):
                        hits.append(full)
    hits.sort()
    return {"ok": True, "total": len(hits), "presets": hits[:max(1, min(limit, 100))]}

@mcp.tool()
async def create_instrument_track(name: str, preset_path: str | None = None) -> dict:
    """Create a new Instrument track at the end of the track list in Bitwig, name it, and optionally load
    an instrument preset (an absolute .bwpreset path from find_presets) so it makes sound. Returns the
    job log, including the devices that ended up on the track. Then use track_instrument and
    add_clip_to_arrangement on it. Takes about 3 seconds."""
    if preset_path is not None:
        if not preset_path.lower().endswith(".bwpreset") or not os.path.isfile(preset_path):
            return {"ok": False, "error": "preset_path must be an existing .bwpreset file (see find_presets)"}
    started = await _bitwig("/create_track", {"name": name, "preset_path": preset_path})
    if not started.get("ok"):
        return started
    for _ in range(30):
        await asyncio.sleep(0.5)
        job = await _bitwig("/job")
        if not job.get("ok"):
            return job
        if job["state"] != "running":
            return {"ok": job["state"] == "done", "state": job["state"], "log": job["log"]}
    return {"ok": False, "state": "timeout", "error": "job did not finish; check Bitwig"}

async def _run_job(address: str, arg: dict, tries: int = 40) -> dict:
    """Start a script job and wait for it; returns {'ok', 'state', 'log'} or an error dict."""
    started = await _bitwig(address, arg)
    if not started.get("ok"):
        return started
    for _ in range(tries):
        await asyncio.sleep(0.5)
        job = await _bitwig("/job")
        if not job.get("ok"):
            return job
        if job["state"] != "running":
            return {"ok": job["state"] == "done", "state": job["state"], "log": job["log"]}
    return {"ok": False, "state": "timeout", "error": "job did not finish; check Bitwig"}

def _last_json(job: dict) -> dict | None:
    try:
        return json.loads(job["log"][-1])
    except (KeyError, IndexError, ValueError):
        return None

@mcp.tool()
async def tap_status(track: str) -> dict:
    """Does this Bitwig track carry the 'Bitwig Ears Tap' plugin, and which audio slot is it on?
    ALWAYS call this for every track before you analyze its audio: get_levels/get_audio take SLOT
    ids ('slot-12'), not track names, and a track without the tap cannot be heard at all (a slot
    that reads silence may simply belong to another track). Returns has_tap, slot_id and whether that
    slot is currently streaming. Briefly selects the tap device in Bitwig's UI (about 2 seconds)."""
    job = await _run_job("/tap_status", {"track": track})
    info = _last_json(job) if job.get("ok") else None
    if info is None:
        return {"ok": False, "track": track, "error": job.get("error", "no result from Bitwig"), "log": job.get("log")}
    if not info.get("has_tap"):
        return {"ok": True, "track": track, "has_tap": False,
                "advice": "no tap on this track; call ensure_tap(track) to add one"}
    if info.get("slot") is None:
        return {"ok": True, "track": track, "has_tap": True, "slot_id": None,
                "note": "tap is on this track, but Bitwig does not expose its Slot parameter, so the slot id is not known. "
                        "Find it by muting the track while playing and seeing which slot's level drops.",
                "remote_controls": info.get("remote_controls")}
    sid = f"slot-{info['slot']}"
    live = tracks.get(sid)
    return {"ok": True, "track": track, "has_tap": True, "slot_id": sid,
            "streaming": bool(live and live.connected),
            "buffered_seconds": round(len(live.buf) / live.sample_rate, 1) if live else 0,
            "note": None if live else "plugin has a Slot but is not connected to this server (project closed or plugin disabled?)"}

@mcp.tool()
async def ensure_tap(track: str) -> dict:
    """Make sure a Bitwig track carries the tap plugin so its audio can be analyzed: if it already has
    one, report it (tap_status); otherwise insert the plugin at the end of the track's device chain and
    give it a free Slot. CHANGES THE PROJECT (adds one plugin to the track). Takes about 6 seconds."""
    status = await tap_status(track)
    if not status.get("ok") or status.get("has_tap"):
        return {**status, "added": False}
    before = set(tracks)
    # Slot can't be set from the script (Bitwig does not expose the parameter), so the plugin keeps its
    # default Slot and the server names the new stream by connection order; we identify it by diff.
    job = await _run_job("/add_tap", {"track": track, "slot": 1})
    info = _last_json(job) if job.get("ok") else None
    if not info or not info.get("added"):
        return {"ok": False, "track": track, "added": False, "error": job.get("error") or (info or {}).get("error"), "log": job.get("log")}
    new = []
    for _ in range(25):
        new = [k for k in tracks if k not in before and tracks[k].connected]
        if new:
            break
        await asyncio.sleep(0.4)
    if len(new) != 1:
        return {"ok": False, "track": track, "added": True, "new_streams": new,
                "error": "tap was added but exactly one new audio stream did not appear; verify with a mute test"}
    return {"ok": True, "track": track, "added": True, "slot_id": new[0], "streaming": True,
            "verify": "mapped by connection order; confirm with a mute test before relying on it"}

