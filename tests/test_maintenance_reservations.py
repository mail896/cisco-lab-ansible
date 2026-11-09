"""Every enrolled target shares the maintenance reservation used by the GUI."""
from datetime import datetime
import unittest
from unittest.mock import patch

from maintenance.preview import BERLIN
from maintenance.reservations import reserved


class ReservationTests(unittest.TestCase):
    def test_only_registered_clients_are_reserved_including_b_when_enrolled(self):
        with patch('maintenance.controller.targets', return_value={'LAB-01A': {}}):
            for hour, minute in ((17, 45), (18, 0), (19, 59)):
                when = datetime(2026, 11, 9, hour, minute, tzinfo=BERLIN)
                self.assertTrue(reserved('LAB-01A', when))
                self.assertFalse(reserved('LAB-08B', when))
                self.assertFalse(reserved('LAB-02A', when))
            for when in (datetime(2026, 11, 9, 17, 44, tzinfo=BERLIN),
                         datetime(2026, 11, 9, 20, tzinfo=BERLIN),
                         datetime(2026, 11, 7, 18, tzinfo=BERLIN)):
                self.assertFalse(reserved('LAB-01A', when))
        with patch('maintenance.controller.targets', return_value={'LAB-08B': {}}):
            self.assertTrue(reserved('LAB-08B',datetime(2026,11,9,18,tzinfo=BERLIN)))


if __name__ == '__main__':
    unittest.main()
