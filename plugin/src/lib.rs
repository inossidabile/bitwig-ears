//! Transparent pass-through CLAP that mirrors its input to the bitwig-ears server.
use nih_plug::prelude::*;
use std::io::Write;
use std::net::TcpStream;
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::Duration;

const ADDR: &str = "127.0.0.1:7878";
const RING_SAMPLES: usize = 48_000 * 2 * 2; // ~2 s stereo

#[derive(Params)]
struct TapParams {
    /// Identifies this track to the MCP server as "slot-<n>".
    #[id = "slot"]
    slot: IntParam,
    /// Debug: mirror a 440 Hz tone instead of the track audio (pass-through is unaffected).
    #[id = "tone"]
    test_tone: BoolParam,
}

struct Tap {
    params: Arc<TapParams>,
    producer: Option<rtrb::Producer<f32>>,
    sample_rate: f32,
    phase: f32,
    stop: Arc<std::sync::atomic::AtomicBool>,
    worker: Option<JoinHandle<()>>,
}

impl Default for Tap {
    fn default() -> Self {
        Self {
            params: Arc::new(TapParams { slot: IntParam::new("Slot", 1, IntRange::Linear { min: 1, max: 64 }),
                test_tone: BoolParam::new("Test tone", false),
            }),
            producer: None,
            sample_rate: 48000.0,
            phase: 0.0,
            stop: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            worker: None,
        }
    }
}

fn frame(t: u8, payload: &[u8]) -> Vec<u8> {
    let mut v = Vec::with_capacity(5 + payload.len());
    v.push(t);
    v.extend_from_slice(&(payload.len() as u32).to_le_bytes());
    v.extend_from_slice(payload);
    v
}

/// Off-thread: connect (retrying), say HELLO, then drain the ring to the socket.
fn sender(mut cons: rtrb::Consumer<f32>, params: Arc<TapParams>, sr: f32, stop: Arc<std::sync::atomic::AtomicBool>) {
    use std::sync::atomic::Ordering::Relaxed;
    let mut buf: Vec<u8> = Vec::new();
    while !stop.load(Relaxed) {
        let addr: std::net::SocketAddr = ADDR.parse().unwrap();
        let Ok(mut s) = TcpStream::connect_timeout(&addr, Duration::from_millis(200)) else {
            while cons.pop().is_ok() {} // discard while nobody listens
            thread::sleep(Duration::from_secs(1));
            continue;
        };
        let _ = s.set_nodelay(true);
        let _ = s.set_write_timeout(Some(Duration::from_millis(200)));
        let hello = serde_json::json!({
            "slot": params.slot.value(), "sample_rate": sr, "channels": 2,
            "plugin_version": env!("CARGO_PKG_VERSION")
        });
        if s.write_all(&frame(1, hello.to_string().as_bytes())).is_err() { thread::sleep(Duration::from_secs(1)); continue; }
        while !stop.load(Relaxed) {
            buf.clear();
            while let Ok(x) = cons.pop() { buf.extend_from_slice(&x.to_le_bytes()); }
            if buf.is_empty() { thread::sleep(Duration::from_millis(5)); continue; }
            if s.write_all(&frame(2, &buf)).is_err() { break; } // reconnect
        }
    }
}

impl Plugin for Tap {
    const NAME: &'static str = "Bitwig Ears Tap";
    const VENDOR: &'static str = "bitwig-ears";
    const URL: &'static str = "";
    const EMAIL: &'static str = "";
    const VERSION: &'static str = env!("CARGO_PKG_VERSION");

    const AUDIO_IO_LAYOUTS: &'static [AudioIOLayout] = &[AudioIOLayout {
        main_input_channels: NonZeroU32::new(2),
        main_output_channels: NonZeroU32::new(2),
        ..AudioIOLayout::const_default()
    }];
    type SysExMessage = ();
    type BackgroundTask = ();

    fn params(&self) -> Arc<dyn Params> { self.params.clone() }

    fn initialize(&mut self, _: &AudioIOLayout, cfg: &BufferConfig, _: &mut impl InitContext<Self>) -> bool {
        self.shutdown();
        self.stop = Arc::new(std::sync::atomic::AtomicBool::new(false)); // fresh flag so a detached old worker still exits
        let (prod, cons) = rtrb::RingBuffer::new(RING_SAMPLES);
        self.producer = Some(prod);
        self.sample_rate = cfg.sample_rate;
        let (p, sr, stop) = (self.params.clone(), cfg.sample_rate, self.stop.clone());
        self.worker = Some(thread::spawn(move || sender(cons, p, sr, stop)));
        true
    }

    fn process(&mut self, buffer: &mut Buffer, _: &mut AuxiliaryBuffers, _: &mut impl ProcessContext<Self>) -> ProcessStatus {
        let tone = self.params.test_tone.value();
        let step = 440.0 / self.sample_rate;
        if let Some(prod) = self.producer.as_mut() {
            for mut frame in buffer.iter_samples() {
                let t = if tone {
                    self.phase = (self.phase + step).fract();
                    Some(0.25 * (self.phase * std::f32::consts::TAU).sin())
                } else { None };
                // Never block: if the ring is full the sample is dropped.
                for ch in 0..2 { let x = t.unwrap_or(*frame.get_mut(ch).unwrap()); let _ = prod.push(x); }
            }
        }
        ProcessStatus::Normal // audio passes through untouched
    }

    fn deactivate(&mut self) { self.shutdown(); }
}

impl Tap {
    fn shutdown(&mut self) {
        self.stop.store(true, std::sync::atomic::Ordering::Relaxed);
        // Never join: Bitwig calls this on a thread that must not block. The worker
        // sees the stop flag within one write timeout and exits on its own.
        drop(self.worker.take());
        self.producer = None;
    }
}

impl ClapPlugin for Tap {
    const CLAP_ID: &'static str = "io.bitwig-ears.tap";
    const CLAP_DESCRIPTION: Option<&'static str> = Some("Mirrors track audio to the bitwig-ears MCP server");
    const CLAP_MANUAL_URL: Option<&'static str> = None;
    const CLAP_SUPPORT_URL: Option<&'static str> = None;
    const CLAP_FEATURES: &'static [ClapFeature] = &[ClapFeature::AudioEffect, ClapFeature::Utility];
}

nih_export_clap!(Tap);
