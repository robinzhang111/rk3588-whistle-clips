import ctypes as C
import errno
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).absolute().parent.parent/'tools'))
from sampling_capture import CapturePCM, capture, wait_for_start
from sampling_probe import SamplingProbe
from simulate_sampling_probe import synthetic_snapshot
from test_sampling_probe import fake_library


class Clock:
    def __init__(self):
        self.now = 0
    def monotonic_ns(self):
        self.now += 1000000
        return self.now
    def time_ns(self):
        return 946684800000000000 + self.now
    def sleep(self, seconds):
        self.now += round(seconds*10**9)


class PCM:
    configuration = dict(rate=48000, channels=2, format='S16_LE')
    def __init__(self, reads=None, state=3, failure=None, close_error=False):
        self.reads = iter(reads or [512]*100)
        self.state, self.failure, self.close_error = state, failure, close_error
        self.closed = self.started = False
    def start(self):
        self.started = True
    def read(self):
        if self.failure:
            raise self.failure
        return next(self.reads, 0)
    def status(self):
        return synthetic_snapshot(state=self.state)
    def close(self):
        self.closed = True
        if self.close_error:
            raise OSError('injected close failure')


def pcm_library(wrong_clock=False, wrong_rate=False, fail_apply=False):
    library, calls = fake_library()
    def pointer(result, value, kind=C.c_void_p):
        C.cast(result, C.POINTER(kind))[0] = kind(value)
        return 0
    def opened(result, *args):
        calls.append(('open', args))
        return pointer(result, 999)
    library.snd_pcm_open = opened
    library.snd_pcm_close = lambda handle: calls.append('close') or 0
    library.snd_pcm_drop = lambda handle: calls.append('drop') or 0
    library.snd_pcm_start = lambda handle: calls.append('start') or 0
    library.snd_pcm_format_value = lambda name: 2
    library.snd_pcm_set_params = lambda *args: calls.append(('set_params', args[1:])) or 0
    library.snd_pcm_readi = lambda *args: -errno.EAGAIN
    library.snd_pcm_avail_update = lambda handle: 1024
    library.snd_pcm_hw_params_get_rate = lambda params, rate, direction: pointer(rate, 44100 if wrong_rate else 48000, C.c_uint)
    library.snd_pcm_hw_params_get_channels = lambda params, channels: pointer(channels, 2, C.c_uint)
    library.snd_pcm_hw_params_get_format = lambda params, fmt: pointer(fmt, 2, C.c_int)
    library.snd_pcm_sw_params = lambda *args: -22 if fail_apply else 0
    for kind in ['hw', 'sw']:
        setattr(library, 'snd_pcm_'+kind+'_params_malloc', lambda result: pointer(result, 444))
        setattr(library, 'snd_pcm_'+kind+'_params_free', lambda params: calls.append('params_free'))
        setattr(library, 'snd_pcm_'+kind+'_params_current', lambda *args: 0)
    library.snd_pcm_sw_params_set_tstamp_mode = lambda *args: calls.append(('mode', args[-1])) or 0
    library.snd_pcm_sw_params_set_tstamp_type = lambda *args: calls.append(('type', args[-1])) or 0
    library.snd_pcm_sw_params_get_tstamp_mode = lambda params, mode: pointer(mode, 1, C.c_int)
    library.snd_pcm_sw_params_get_tstamp_type = lambda params, clock: pointer(clock, 0 if wrong_clock else 1, C.c_int)
    def audio_config(status, config):
        from sampling_probe import AudioConfig
        config = C.cast(config, C.POINTER(AudioConfig)).contents
        calls.append(('audio_config', config.type_requested, config.report_delay))
    library.snd_pcm_status_set_audio_htstamp_config = audio_config
    return library, calls


