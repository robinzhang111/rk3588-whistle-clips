import unittest

from basketclip.buttons import INPUT_EVENT, rec_press_timestamp


class ButtonTests(unittest.TestCase):
    def test_only_rec_press_generates_event(self):
        packet = INPUT_EVENT.pack(12, 500000, 1, 250, 1)
        self.assertEqual(rec_press_timestamp(packet), 12.5)

    def test_release_repeat_other_keys_and_sync_ignored(self):
        for event_type, code, value in ((1, 250, 0), (1, 250, 2),
                                       (1, 116, 1), (0, 0, 0)):
            self.assertIsNone(rec_press_timestamp(INPUT_EVENT.pack(12, 0, event_type, code, value)))
