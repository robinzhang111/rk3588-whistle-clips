"""Diagnostic ALSA status snapshots; no device open, capture, or UTC promotion."""
import ctypes as C
import sys
from copy import deepcopy

from sample_time import SegmentTime, nonnegative_integer, utc


class Timespec(C.Structure):
    _fields_ = [('seconds', C.c_long), ('nanoseconds', C.c_long)]


class AudioReport(C.Structure):
    _fields_ = [('valid', C.c_uint, 1), ('actual_type', C.c_uint, 4),
                ('accuracy_report', C.c_uint, 1), ('accuracy', C.c_uint)]


class AudioConfig(C.Structure):
    _fields_ = [('type_requested', C.c_uint, 4), ('report_delay', C.c_uint, 1)]


class AlsaStatusReader:
    """Borrow an already configured PCM handle; never open/recover/close it.

    shortcut: native Linux LP64 ABI only; verify layouts against board headers
    before a separately authorized live run. Fake-library tests prove wiring only.
    """
    def __init__(self, library, audio_config=None):
        if isinstance(library, C.CDLL) and (sys.platform != 'linux' or C.sizeof(C.c_long) != 8):
            raise RuntimeError('Live reader requires Linux LP64; ABI validation still required')
        self.library = library
        self.audio_config = audio_config
        signatures = {
            'snd_pcm_status_malloc': ([C.POINTER(C.c_void_p)], C.c_int),
            'snd_pcm_status_free': ([C.c_void_p], None),
            'snd_pcm_status': ([C.c_void_p, C.c_void_p], C.c_int),
            'snd_pcm_status_get_state': ([C.c_void_p], C.c_int),
            'snd_pcm_status_get_avail': ([C.c_void_p], C.c_ulong),
            'snd_pcm_status_get_delay': ([C.c_void_p], C.c_long),
            'snd_pcm_status_get_audio_htstamp_report':
                ([C.c_void_p, C.POINTER(AudioReport)], None),
        }
        for name in ['htstamp', 'audio_htstamp', 'driver_htstamp']:
            signatures['snd_pcm_status_get_' + name] = (
                [C.c_void_p, C.POINTER(Timespec)], None)
        for name, (arguments, result) in signatures.items():
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result
        if audio_config is not None:
            function = library.snd_pcm_status_set_audio_htstamp_config
            function.argtypes = [C.c_void_p, C.POINTER(AudioConfig)]
            function.restype = None

    def read(self, pcm):
        status = C.c_void_p()
        result = self.library.snd_pcm_status_malloc(C.byref(status))
        if result < 0:
            raise OSError(result, 'snd_pcm_status_malloc failed')
        try:
            if self.audio_config is not None:
                self.library.snd_pcm_status_set_audio_htstamp_config(status, C.byref(self.audio_config))
            result = self.library.snd_pcm_status(pcm, status)
            if result < 0:
                raise OSError(result, 'snd_pcm_status failed; no recovery attempted')
            snapshot = {}
            for name in ['state', 'avail', 'delay']:
                snapshot[name] = getattr(self.library, 'snd_pcm_status_get_' + name)(status)
            for name in ['htstamp', 'audio_htstamp', 'driver_htstamp']:
                stamp = Timespec()
                getattr(self.library, 'snd_pcm_status_get_' + name)(status, C.byref(stamp))
                if stamp.seconds < 0 or not 0 <= stamp.nanoseconds < 10**9:
                    raise ValueError('Invalid ALSA timespec')
                snapshot[name + '_ns'] = stamp.seconds * 10**9 + stamp.nanoseconds
            report = AudioReport()
            self.library.snd_pcm_status_get_audio_htstamp_report(status, C.byref(report))
            snapshot['audio_report'] = dict(valid=bool(report.valid),
                actual_type=report.actual_type, accuracy_report=bool(report.accuracy_report),
                accuracy_ns=report.accuracy if report.accuracy_report else None)
            return snapshot
        finally:
            self.library.snd_pcm_status_free(status)