class CaptureTests(unittest.TestCase):
    def test_field_read_waits_for_block_without_consuming_tiny_fragments(self):
        library, calls = pcm_library()
        available = iter([16, 272, 1024])
        library.snd_pcm_avail_update = lambda handle: next(available)
        reads = []
        library.snd_pcm_readi = lambda handle, buffer, count: reads.append(count) or count
        pcm = CapturePCM(library, 'hw:test', buffer_us=1000000, minimum_frames=1024)
        try:
            self.assertEqual(pcm.read(), 0)
            self.assertEqual(pcm.read(), 0)
            self.assertEqual(reads, [])
            self.assertEqual(pcm.read(), 1024)
            self.assertEqual(reads, [1024])
            self.assertIn(('set_params', (2, 3, 2, 48000, 0, 1000000)), calls)
        finally:
            pcm.close()

    def test_field_availability_xrun_remains_failure_without_recovery(self):
        library, calls = pcm_library()
        library.snd_pcm_avail_update = lambda handle: -errno.EPIPE
        pcm = CapturePCM(library, 'hw:test', minimum_frames=1024)
        try:
            with self.assertRaises(OSError) as raised:
                pcm.read()
            self.assertEqual(raised.exception.errno, errno.EPIPE)
        finally:
            pcm.close()
        self.assertEqual(calls.count('close'), 1)

    def run_capture(self, pcm, stop=lambda: False, emit=None):
        rows = []
        probe = SamplingProbe('s', tolerance_ns=5000000)
        summary = capture(lambda: pcm, probe, emit or rows.append, stop, .05, Clock())
        return summary, rows, probe

    def test_nonblocking_short_reads_cumulative_count_and_timeout(self):
        pcm = PCM([0, 100, 200])
        result, rows, probe = self.run_capture(pcm)
        self.assertEqual(result['status'], 'closed')
        self.assertEqual(result['frames'], 300)
        self.assertEqual([row['original_sample'] for row in rows[1:]], [100, 300])
        self.assertTrue(result['resources_released'] and pcm.closed)
        self.assertEqual(result['close_reason'], 'duration_limit')
        self.assertIsNone(probe.mapping.anchor)
        self.assertTrue(all(not row['formal_utc_upload'] for row in rows))

    def test_user_stop_after_one_read(self):
        stops = iter([False, True])
        result, _, _ = self.run_capture(PCM(), stop=lambda: next(stops))
        self.assertEqual(result['status'], 'closed')
        self.assertEqual(result['close_reason'], 'user_stop')
        self.assertEqual(result['frames'], 512)

    def test_xrun_and_read_errors_invalidate_and_close(self):
        for pcm in [PCM(state=4), PCM(failure=OSError(errno.EPIPE, 'xrun')),
                    PCM(failure=KeyboardInterrupt())]:
            result, _, probe = self.run_capture(pcm)
            self.assertEqual(result['status'], 'failed')
            self.assertIsNotNone(probe.mapping.invalid_reason)
            self.assertTrue(pcm.closed)

    def test_status_and_emit_failures_close(self):
        pcm = PCM()
        def failed_status():
            raise OSError('status failed')
        pcm.status = failed_status
        result, _, probe = self.run_capture(pcm)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(pcm.closed)
        def failed_emit(row):
            raise OSError('disk full')
        pcm = PCM()
        result, _, probe = self.run_capture(pcm, emit=failed_emit)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(pcm.closed)

    def test_empty_capture_and_cleanup_error_not_success(self):
        for pcm in [PCM([0]), PCM(close_error=True)]:
            result, _, _ = self.run_capture(pcm)
            self.assertEqual(result['status'], 'failed')

    def test_wait_for_space_or_cancel(self):
        for keys, expected in [([None, 'x', ' '], True), ([None, '\x03'], False)]:
            keys = iter(keys)
            self.assertEqual(wait_for_start(lambda: next(keys), Clock()), expected)

    def test_fixed_params_readback_audio_request_and_cleanup(self):
        library, calls = pcm_library()
        pcm = CapturePCM(library, 'hw:test')
        self.assertNotIn('start', calls)
        self.assertEqual(calls[0], ('open', (b'hw:test', 1, 1)))
        self.assertIn(('set_params', (2, 3, 2, 48000, 0, 100000)), calls)
        pcm.start()
        self.assertEqual(pcm.read(), 0)
        pcm.status()
        self.assertIn(('audio_config', 2, 1), calls)
        self.assertEqual(pcm.configuration['timestamp_clock'], 'monotonic')
        pcm.close()
        pcm.close()
        self.assertEqual(calls.count('close'), 1)
        self.assertEqual(calls.count('drop'), 1)

    def test_bad_negotiation_or_configuration_closes_without_retry(self):
        for changes in [dict(wrong_clock=True), dict(wrong_rate=True), dict(fail_apply=True)]:
            library, calls = pcm_library(**changes)
            with self.assertRaises((ValueError, OSError)):
                CapturePCM(library, 'hw:test')
            self.assertEqual(calls.count('close'), 1)
            self.assertEqual(sum(isinstance(row, tuple) and row[0]=='open' for row in calls), 1)


if __name__ == '__main__':
    unittest.main()
