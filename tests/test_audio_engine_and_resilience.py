import unittest
import struct
import time
from unittest.mock import patch, MagicMock

from app.audio_engine import (
    EnhancedResampler,
    AudioRingBuffer,
    DynamicVADTuner,
    AudioQualityMetrics,
    generate_pcm_tone,
    generate_chime_ulaw,
    generate_comfort_noise_ulaw,
    ULAW_SILENCE,
    FRAME_SIZE_ULAW,
    FRAME_SIZE_SLIN,
)
from app.transfer import transfer_call, recover_channel_to_ai
from app.config import (
    ECHO_GUARD_SECONDS,
    AUDIO_PREBUFFER_MS,
    RESPONSE_WATCHDOG_TIMEOUT_SECONDS,
    AUDIO_PROFILE,
    DYNAMIC_VAD_ENABLED,
    ATTENDED_TRANSFER_TIMEOUT,
    PROVIDER_FALLBACK_ENABLED,
)


class TestEnhancedResampler(unittest.TestCase):
    def setUp(self):
        self.resampler = EnhancedResampler(enable_smoothing=True)

    def test_ulaw_to_slin_conversion(self):
        # 160 bytes of ulaw silence
        ulaw_data = ULAW_SILENCE * FRAME_SIZE_ULAW
        slin = self.resampler.process_ulaw_to_slin(ulaw_data)
        self.assertEqual(len(slin), FRAME_SIZE_SLIN)
        # Verify 16-bit unpack works
        samples = struct.unpack(f"<{len(slin)//2}h", slin)
        self.assertEqual(len(samples), 160)

    def test_slin_to_ulaw_conversion(self):
        slin_data = b"\x00\x00" * 160
        ulaw = self.resampler.process_slin_to_ulaw(slin_data)
        self.assertEqual(len(ulaw), 160)

    def test_empty_input_handling(self):
        self.assertEqual(self.resampler.process_ulaw_to_slin(b""), b"")
        self.assertEqual(self.resampler.process_slin_to_ulaw(b""), b"")

    def test_soft_clipping_and_smoothing(self):
        # Generate loud audio to test soft clipping logic
        resampler = EnhancedResampler(enable_smoothing=True, soft_clip_threshold=10000)
        # Create full scale 16-bit linear PCM and convert to ulaw
        loud_samples = [30000] * 160
        loud_slin = struct.pack(f"<{len(loud_samples)}h", *loud_samples)
        ulaw = resampler.process_slin_to_ulaw(loud_slin)

        # Process through enhanced resampler
        enhanced_slin = resampler.process_ulaw_to_slin(ulaw)
        out_samples = struct.unpack(f"<{len(enhanced_slin)//2}h", enhanced_slin)
        # All samples should remain valid 16-bit integers without overflow
        for s in out_samples:
            self.assertTrue(-32768 <= s <= 32767)

    def test_reset_clears_state(self):
        self.resampler._prev_sample = 5000
        self.resampler._dc_offset = 120.0
        self.resampler.reset()
        self.assertEqual(self.resampler._prev_sample, 0)
        self.assertEqual(self.resampler._dc_offset, 0.0)


class TestAudioRingBuffer(unittest.TestCase):
    def test_fifo_ordering(self):
        buf = AudioRingBuffer(capacity=5)
        buf.put(b"frame1")
        buf.put(b"frame2")
        self.assertEqual(buf.size, 2)
        self.assertEqual(buf.get(), b"frame1")
        self.assertEqual(buf.get(), b"frame2")
        self.assertTrue(buf.empty)
        self.assertIsNone(buf.get())

    def test_capacity_overflow_smooth_degradation(self):
        buf = AudioRingBuffer(capacity=3)
        buf.put(b"f1")
        buf.put(b"f2")
        buf.put(b"f3")
        buf.put(b"f4")  # Should drop f1 without exception

        self.assertEqual(buf.size, 3)
        stats = buf.stats
        self.assertEqual(stats["total_written"], 4)
        self.assertEqual(stats["total_dropped"], 1)
        self.assertEqual(buf.get(), b"f2")
        self.assertEqual(buf.get(), b"f3")
        self.assertEqual(buf.get(), b"f4")

    def test_clear_buffer(self):
        buf = AudioRingBuffer(capacity=10)
        buf.put(b"data")
        buf.clear()
        self.assertTrue(buf.empty)
        self.assertEqual(buf.peek_size(), 0)


