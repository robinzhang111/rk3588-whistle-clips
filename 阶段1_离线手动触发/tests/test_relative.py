import unittest

from basketclip.relative import relative_position
from basketclip.timing import clip_window


class RelativeTests(unittest.TestCase):
    def mapping(self):
        return dict(schema='same-source-relative-media-v1',audio_frames=22050*10,
            sample_rate=22050,audio_source_rate=44100,video_first_pts=5.,
            audio_frame_sample_map=[dict(source_start_sample=0,source_end_sample=441000,
                media_start_pts=7.,mapping_valid=True)])

    def test_nonzero_audio_video_pts_and_onset_window(self):
        event=dict(estimated_start_sample=22050*2,estimated_end_sample=22050*4)
        position=relative_position(event,self.mapping())
        self.assertEqual(position,4.)
        self.assertEqual(clip_window(position,5,3,20),(0.,7.))
        self.assertEqual(clip_window(10,5,3,20),(5.,13.))

    def test_actual_frame_mapping_instead_of_assumed_linear_origin(self):
        mapping=self.mapping()
        mapping['audio_frame_sample_map']=[
            dict(source_start_sample=0,source_end_sample=44100,media_start_pts=7.,mapping_valid=True),
            dict(source_start_sample=44100,source_end_sample=441000,media_start_pts=8.00002,mapping_valid=True)]
        self.assertAlmostEqual(relative_position(dict(estimated_start_sample=44100,estimated_end_sample=45000),mapping),4.00002)

    def test_ambiguous_tail_bounds_and_utc_rejected(self):
        mapping=self.mapping()
        mapping['audio_frame_sample_map'][0]['mapping_valid']=False
        with self.assertRaises(ValueError):
            relative_position(dict(estimated_start_sample=1,estimated_end_sample=1024),mapping)
        for event in [dict(estimated_start_sample=-1,estimated_end_sample=1024),
                      dict(estimated_start_sample=1,estimated_end_sample=1024,event_time_utc='fake')]:
            with self.assertRaises(ValueError):
                relative_position(event,self.mapping())

    def test_end_clipping_and_outside_rule(self):
        self.assertEqual(clip_window(19,5,3,20),(14.,20.))
        self.assertIsNone(clip_window(20,5,3,20))


if __name__=='__main__':
    unittest.main()
