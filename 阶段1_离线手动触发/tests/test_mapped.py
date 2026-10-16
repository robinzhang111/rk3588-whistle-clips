import copy
import unittest
from unittest.mock import patch
from basketclip.mapped import main, mapped_window, validate_mapping
from basketclip.timing import make_alignment


def mapping():
    return dict(schema='bounded-video-utc-v1', kind='synthetic', evidence='synthetic test only',
                session_id='s', source_video_sha256='a'*64, video_duration=40,
                video_first_pts=7., format_start_pts=5., intervals=[
                    dict(utc_start='2000-01-01T00:00:00Z', utc_end='2000-01-01T00:00:12Z',
                         video_start=0, video_end=12, alignment=make_alignment('s','2000-01-01T00:00:00Z',0)),
                    dict(utc_start='2000-01-01T00:00:15Z', utc_end='2000-01-01T00:00:40Z',
                         video_start=15, video_end=40, alignment=make_alignment('s','2000-01-01T00:00:15Z',15))])


def event(second):
    return dict(session_id='s', event_time_utc='2000-01-01T00:00:%02dZ' % second)


class MappedTests(unittest.TestCase):
    def test_software_playback_requires_explicit_mode(self):
        value = dict(mapping(), kind='software_playback_observation',
                     evidence='Browser observations; audio latency unknown')
        self.assertEqual(mapped_window(event(8), value)['video_event_seconds'], 8)
        with patch('basketclip.mapped.load_json', return_value=value), patch('sys.argv',
                ['mapped', '--video', 'unused', '--events', 'unused', '--mapping',
                 'unused', '--output', 'unused']):
            with self.assertRaisesRegex(ValueError, 'explicit software-playback'):
                main()

    def test_user_anchor_is_distinct_from_verified_capture(self):
        value = dict(mapping(), kind='user_supplied_video_anchor',
                     evidence='User-provided point; not independently verified')
        validate_mapping(value)
        self.assertEqual(mapped_window(event(8), value)['video_event_seconds'], 8)
        self.assertEqual(value['kind'], 'user_supplied_video_anchor')

    def test_onset_edges_and_nonzero_pts(self):
        result = mapped_window(event(1), mapping())
        self.assertEqual((result['start_seconds'], result['end_seconds']), (0, 4))
        self.assertEqual(result['source_seek_start'], 2)
        self.assertTrue(result['clipped_at_start'])
        result = mapped_window(event(39), mapping())
        self.assertEqual((result['start_seconds'], result['end_seconds']), (34, 40))
        self.assertTrue(result['clipped_at_end'])

    def test_gap_and_crossing_clip_rejected(self):
        for second in [12, 13, 14, 15, 17, 40]:
            with self.subTest(second=second), self.assertRaises(ValueError):
                mapped_window(event(second), mapping())
        self.assertEqual(mapped_window(event(8), mapping())['end_seconds'], 11)

    def test_missing_unknown_or_wrong_session(self):
        for changes in [dict(kind='unknown'), dict(evidence=''), dict(intervals=[]), dict(source_video_sha256='bad')]:
            with self.assertRaises(ValueError):
                mapped_window(event(8), dict(mapping(), **changes))
        with self.assertRaises(ValueError):
            mapped_window(dict(event(8), session_id='other'), mapping())

    def test_overlap_and_bad_extents(self):
        for changes in [dict(video_duration=0), dict(video_first_pts=4.)]:
            with self.assertRaises(ValueError):
                validate_mapping(dict(mapping(), **changes))
        value = mapping()
        value['intervals'].append(copy.deepcopy(value['intervals'][0]))
        with self.assertRaises(ValueError):
            validate_mapping(value)
        value = mapping()
        value['intervals'][0]['video_end'] = 11
        with self.assertRaises(ValueError):
            validate_mapping(value)

    def test_two_anchor_scale_reused(self):
        value = mapping()
        value['intervals'] = [dict(utc_start='2000-01-01T00:00:00Z',
            utc_end='2000-01-01T00:00:20Z', video_start=0, video_end=40,
            alignment=make_alignment('s','2000-01-01T00:00:00Z',0,'2000-01-01T00:00:20Z',40))]
        self.assertEqual(mapped_window(event(10), value)['video_event_seconds'], 20)


if __name__ == '__main__':
    unittest.main()
