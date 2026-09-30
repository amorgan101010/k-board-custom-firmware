import tempfile
import unittest
from pathlib import Path

from firmware_tools.extract_kmi_firmware import decode_7bit, extract


class FirmwareExtractTest(unittest.TestCase):
    def test_decodes_high_bit_mask(self):
        encoded = bytes((0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0b0101010))
        self.assertEqual(
            decode_7bit(encoded),
            bytes((0x01, 0x82, 0x03, 0x84, 0x05, 0x86, 0x07)),
        )

    def test_extracts_official_122_image_when_available(self):
        source = Path("/tmp/kmi-sendsysex/syx/K-Board/K-Board Firmware v1.2.2_cs512.syx")
        if not source.exists():
            self.skipTest("official updater checkout is unavailable")
        records, chunks = extract(source)
        self.assertEqual(len(chunks), 161)
        self.assertTrue(any(record.address == 0x2400 for record in records))
        self.assertEqual(records[-1].record_type, 1)

    def test_rejects_unframed_data(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bad.syx"
            source.write_bytes(b"not sysex")
            with self.assertRaisesRegex(ValueError, "stray byte"):
                extract(source)


if __name__ == "__main__":
    unittest.main()
