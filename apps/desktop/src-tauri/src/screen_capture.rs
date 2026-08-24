//! macOS ScreenCaptureKit stream with a bounded latest-frame buffer.

#![cfg(target_os = "macos")]

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use block2::RcBlock;
use objc2::runtime::ProtocolObject;
use objc2::{define_class, msg_send, AnyThread, DefinedClass};
use objc2_core_video::{
    CVPixelBufferGetBaseAddress, CVPixelBufferGetBytesPerRow, CVPixelBufferGetHeight,
    CVPixelBufferGetWidth, CVPixelBufferLockBaseAddress, CVPixelBufferLockFlags,
    CVPixelBufferUnlockBaseAddress,
};
use objc2_foundation::{NSArray, NSError, NSObject, NSObjectProtocol};
use objc2_screen_capture_kit::{
    SCContentFilter, SCDisplay, SCShareableContent, SCStream, SCStreamConfiguration,
    SCStreamOutput, SCStreamOutputType,
};

#[derive(Clone)]
pub struct ScreenFrame {
    pub png: Vec<u8>,
    pub width: usize,
    pub height: usize,
    captured_at: Instant,
}

type SharedFrame = Arc<Mutex<Option<ScreenFrame>>>;

struct OutputState {
    frame_count: AtomicU64,
    latest: SharedFrame,
}

define_class!(
    #[unsafe(super(NSObject))]
    #[thread_kind = AnyThread]
    #[ivars = OutputState]
    struct ScreenStreamOutput;

    unsafe impl NSObjectProtocol for ScreenStreamOutput {}

    unsafe impl SCStreamOutput for ScreenStreamOutput {
        #[unsafe(method(stream:didOutputSampleBuffer:ofType:))]
        unsafe fn stream_did_output_sample_buffer_of_type(
            &self,
            _stream: &SCStream,
            sample_buffer: &objc2_core_media::CMSampleBuffer,
            output_type: SCStreamOutputType,
        ) {
            if output_type != SCStreamOutputType::Screen {
                return;
            }
            self.ivars().frame_count.fetch_add(1, Ordering::Relaxed);
            if let Some(frame) = encode_sample_buffer(sample_buffer) {
                if let Ok(mut latest) = self.ivars().latest.lock() {
                    *latest = Some(frame);
                }
            }
        }
    }
);

impl ScreenStreamOutput {
    fn new(latest: SharedFrame) -> objc2::rc::Retained<Self> {
        let this = ScreenStreamOutput::alloc().set_ivars(OutputState {
            frame_count: AtomicU64::new(0),
            latest,
        });
        unsafe { msg_send![super(this), init] }
    }
}

static LATEST_FRAME: OnceLock<SharedFrame> = OnceLock::new();
static CAPTURE_MODE: OnceLock<&'static str> = OnceLock::new();

fn shared_frame() -> SharedFrame {
    LATEST_FRAME
        .get_or_init(|| Arc::new(Mutex::new(None)))
        .clone()
}

pub fn latest_frame() -> Option<ScreenFrame> {
    shared_frame().lock().ok()?.clone()
}

pub fn latest_frame_fresh(max_age: Duration) -> Option<ScreenFrame> {
    let frame = latest_frame()?;
    frame_is_fresh(frame.captured_at, Instant::now(), max_age).then_some(frame)
}

fn frame_is_fresh(captured_at: Instant, now: Instant, max_age: Duration) -> bool {
    now.saturating_duration_since(captured_at) <= max_age
}

