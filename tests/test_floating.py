import unittest
from AppKit import NSMakeRect
from agent.macos.floating import fit_frame


class FloatingFrameTests(unittest.TestCase):
    def test_stays_in_display_and_avoids_menu_bar(self):
        visible = NSMakeRect(0, 30, 1440, 850)
        self.assertEqual(fit_frame(1500, 900, 328, 620, visible), (1112, 260))
        self.assertEqual(fit_frame(-20, 0, 82, 82, visible), (0, 30))

    def test_supports_display_left_of_primary(self):
        visible = NSMakeRect(-1920, 0, 1920, 1080)
        self.assertEqual(fit_frame(-1900, 500, 82, 82, visible), (-1900, 500))
        self.assertEqual(fit_frame(10, 500, 328, 400, visible), (-328, 500))
