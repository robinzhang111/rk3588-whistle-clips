import ctypes as C
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).absolute().parent.parent/'tools'))
from sampling_probe import AlsaStatusReader, AudioReport, Timespec, SamplingProbe
from sample_time import SamplingAnchor, SegmentTime
from simulate_sampling_probe import observe, synthetic_probe, synthetic_snapshot, simulate


def fake_library(allocation_error=0, status_error=0, getter_error=False):
    calls = []
    def allocate(pointer):
        calls.append('allocate')
        C.cast(pointer, C.POINTER(C.c_void_p))[0] = C.c_void_p(123)
        return allocation_error
    def status(pcm, pointer):
        calls.append('status')
        return status_error
    def stamp(pointer, result):
        if getter_error:
            raise ValueError('injected getter error')
        target = C.cast(result, C.POINTER(Timespec)).contents
        target.seconds, target.nanoseconds = 5, 123
    def report(pointer, result):
        target = C.cast(result, C.POINTER(AudioReport)).contents
        target.valid, target.actual_type = 1, 1
        target.accuracy_report, target.accuracy = 0, 999
    functions = dict(snd_pcm_status_malloc=allocate, snd_pcm_status=status,
        snd_pcm_status_free=lambda pointer: calls.append('free'),
        snd_pcm_status_get_state=lambda pointer: 3,
        snd_pcm_status_get_avail=lambda pointer: 2**33,
        snd_pcm_status_get_delay=lambda pointer: -2,
        snd_pcm_status_get_audio_htstamp_report=report)
    for name in ['htstamp', 'audio_htstamp', 'driver_htstamp']:
        functions['snd_pcm_status_get_' + name] = stamp
    return SimpleNamespace(**functions), calls


class SamplingProbeTests(unittest.TestCase):
    def test_status_one_snapshot_signed_delay_and_free(self):
        library, calls = fake_library()
        result = AlsaStatusReader(library).read(C.c_void_p(456))
        self.assertEqual(calls, ['allocate', 'status', 'free'])
        self.assertEqual(result['avail'], 2**33)
        self.assertEqual(result['delay'], -2)
        self.assertEqual(result['htstamp_ns'], 5000000123)
        self.assertIsNone(result['audio_report']['accuracy_ns'])
        self.assertEqual(library.snd_pcm_status_get_delay.restype, C.c_long)
        self.assertEqual(library.snd_pcm_status_get_avail.restype, C.c_ulong)

    def test_allocation_status_and_getter_failures(self):
        for options, expected, error in [
                (dict(allocation_error=-12), ['allocate'], OSError),
                (dict(status_error=-32), ['allocate', 'status', 'free'], OSError),
                (dict(getter_error=True), ['allocate', 'status', 'free'], ValueError)]:
            library, calls = fake_library(**options)
            with self.assertRaises(error):
                AlsaStatusReader(library).read(C.c_void_p(456))
            self.assertEqual(calls, expected)

    def test_unknown_time_and_type_fallback_never_promote(self):
        probe = SamplingProbe('unknown')
        row = observe(probe, audio_report=dict(valid=True, actual_type=1,
            accuracy_report=True, accuracy_ns=0))
        self.assertFalse(row['sampling_anchor_verified'])
        self.assertFalse(row['formal_utc_upload'])
        self.assertEqual(row['clock_check']['status'], 'unknown_anchor')
        self.assertIsNone(probe.mapping.anchor)

    def test_interrupt_sticky_and_new_segment_unanchored(self):
        probe = synthetic_probe('s')
        observe(probe, state=4)
        probe.interrupt('restart')
        row = observe(probe)
        self.assertEqual(row['invalid_reason'], 'pcm_state_4')
        self.assertIsNone(SamplingProbe('new').mapping.anchor)

    def test_snapshot_is_copied(self):
        snapshot = synthetic_snapshot()
        row = SamplingProbe('s').observe(48000, snapshot, 10**9, '2000-01-01T00:00:01Z',
                                        'unknown', 'unknown', 'unknown')
        snapshot['audio_report']['actual_type'] = 4
        self.assertEqual(row['snapshot']['audio_report']['actual_type'], 1)
        self.assertEqual(row['original_sample'], 48000)
        self.assertEqual(row['position_basis'], 'application_read_boundary_frames_per_channel')
        self.assertEqual(row['timestamp_clock'], 'unknown')

    def test_unanchored_clock_jump_invalidates(self):
        probe = SamplingProbe('s')
        observe(probe)
        row = observe(probe, mono=2*10**9, wall='2000-01-01T00:00:04Z')
        self.assertEqual(row['invalid_reason'], 'observed_clock_discontinuity')

    def test_external_anchor_cannot_be_promoted_by_probe(self):
        anchor = SamplingAnchor('s', 0, 48000, 0, '2000-01-01T00:00:00Z',
                                'external fixture', 0, kind='verified_sampling')
        with self.assertRaises(ValueError):
            SamplingProbe('s', mapping=SegmentTime('s', anchor))

    def test_bad_input(self):
        for sample in [-1, True, 1.5]:
            with self.assertRaises(ValueError):
                observe(synthetic_probe('s'), sample=sample)
        with self.assertRaises(ValueError):
            SamplingProbe('s', sample_rate=0)
        with self.assertRaises(ValueError):
            observe(synthetic_probe('s'), delay=True)

    def test_simulated_scenarios_and_relative_only_after_failures(self):
        records = simulate()
        mappings = [row for row in records if row['kind'] == 'mapping_check']
        self.assertEqual(len(mappings), 10)
        self.assertEqual(sum(row['timing']['status'] == 'relative_only' for row in mappings), 8)
        self.assertTrue(all(not row['timing']['formal_utc_upload'] for row in mappings))


if __name__ == '__main__':
    unittest.main()
