"""
Enterprise Audio Engine for AI Voice Agent Bridge.

Provides production-grade telephony audio processing:
  - Enhanced 8kHz G.711 µ-law resampling with band-limited smoothing
  - Ring buffer for jitter-absorbing outbound audio queueing (replaces drop-oldest)
  - Dynamic VAD sensitivity tuning based on call noise floor
  - Audio metrics collection for call quality telemetry
  - Comfort noise generation for silence padding

Inspired by AVA Audio Profile architecture, purpose-built for
Asterisk AudioSocket / OpenAI Realtime bridge.
"""

try:
    import audioop
except ImportError:
    try:
        import audioop_lts as audioop
    except ImportError:
        audioop = None

import collections
import math
import struct
import time


# ─────────────────────────────────────────────
#  Audio Constants
# ─────────────────────────────────────────────

ULAW_SILENCE = b"\xff"  # µ-law silence byte (digital zero / minimum energy)
SLIN_SILENCE = b"\x00\x00"  # 16-bit signed linear silence (zero amplitude)

FRAME_SIZE_ULAW = 160  # 20ms @ 8kHz µ-law (1 byte per sample)
FRAME_SIZE_SLIN = 320  # 20ms @ 8kHz 16-bit signed linear (2 bytes per sample)

SAMPLE_RATE = 8000
FRAME_DURATION_MS = 20
FRAME_DURATION_SEC = 0.020