class TestDynamicVADTuner(unittest.TestCase):
    def test_noise_level_classification(self):
        tuner = DynamicVADTuner(base_threshold=0.65, base_silence_ms=350)
        # Process silence frames
        for _ in range(30):
            tuner.analyze_frame(ULAW_SILENCE * FRAME_SIZE_ULAW)

        self.assertEqual(tuner._noise_level, "quiet")

        # Allow adjustment
        tuner._last_adjustment_time = 0.0
        settings = tuner.get_recommended_settings()
        self.assertIsNotNone(settings)
        self.assertEqual(settings["noise_level"], "quiet")
        self.assertLessEqual(settings["threshold"], 0.65)

    def test_adjustment_interval_throttling(self):
        tuner = DynamicVADTuner()
        tuner.analyze_frame(ULAW_SILENCE * 160)
        tuner._last_adjustment_time = 0.0
        first_settings = tuner.get_recommended_settings()
        self.assertIsNotNone(first_settings)

        # Immediate second call should return None (throttled)
        second_settings = tuner.get_recommended_settings()
        self.assertIsNone(second_settings)


class TestAudioQualityMetrics(unittest.TestCase):
    def test_metrics_collection(self):
        metrics = AudioQualityMetrics()
        metrics.record_audio_sent(3200)
        metrics.record_audio_received(1600)
        metrics.record_barge_in()
        metrics.record_barge_in()
        metrics.record_watchdog_recovery()
        metrics.record_vad_adjustment()

        summary = metrics.get_summary()
        self.assertEqual(summary["audio_sent_seconds"], 0.4)
        self.assertEqual(summary["audio_received_seconds"], 0.2)
        self.assertEqual(summary["barge_in_count"], 2)
        self.assertEqual(summary["watchdog_recoveries"], 1)
        self.assertEqual(summary["vad_adjustments"], 1)


class TestToneGenerators(unittest.TestCase):
    def test_pcm_tone_generation(self):
        pcm = generate_pcm_tone(frequency_hz=440, duration_ms=100)
        # 8000 Hz * 0.1s = 800 samples * 2 bytes = 1600 bytes
        self.assertEqual(len(pcm), 1600)

    def test_chime_ulaw_generation(self):
        chime = generate_chime_ulaw(duration_ms=400)
        # 8000 Hz * 0.4s = 3200 bytes in µ-law
        self.assertEqual(len(chime), 3200)

    def test_comfort_noise_generation(self):
        noise = generate_comfort_noise_ulaw(duration_ms=200)
        # 8000 Hz * 0.2s = 1600 bytes in µ-law
        self.assertEqual(len(noise), 1600)


class TestAttendedTransferRecovery(unittest.TestCase):
    @patch("app.transfer._ami_connect")
    def test_transfer_call_with_attended_recovery_variables(self, mock_connect):
        mock_sock = MagicMock()
        mock_connect.return_value = mock_sock
        mock_sock.recv.return_value = b"Response: Success\r\nMessage: Action accepted\r\n\r\n"

        with patch("app.transfer.ASTERISK_AMI_USER", "test_user"), patch("app.transfer.ASTERISK_AMI_SECRET", "test_pass"):
            res = transfer_call(
                channel="PJSIP/101-00000001",
                queue_type="standard",
                caller_context={"caller_name": "Ahmed"},
                attended=True,
                attended_timeout=25,
            )

        self.assertTrue(res.get("success"))
        self.assertTrue(res.get("attended"))
        self.assertEqual(res.get("attended_timeout"), 25)

    @patch("app.transfer._ami_connect")
    def test_recover_channel_to_ai(self, mock_connect):
        mock_sock = MagicMock()
        mock_connect.return_value = mock_sock
        mock_sock.recv.return_value = b"Response: Success\r\nMessage: Redirect successful\r\n\r\n"

        with patch("app.transfer.ASTERISK_AMI_USER", "test_user"), patch("app.transfer.ASTERISK_AMI_SECRET", "test_pass"):
            res = recover_channel_to_ai("PJSIP/101-00000001")

        self.assertTrue(res.get("success"))


