import sys
from pathlib import Path
import unittest
from copy import deepcopy
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent/'tools'))
from continuous_whistle import candidate_events
from event_logic_v2a import merge_qualified_candidates


class V2ATests(unittest.TestCase):
    def prepare(self, pattern):
        scores = np.array([[.9 if p else .1] for p in pattern])
        samples = (len(pattern)-1)*512+1024+301
        events = candidate_events(scores, np.arange(len(pattern))*512, samples, 22050)
        return events, merge_qualified_candidates(events, samples), samples

    def test_empty(self):
        self.assertEqual(merge_qualified_candidates([], 1000), [])

    def test_one_gap_and_provenance(self):
        original, result, samples = self.prepare([1,1,0,1,1,0,0])
        before = deepcopy(original)
        self.assertEqual(len(result), 1)
        event = result[0]
        self.assertEqual(event['component_event_indices'], [0,1])
        self.assertEqual(event['bridged_negative_windows'], [2])
        self.assertEqual(event['components'], original)
        self.assertEqual(event['estimated_start_sample'], original[0]['estimated_start_sample'])
        self.assertEqual(event['decision_sample'], original[0]['decision_sample'])
        self.assertEqual(event['estimated_end_sample'], original[1]['estimated_end_sample'])
        self.assertEqual(event['closure_available_sample'], samples)
        event['components'][0]['active_channels'].append(7)
        self.assertEqual(original, before)

    def test_two_gap_preserves_two_blows(self):
        _, result, _ = self.prepare([1,1,0,0,1,1])
        self.assertEqual(len(result), 2)

    def test_two_singletons_cannot_qualify(self):
        original, result, _ = self.prepare([1,0,1])
        self.assertEqual(original, [])
        self.assertEqual(result, [])

    def test_singleton_after_qualified_does_not_extend(self):
        original, result, _ = self.prepare([1,1,0,1,0])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['estimated_end_sample'], original[0]['estimated_end_sample'])
        self.assertEqual(result[0]['bridged_negative_windows'], [])

    def test_chained_merges_and_eof_tail(self):
        original, result, samples = self.prepare([1,1,0,1,1,0,1,1])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['component_event_indices'], [0,1,2])
        self.assertEqual(result[0]['bridged_negative_windows'], [2,5])
        self.assertEqual(result[0]['closed_by'], 'end_of_file')
        self.assertEqual(result[0]['estimated_end_sample'], samples-301)
        self.assertFalse(result[0]['natural_offset_verified'])

    def test_channel_union_and_strongest_component(self):
        original, _, samples = self.prepare([1,1,0,1,1])
        original[0].update(active_channels=[0],peak_score=.8,audition_channel=0)
        original[1].update(active_channels=[2],peak_score=.95,audition_channel=2)
        event = merge_qualified_candidates(original,samples)[0]
        self.assertEqual(event['active_channels'],[0,2])
        self.assertEqual(event['audition_channel'],2)
        self.assertEqual(event['peak_score'],.95)


if __name__ == '__main__':
    unittest.main()
