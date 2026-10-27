import unittest
from agent import connection_status as status


class ConnectionStatusTests(unittest.TestCase):
    def tearDown(self):
        for client in ('a', 'b'):
            status.remove(client)

    def test_disconnect_cleans_only_its_own_session(self):
        status.add('a', lambda: None)
        status.add('b', lambda: None)
        status.update('a', meeting='first')
        status.update('b', meeting='second')
        status.remove('a')
        self.assertEqual(status.snapshot(), [{'state': 'Connected · checking shared window', 'meeting': 'second'}])
        status.update('a', meeting='stale')
        self.assertEqual(len(status.snapshot()), 1)

    def test_metadata_is_bounded_and_callbacks_are_not_exposed(self):
        calls = []
        status.add('a', lambda: calls.append('closed'))
        status.update('a', tab='x\n' * 200, meeting=None)
        row = status.snapshot()[0]
        self.assertEqual(row['tab'], 'x' * 160)
        self.assertEqual(row['meeting'], '')
        self.assertNotIn('disconnect', row)
        status.disconnect_all()
        self.assertEqual(calls, ['closed'])


class SurfaceLabelTests(unittest.TestCase):
    def test_monitor_is_recognized_without_a_window_title(self):
        self.assertEqual(status.surface_label({'ok': True, 'kind': 'monitor', 'width': 1440, 'height': 900}), 'Entire display · 1440 × 900')

    def test_window_and_failed_match(self):
        self.assertEqual(status.surface_label({'ok': True, 'kind': 'window', 'owner': 'Chrome', 'name': 'Session'}), 'Chrome · Session')
        self.assertEqual(status.surface_label({'ok': False}), 'Not matched')