# Pre-computed silence frames for zero-allocation padding
SILENCE_FRAME_ULAW = ULAW_SILENCE * FRAME_SIZE_ULAW
SILENCE_FRAME_SLIN = SLIN_SILENCE * (FRAME_SIZE_SLIN // 2)


# ─────────────────────────────────────────────
#  Enhanced Audio Resampler (Band-Limited 8kHz)
# ─────────────────────────────────────────────

class EnhancedResampler:
    """
    Stateful band-limited resampler for cleaner G.711 µ-law playback.

    When OpenAI Realtime API sends audio, it arrives as raw G.711 µ-law.
    Standard audioop.ulaw2lin conversion can produce harsh quantization
    artifacts on narrowband telephony lines. This resampler applies:

    1. DC offset removal (high-pass) to prevent baseline drift
    2. Soft clipping to prevent harsh digital distortion
    3. Gentle low-pass smoothing to reduce aliasing artifacts
    4. Per-call state isolation to prevent cross-call audio bleed

    Based on the "telephony_enhanced_8k" Audio Profile concept from AVA.
    """

    def __init__(self, enable_smoothing=True, soft_clip_threshold=28000):
        self._prev_sample = 0  # For single-pole low-pass filter
        self._dc_offset = 0.0  # Running DC offset estimate
        self._enable_smoothing = enable_smoothing
        self._soft_clip_threshold = soft_clip_threshold
        self._frame_count = 0

    def reset(self):
        """Reset resampler state between responses/interruptions."""
        self._prev_sample = 0
        self._dc_offset = 0.0
        self._frame_count = 0

    def process_ulaw_to_slin(self, ulaw_data: bytes) -> bytes:
        """
        Convert µ-law to enhanced 16-bit signed linear with band-limited smoothing.

        Args:
            ulaw_data: Raw G.711 µ-law audio bytes from OpenAI.

        Returns:
            Enhanced 16-bit signed linear PCM for Asterisk playback.
        """
        if not ulaw_data:
            return b""

        # Step 1: Standard µ-law → 16-bit linear conversion
        slin = audioop.ulaw2lin(ulaw_data, 2)

        if not self._enable_smoothing:
            return slin

        self._frame_count += 1

        # Step 2: Apply DC offset removal + soft clipping + gentle smoothing
        samples = list(struct.unpack(f"<{len(slin)//2}h", slin))
        output = []

        # Low-pass filter coefficient (0.0 = no filter, 1.0 = full filter)
        # 0.15 gives subtle smoothing without audible quality loss
        alpha = 0.15

        for sample in samples:
            # DC offset removal (exponential moving average)
            self._dc_offset = 0.999 * self._dc_offset + 0.001 * sample
            sample = int(sample - self._dc_offset)

            # Soft clipping (prevents harsh digital distortion on loud audio)
            if abs(sample) > self._soft_clip_threshold:
                sign = 1 if sample > 0 else -1
                excess = abs(sample) - self._soft_clip_threshold
                headroom = 32767 - self._soft_clip_threshold
                compressed = self._soft_clip_threshold + int(headroom * math.tanh(excess / headroom))
                sample = sign * min(compressed, 32767)

            # Gentle single-pole low-pass filter
            sample = int(self._prev_sample * alpha + sample * (1.0 - alpha))
            self._prev_sample = sample

            # Clamp to 16-bit range
            sample = max(-32768, min(32767, sample))
            output.append(sample)

        return struct.pack(f"<{len(output)}h", *output)

    def process_slin_to_ulaw(self, slin_data: bytes) -> bytes:
        """Standard 16-bit linear → µ-law conversion for caller audio (no enhancement needed)."""
        if not slin_data:
            return b""
        return audioop.lin2ulaw(slin_data, 2)


# ─────────────────────────────────────────────
#  Ring Buffer (Jitter-Absorbing Outbound Queue)
# ─────────────────────────────────────────────

class AudioRingBuffer:
    """
    Fixed-capacity ring buffer for outbound audio frames.

    Replaces the asyncio.Queue with drop-oldest behavior. When the buffer
    is full, the oldest frame is silently discarded to prevent memory
    pressure during prolonged AI speech or network stalls.

    Benefits over asyncio.Queue(maxsize=N):
    - No exception on overflow (smooth degradation)
    - O(1) append and pop operations
    - Pre-allocated memory footprint
    - Thread-safe via deque's atomic append/popleft

    Args:
        capacity: Maximum number of audio frames to buffer.
                  Default 1500 = 30 seconds of audio at 20ms/frame.
    """

    def __init__(self, capacity=1500):
        self._buffer = collections.deque(maxlen=capacity)
        self._capacity = capacity
        self._total_written = 0
        self._total_dropped = 0

    @property
    def size(self):
        return len(self._buffer)

    @property
    def empty(self):
        return len(self._buffer) == 0

    @property
    def stats(self):
        return {
            "buffered": len(self._buffer),
            "capacity": self._capacity,
            "total_written": self._total_written,
            "total_dropped": self._total_dropped,
        }

    def put(self, frame: bytes):
        """
        Append a frame to the ring buffer.

        If the buffer is at capacity, the oldest frame is automatically
        discarded by deque's maxlen behavior — no data corruption,
        no exception, smooth degradation.
        """
        was_full = len(self._buffer) >= self._capacity
        self._buffer.append(frame)
        self._total_written += 1
        if was_full:
            self._total_dropped += 1

    def get(self) -> bytes:
        """Pop the oldest frame from the buffer. Returns None if empty."""
        if self._buffer:
            return self._buffer.popleft()
        return None

    def clear(self):
        """Flush all buffered frames (used on barge-in / interruption)."""
        self._buffer.clear()

    def peek_size(self) -> int:
        """Current number of buffered frames."""
        return len(self._buffer)


# ─────────────────────────────────────────────
#  Dynamic VAD Tuning Engine
# ─────────────────────────────────────────────

class DynamicVADTuner:
    """
    Analyzes incoming caller audio energy to dynamically adjust VAD
    sensitivity, preventing false triggers in noisy environments and
    improving turn detection in quiet calls.

    Tracks:
    - Running noise floor estimate (background energy)
    - Speech energy peaks
    - Signal-to-noise ratio (SNR)

    Provides recommended VAD threshold adjustments based on call conditions.
    """

    def __init__(self, base_threshold=0.65, base_silence_ms=350):
        self.base_threshold = base_threshold
        self.base_silence_ms = base_silence_ms

        # Exponential moving averages for energy tracking
        self._noise_floor = 0.0  # Background noise energy estimate
        self._speech_energy = 0.0  # Speech energy estimate
        self._sample_count = 0
        self._last_adjustment_time = 0.0
        self._adjustment_interval = 5.0  # Re-evaluate every 5 seconds

        # Noise classification
        self._noise_level = "quiet"  # quiet, moderate, noisy, very_noisy
        self._consecutive_noisy_frames = 0

    def analyze_frame(self, ulaw_frame: bytes):
        """
        Analyze a single µ-law audio frame from the caller.

        Called on every inbound audio frame to maintain running
        noise floor and speech energy estimates.
        """
        if not ulaw_frame or len(ulaw_frame) < 10:
            return

        try:
            # Convert to linear for energy measurement
            slin = audioop.ulaw2lin(ulaw_frame, 2)
            rms = audioop.rms(slin, 2)
        except Exception:
            return

        self._sample_count += 1

        # Update noise floor (slow-adapting, tracks minimum energy)
        if self._sample_count < 25:
            # Initial calibration period (first 500ms): establish baseline
            self._noise_floor = max(self._noise_floor, rms * 0.5)
        else:
            if rms < self._noise_floor * 2.0:
                # Low energy frame — likely silence/background noise
                self._noise_floor = 0.995 * self._noise_floor + 0.005 * rms
            # High energy frame — likely speech (don't update noise floor)

        # Update speech energy estimate (fast-adapting)
        if rms > self._noise_floor * 3.0:
            self._speech_energy = 0.9 * self._speech_energy + 0.1 * rms

        # Classify noise level
        if self._noise_floor < 200:
            self._noise_level = "quiet"
            self._consecutive_noisy_frames = 0
        elif self._noise_floor < 800:
            self._noise_level = "moderate"
            self._consecutive_noisy_frames = 0
        elif self._noise_floor < 2000:
            self._noise_level = "noisy"
            self._consecutive_noisy_frames += 1
        else:
            self._noise_level = "very_noisy"
            self._consecutive_noisy_frames += 1

    def get_recommended_settings(self) -> dict:
        """
        Returns recommended VAD settings based on current call audio conditions.

        Called periodically (every ~5 seconds) to adjust OpenAI session
        VAD parameters for optimal turn detection.
        """
        now = time.monotonic()
        if now - self._last_adjustment_time < self._adjustment_interval:
            return None  # Too soon since last adjustment

        self._last_adjustment_time = now

        # Calculate SNR
        snr = (self._speech_energy / max(self._noise_floor, 1.0)) if self._noise_floor > 0 else 50.0

        if self._noise_level == "quiet":
            # Quiet environment: lower threshold for better sensitivity
            return {
                "threshold": max(0.45, self.base_threshold - 0.15),
                "silence_ms": self.base_silence_ms,
                "noise_level": "quiet",
                "snr": round(snr, 1),
                "noise_floor_rms": round(self._noise_floor, 1),
            }
        elif self._noise_level == "moderate":
            # Moderate noise: use baseline settings
            return {
                "threshold": self.base_threshold,
                "silence_ms": self.base_silence_ms,
                "noise_level": "moderate",
                "snr": round(snr, 1),
                "noise_floor_rms": round(self._noise_floor, 1),
            }
        elif self._noise_level == "noisy":
            # Noisy environment: raise threshold to prevent false triggers,
            # increase silence window to avoid cutting off speech
            return {
                "threshold": min(0.85, self.base_threshold + 0.10),
                "silence_ms": min(1200, self.base_silence_ms + 200),
                "noise_level": "noisy",
                "snr": round(snr, 1),
                "noise_floor_rms": round(self._noise_floor, 1),
            }
        else:
            # Very noisy (branch floor, mobile car): max threshold and silence
            return {
                "threshold": min(0.90, self.base_threshold + 0.20),
                "silence_ms": min(1500, self.base_silence_ms + 400),
                "noise_level": "very_noisy",
                "snr": round(snr, 1),
                "noise_floor_rms": round(self._noise_floor, 1),
            }


# ─────────────────────────────────────────────
#  Call Audio Quality Metrics
# ─────────────────────────────────────────────

class AudioQualityMetrics:
    """
    Collects per-call audio quality telemetry for operational dashboards.

    Tracks:
    - Total audio seconds sent/received
    - Barge-in / interruption count
    - Buffer overflow events
    - Silence gaps (potential dead air)
    - Noise floor classification over call duration
    """

    def __init__(self):
        self._start_time = time.monotonic()
        self._bytes_sent = 0
        self._bytes_received = 0
        self._barge_in_count = 0
        self._buffer_overflow_count = 0
        self._silence_gap_count = 0
        self._silence_gap_total_ms = 0
        self._watchdog_recovery_count = 0
        self._vad_adjustments = 0
        self._last_audio_sent_at = 0.0
        self._last_audio_received_at = 0.0
        self._max_silence_gap_ms = 0

    def record_audio_sent(self, byte_count: int):
        now = time.monotonic()
        if self._last_audio_sent_at > 0:
            gap_ms = (now - self._last_audio_sent_at) * 1000
            if gap_ms > 500:  # Gap > 500ms = potential dead air
                self._silence_gap_count += 1
                self._silence_gap_total_ms += gap_ms
                self._max_silence_gap_ms = max(self._max_silence_gap_ms, gap_ms)
        self._last_audio_sent_at = now
        self._bytes_sent += byte_count

    def record_audio_received(self, byte_count: int):
        self._last_audio_received_at = time.monotonic()
        self._bytes_received += byte_count

    def record_barge_in(self):
        self._barge_in_count += 1

    def record_buffer_overflow(self):
        self._buffer_overflow_count += 1

    def record_watchdog_recovery(self):
        self._watchdog_recovery_count += 1

    def record_vad_adjustment(self):
        self._vad_adjustments += 1

    def get_summary(self) -> dict:
        elapsed = time.monotonic() - self._start_time
        return {
            "duration_seconds": round(elapsed, 1),
            "audio_sent_seconds": round(self._bytes_sent / SAMPLE_RATE, 1),
            "audio_received_seconds": round(self._bytes_received / SAMPLE_RATE, 1),
            "barge_in_count": self._barge_in_count,
            "buffer_overflows": self._buffer_overflow_count,
            "silence_gaps": self._silence_gap_count,
            "total_silence_gap_ms": round(self._silence_gap_total_ms),
            "max_silence_gap_ms": round(self._max_silence_gap_ms),
            "watchdog_recoveries": self._watchdog_recovery_count,
            "vad_adjustments": self._vad_adjustments,
        }


# ─────────────────────────────────────────────
#  Telephony Tones & Fallback Audio Generators
# ─────────────────────────────────────────────

def generate_pcm_tone(frequency_hz: float, duration_ms: int, amplitude: float = 0.5) -> bytes:
    """Generate 16-bit 8kHz mono PCM sine wave tone."""
    sample_count = int(SAMPLE_RATE * (duration_ms / 1000.0))
    samples = []
    max_amp = 32767 * min(max(amplitude, 0.0), 1.0)
    for i in range(sample_count):
        t = i / SAMPLE_RATE
        val = int(max_amp * math.sin(2 * math.pi * frequency_hz * t))
        samples.append(max(-32768, min(32767, val)))
    return struct.pack(f"<{len(samples)}h", *samples)


def generate_chime_ulaw(duration_ms: int = 400) -> bytes:
    """Generate pleasant dual-frequency PBX advisory chime in G.711 µ-law."""
    sample_count = int(SAMPLE_RATE * (duration_ms / 1000.0))
    samples = []
    for i in range(sample_count):
        t = i / SAMPLE_RATE
        # Envelope decay
        decay = math.exp(-3.0 * (i / sample_count))
        # Dual frequency (587Hz D5 + 880Hz A5 harmonic)
        wave = 0.5 * math.sin(2 * math.pi * 587.33 * t) + 0.5 * math.sin(2 * math.pi * 880.0 * t)
        val = int(16000 * decay * wave)
        samples.append(max(-32768, min(32767, val)))
    slin = struct.pack(f"<{len(samples)}h", *samples)
    if audioop:
        return audioop.lin2ulaw(slin, 2)
    return ULAW_SILENCE * len(samples)


def generate_comfort_noise_ulaw(duration_ms: int = 200, level_db: float = -45.0) -> bytes:
    """Generate low-amplitude pseudo-random comfort noise in µ-law."""
    sample_count = int(SAMPLE_RATE * (duration_ms / 1000.0))
    # -45 dB corresponds to amplitude of ~184
    amp = int(32767 * (10 ** (level_db / 20.0)))
    import random
    samples = [random.randint(-amp, amp) for _ in range(sample_count)]
    slin = struct.pack(f"<{len(samples)}h", *samples)
    if audioop:
        return audioop.lin2ulaw(slin, 2)
    return ULAW_SILENCE * len(samples)

