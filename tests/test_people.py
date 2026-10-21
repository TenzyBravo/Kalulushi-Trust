from django.db import IntegrityError
from django.test import TestCase

from people.models import Guardian

from .factories import link, make_guardian, make_student


class GuardianTests(TestCase):
    def test_siblings_share_guardian(self):
        mum = make_guardian()
        a = make_student("A1", "Chilufya")
        b = make_student("A2", "Mwila", gender="M")
        link(a, mum, primary=True)
        link(b, mum, primary=True)
        self.assertEqual(Guardian.objects.count(), 1)
        self.assertEqual(mum.students.count(), 2)

    def test_guardian_phone_unique_after_normalising(self):
        make_guardian(phone="0971234567")
        with self.assertRaises(IntegrityError):
            make_guardian(phone="+260 97 123 4567", name="Someone Else")

    def test_one_primary_guardian(self):
        s = make_student()
        mum = make_guardian("0971234567", "Mary")
        dad = make_guardian("0961234567", "John")
        link(s, mum, primary=True)
        link(s, dad, primary=True)
        self.assertEqual(s.primary_guardian, dad)
        self.assertEqual(s.guardian_links.filter(is_primary=True).count(), 1)

    def test_result_recipients(self):
        s = make_student()
        mum = make_guardian("0971234567", "Mary")
        uncle = make_guardian("0961234567", "Uncle")
        link(s, mum, primary=True)
        link(s, uncle, receives_results=False)
        self.assertEqual(list(s.result_recipients()), [mum])