class SamplingProbe:
    """Count raw frames per continuous segment; status timestamps stay evidence.

    original_sample is the application read boundary, not an ADC timestamp.
    Only explicit synthetic fixtures may supply a mapping in this local version.
    """
    def __init__(self, segment_id, sample_rate=48000, mapping=None, tolerance_ns=0):
        nonnegative_integer(sample_rate, 'sample_rate')
        nonnegative_integer(tolerance_ns, 'tolerance_ns')
        if not sample_rate:
            raise ValueError('Positive sample rate required')
        self.mapping = mapping or SegmentTime(segment_id)
        if self.mapping.segment_id != segment_id:
            raise ValueError('Mapping belongs to another segment')
        anchor = self.mapping.anchor
        if anchor and (anchor.kind != 'synthetic' or anchor.sample_rate != sample_rate):
            raise ValueError('Local probe accepts matching synthetic anchors only')
        self.segment_id, self.sample_rate = segment_id, sample_rate
        self.tolerance_ns = tolerance_ns
        self.previous_sample = self.previous_monotonic = None
        self.previous_utc = None

    def interrupt(self, reason):
        if self.mapping.invalid_reason is None:
            self.mapping.invalidate(reason)
        return dict(segment_id=self.segment_id, kind='interruption',
                    reason=self.mapping.invalid_reason, formal_utc_upload=False)

    def observe(self, original_sample, snapshot, monotonic_ns, utc_time,
                clock_evidence, timestamp_clock, timestamp_mode):
        nonnegative_integer(original_sample, 'original_sample')
        nonnegative_integer(monotonic_ns, 'monotonic_ns')
        current_utc = utc(utc_time)
        if not isinstance(clock_evidence, str) or not clock_evidence.strip():
            raise ValueError('Explicit clock evidence required (unknown is allowed)')
        if timestamp_clock not in ['unknown', 'realtime', 'monotonic', 'monotonic_raw']:
            raise ValueError('Unknown timestamp clock')
        if timestamp_mode not in ['unknown', 'none', 'enabled']:
            raise ValueError('Unknown timestamp mode')
        # Snapshot fields are raw evidence, never used to manufacture an anchor.
        for name in ['state', 'avail', 'htstamp_ns', 'audio_htstamp_ns', 'driver_htstamp_ns']:
            nonnegative_integer(snapshot[name], name)
        if isinstance(snapshot['delay'], bool) or not isinstance(snapshot['delay'], int):
            raise ValueError('delay must be a signed integer')
        report = snapshot['audio_report']
        for name in ['valid', 'accuracy_report']:
            if not isinstance(report[name], bool):
                raise ValueError(name + ' must be boolean')
        nonnegative_integer(report['actual_type'], 'actual_type')
        if report['accuracy_report']:
            nonnegative_integer(report['accuracy_ns'], 'accuracy_ns')
        if snapshot['state'] != 3:  # ALSA RUNNING; xrun/suspend/disconnect invalidate.
            self.interrupt('pcm_state_' + str(snapshot['state']))
        if self.previous_sample is not None and original_sample < self.previous_sample:
            self.interrupt('sample_counter_regression')
        if self.previous_monotonic is not None and monotonic_ns < self.previous_monotonic:
            self.interrupt('monotonic_regression')
        if self.previous_utc is not None:
            elapsed = current_utc - self.previous_utc
            wall_ns = (elapsed.days * 86400 + elapsed.seconds) * 10**9 + elapsed.microseconds * 1000
            if abs(wall_ns - (monotonic_ns - self.previous_monotonic)) > self.tolerance_ns:
                self.interrupt('observed_clock_discontinuity')
        clock = (dict(status='invalid', reason=self.mapping.invalid_reason)
                 if self.mapping.invalid_reason else
                 self.mapping.check_clock(monotonic_ns, utc_time, self.tolerance_ns))
        self.previous_sample, self.previous_monotonic = original_sample, monotonic_ns
        self.previous_utc = current_utc
        return dict(segment_id=self.segment_id, kind='status', original_sample=original_sample,
            sample_rate=self.sample_rate, position_basis='application_read_boundary_frames_per_channel',
            snapshot=deepcopy(snapshot), observed_monotonic_ns=monotonic_ns,
            observed_utc=utc_time, clock_evidence=clock_evidence,
            timestamp_clock=timestamp_clock, timestamp_mode=timestamp_mode,
            clock_check=clock, invalid_reason=self.mapping.invalid_reason,
            sampling_anchor_verified=False, resampling_relation_verified=False,
            formal_utc_upload=False)
