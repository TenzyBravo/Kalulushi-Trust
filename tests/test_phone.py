from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from people.phone import normalise_phone

from .factories import make_guardian


class NormalisePhoneTests(SimpleTestCase):
    def test_accepts_common_formats(self):
        for raw in ["0971234567", "971234567", "260971234567", "+260971234567",
                    "+260 97 123 4567", "097-123-4567", "00260971234567"]:
            self.assertEqual(normalise_phone(raw), "+260971234567", raw)

    def test_accepts_all_networks(self):
        for raw in ["0961234567", "0951234567", "0771234567", "0761234567", "0751234567", "0571234567"]:
            self.assertTrue(normalise_phone(raw).startswith("+260"), raw)

    def test_rejects_bad_numbers(self):
        for raw in ["", "123", "0211234567", "09712345", "097123456789", "+27821234567", "abcdefghij"]:
            with self.assertRaises(ValidationError, msg=raw):
                normalise_phone(raw)


class PhoneFieldTests(TestCase):
    def test_saved_normalised(self):
        g = make_guardian(phone="097 123 4567")
        g.refresh_from_db()
        self.assertEqual(g.phone, "+260971234567")

    def test_invalid_number_rejected_on_save(self):
        with self.assertRaises(ValidationError):
            make_guardian(phone="12345")