pub fn probe_stream_frame() -> Result<u64, String> {
    let (result_tx, result_rx) = mpsc::channel::<Result<u64, String>>();
    let latest = shared_frame();
    std::thread::Builder::new()
        .name("edecan-screen-stream".to_string())
        .spawn(move || {
            let result: Result<(), String> = (|| {
                let (content_tx, content_rx) =
                    mpsc::channel::<Result<objc2::rc::Retained<SCDisplay>, String>>();
                let callback = RcBlock::new(
                    move |content: *mut SCShareableContent, error: *mut NSError| {
                        if !error.is_null() || content.is_null() {
                            let _ = content_tx
                                .send(Err("no se pudo obtener contenido compartible".to_string()));
                            return;
                        }
                        let displays = unsafe { (&*content).displays() };
                        let _ = content_tx.send(
                            displays
                                .firstObject()
                                .ok_or_else(|| "no hay pantallas compartibles".to_string()),
                        );
                    },
                );
                unsafe {
                    SCShareableContent::getCurrentProcessShareableContentWithCompletionHandler(
                        &callback,
                    );
                }
                let display = content_rx
                    .recv_timeout(Duration::from_secs(5))
                    .map_err(|_| "ScreenCaptureKit no respondió a tiempo".to_string())??;
                let excluded = NSArray::<objc2_screen_capture_kit::SCWindow>::new();
                let filter = unsafe {
                    SCContentFilter::initWithDisplay_excludingWindows(
                        SCContentFilter::alloc(),
                        &display,
                        &excluded,
                    )
                };
                let config = unsafe { SCStreamConfiguration::new() };
                unsafe {
                    config.setWidth(1600);
                    config.setHeight(900);
                    config.setQueueDepth(3);
                }
                let output = ScreenStreamOutput::new(latest);
                let output_protocol: &ProtocolObject<dyn SCStreamOutput> =
                    ProtocolObject::from_ref(&*output);
                let queue = dispatch2::DispatchQueue::new("cc.edecan.screen-stream", None);
                let stream = unsafe {
                    SCStream::initWithFilter_configuration_delegate(
                        SCStream::alloc(),
                        &filter,
                        &config,
                        None,
                    )
                };
                unsafe {
                    stream
                        .addStreamOutput_type_sampleHandlerQueue_error(
                            output_protocol,
                            SCStreamOutputType::Screen,
                            Some(&queue),
                        )
                        .map_err(|_| "no se pudo conectar la salida de SCStream".to_string())?;
                }
                let (start_tx, start_rx) = mpsc::channel();
                let start_callback = RcBlock::new(move |error: *mut NSError| {
                    let _ = start_tx.send(error.is_null());
                });
                unsafe { stream.startCaptureWithCompletionHandler(Some(&start_callback)) };
                if !start_rx
                    .recv_timeout(Duration::from_secs(5))
                    .map_err(|_| "SCStream no inició a tiempo".to_string())?
                {
                    return Err("SCStream rechazó el inicio".to_string());
                }
                std::thread::sleep(Duration::from_millis(300));
                if latest_frame().is_none() {
                    return Err("SCStream inició pero no entregó frames".to_string());
                }
                let _ = result_tx.send(Ok(1));
                loop {
                    let _keep_alive = (&stream, &output, &queue);
                    std::thread::park_timeout(Duration::from_secs(60));
                }
            })();
            if let Err(error) = result {
                let _ = result_tx.send(Err(error));
            }
        })
        .map_err(|_| "no se pudo mantener el stream de pantalla".to_string())?;
    result_rx
        .recv_timeout(Duration::from_secs(8))
        .map_err(|_| "SCStream no respondió a tiempo".to_string())?
}

pub fn capture_mode() -> &'static str {
    CAPTURE_MODE.get_or_init(|| {
        if probe_stream_frame().is_ok() {
            "screen_capture_kit_stream_ready"
        } else {
            "screencapture_fallback"
        }
    })
}

fn encode_sample_buffer(sample_buffer: &objc2_core_media::CMSampleBuffer) -> Option<ScreenFrame> {
    let pixel_buffer = unsafe { sample_buffer.image_buffer()? };
    let width = CVPixelBufferGetWidth(&pixel_buffer);
    let height = CVPixelBufferGetHeight(&pixel_buffer);
    let bytes_per_row = CVPixelBufferGetBytesPerRow(&pixel_buffer);
    let lock_result =
        unsafe { CVPixelBufferLockBaseAddress(&pixel_buffer, CVPixelBufferLockFlags::ReadOnly) };
    if lock_result != 0 || width == 0 || height == 0 || bytes_per_row < width * 4 {
        return None;
    }
    let base = CVPixelBufferGetBaseAddress(&pixel_buffer) as *const u8;
    if base.is_null() {
        unsafe { CVPixelBufferUnlockBaseAddress(&pixel_buffer, CVPixelBufferLockFlags::ReadOnly) };
        return None;
    }
    let mut rgba = vec![0_u8; width * height * 4];
    for row in 0..height {
        let source =
            unsafe { std::slice::from_raw_parts(base.add(row * bytes_per_row), width * 4) };
        let target = &mut rgba[row * width * 4..(row + 1) * width * 4];
        for (source_pixel, target_pixel) in source.chunks_exact(4).zip(target.chunks_exact_mut(4)) {
            target_pixel[0] = source_pixel[2];
            target_pixel[1] = source_pixel[1];
            target_pixel[2] = source_pixel[0];
            target_pixel[3] = source_pixel[3];
        }
    }
    unsafe { CVPixelBufferUnlockBaseAddress(&pixel_buffer, CVPixelBufferLockFlags::ReadOnly) };
    let mut png_bytes = Vec::new();
    let mut encoder = png::Encoder::new(&mut png_bytes, width as u32, height as u32);
    encoder.set_color(png::ColorType::Rgba);
    encoder.set_depth(png::BitDepth::Eight);
    encoder.write_header().ok()?.write_image_data(&rgba).ok()?;
    Some(ScreenFrame {
        png: png_bytes,
        width,
        height,
        captured_at: Instant::now(),
    })
}

#[cfg(test)]
mod tests {
    use super::frame_is_fresh;
    use std::time::{Duration, Instant};

    #[test]
    fn frame_stale_no_se_presenta_como_live() {
        let ahora = Instant::now();
        assert!(frame_is_fresh(ahora, ahora, Duration::from_secs(2)));
        assert!(!frame_is_fresh(
            ahora - Duration::from_secs(3),
            ahora,
            Duration::from_secs(2)
        ));
    }
}