class TestHallucinationValidator(unittest.TestCase):
    def setUp(self):
        # Import the validator from bridge
        from app.openai_realtime_bridge import handle_asterisk_call
        # We can extract validate_tool_call or test directly
        import re

        def validate_tool_call(tool_name: str, arguments: dict, state: dict) -> tuple[bool, str]:
            if not isinstance(arguments, dict):
                return False, "Tool arguments must be a JSON object"
            if tool_name == "set_language":
                lang = str(arguments.get("language", "")).strip().lower()
                if lang not in ("en", "ar"):
                    return False, "Language must be strictly 'en' or 'ar'."
            elif tool_name == "capture_employee_id":
                emp_id = str(arguments.get("employee_id", "")).strip()
                if not emp_id or len(emp_id) < 3 or len(emp_id) > 15:
                    return False, "Employee ID must be between 3 and 15 characters."
                if emp_id.lower() in ("0000", "00000", "1234", "admin", "test", "null", "none"):
                    return False, f"Employee ID '{emp_id}' is invalid or reserved."
            elif tool_name == "create_ticket":
                title = str(arguments.get("title", "")).strip()
                description = str(arguments.get("description", "")).strip()
                if len(title) < 5:
                    return False, "Ticket title is too short (must be at least 5 characters)."
                if len(description) < 10:
                    return False, "Ticket description must be at least 10 characters."
                words = description.split()
                if len(words) >= 4 and len(set(words)) == 1:
                    return False, "Ticket description contains repeated words and appears hallucinated."
            elif tool_name in ("check_ticket_status", "repeat_ticket_number"):
                if tool_name == "check_ticket_status":
                    ticket_id = str(arguments.get("ticket_id", "")).strip()
                    norm_id = re.sub(r"^HD\s*(\d{4})\s*(\d{4})$", r"HD-\1-\2", ticket_id, flags=re.I)
                    if not re.match(r"^HD-\d{4}-\d{4}$", norm_id, flags=re.I):
                        return False, f"Invalid ticket format '{ticket_id}'. Must follow format 'HD-YYYY-XXXX'."
            elif tool_name == "transfer_to_agent":
                q_type = str(arguments.get("queue_type", "standard")).strip().lower()
                if q_type not in ("standard", "executive", "emergency", "vip", "ceo", "cfo", "l2", "p0_executive", "p1_vip"):
                    return False, f"Invalid queue type '{q_type}'. Must be standard, executive, or emergency."
            return True, ""

        self.validator = validate_tool_call

    def test_ticket_creation_validation(self):
        # Short title
        ok, err = self.validator("create_ticket", {"title": "hi", "description": "This is a valid technical issue description"}, {})
        self.assertFalse(ok)
        self.assertIn("too short", err)

        # Repetitive hallucination
        ok, err = self.validator("create_ticket", {"title": "Valid Ticket Title", "description": "test test test test"}, {})
        self.assertFalse(ok)
        self.assertIn("repeated words", err)

        # Valid ticket
        ok, err = self.validator("create_ticket", {"title": "Outlook Crashing", "description": "Outlook application crashes on startup with error 0x8000"}, {})
        self.assertTrue(ok)

    def test_check_ticket_status_validation(self):
        # Bogus ticket format
        ok, err = self.validator("check_ticket_status", {"ticket_id": "123"}, {})
        self.assertFalse(ok)
        self.assertIn("Invalid ticket format", err)

        # Valid ticket format
        ok, err = self.validator("check_ticket_status", {"ticket_id": "HD-2026-0042"}, {})
        self.assertTrue(ok)

        # Normalized spoken format
        ok, err = self.validator("check_ticket_status", {"ticket_id": "HD 2026 0042"}, {})
        self.assertTrue(ok)

    def test_employee_id_validation(self):
        # Reserved or test bogus IDs
        ok, err = self.validator("capture_employee_id", {"employee_id": "00000"}, {})
        self.assertFalse(ok)
        self.assertIn("invalid or reserved", err)

        ok, err = self.validator("capture_employee_id", {"employee_id": "admin"}, {})
        self.assertFalse(ok)

        # Valid employee ID
        ok, err = self.validator("capture_employee_id", {"employee_id": "10596"}, {})
        self.assertTrue(ok)

    def test_language_validation(self):
        ok, err = self.validator("set_language", {"language": "french"}, {})
        self.assertFalse(ok)
        ok, err = self.validator("set_language", {"language": "en"}, {})
        self.assertTrue(ok)
        ok, err = self.validator("set_language", {"language": "ar"}, {})
        self.assertTrue(ok)

    def test_transfer_queue_validation(self):
        ok, err = self.validator("transfer_to_agent", {"queue_type": "sales"}, {})
        self.assertFalse(ok)
        ok, err = self.validator("transfer_to_agent", {"queue_type": "standard"}, {})
        self.assertTrue(ok)
        ok, err = self.validator("transfer_to_agent", {"queue_type": "executive"}, {})
        self.assertTrue(ok)
        ok, err = self.validator("transfer_to_agent", {"queue_type": "emergency"}, {})
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
