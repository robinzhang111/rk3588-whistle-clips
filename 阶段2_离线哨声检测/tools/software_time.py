"""DMA software estimates from this segment's own status, never hardware signoff."""
from datetime import datetime, timedelta, timezone
from fractions import Fraction

from sample_time import SamplingAnchor, SegmentTime, nonnegative_integer


class SoftwareTime(SegmentTime):
    def __init__(self, segment_id, relation_evidence, original_at_target_zero=0,
                 tolerance_ns=5000000, maximum_age_ns=5*10**9):
        if not isinstance(relation_evidence, str) or not relation_evidence.strip():
            raise ValueError('Measured resampling relation evidence required')
        nonnegative_integer(tolerance_ns, 'tolerance_ns')
        nonnegative_integer(maximum_age_ns, 'maximum_age_ns')
        super().__init__(segment_id, original_at_target_zero=original_at_target_zero,
                         relation_evidence=relation_evidence)
        self.tolerance_ns, self.maximum_age_ns = tolerance_ns, maximum_age_ns
        self.last_sample = self.last_monotonic = None
        self.latest_source_ns = None
        self.observation_window_ns = None

    def observe(self, sample, snapshot, before_ns, realtime_ns, paired_ns, after_ns):
        for name, value in [('sample', sample), ('before', before_ns), ('realtime', realtime_ns),
                            ('paired', paired_ns), ('after', after_ns)]:
            nonnegative_integer(value, name)
        if self.invalid_reason:
            return
        if not before_ns <= paired_ns <= after_ns:
            self.invalidate('invalid_clock_bracket')
            return
        if (snapshot['state'] != 3 or snapshot['delay'] < 0
                or (self.last_sample is not None and sample < self.last_sample)
                or (self.last_monotonic is not None and paired_ns < self.last_monotonic)):
            self.invalidate('input_or_clock_discontinuity')
            return
        source_ns = snapshot['htstamp_ns']
        # A status timestamp can predate the syscall, but must not be stale/future.
        if not 0 < source_ns <= after_ns or after_ns-source_ns > self.maximum_age_ns:
            self.invalidate('stale_or_invalid_status_timestamp')
            return
        if self.latest_source_ns is not None and source_ns < self.latest_source_ns:
            self.invalidate('status_timestamp_regression')
            return
        wall = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=realtime_ns//1000)
        wall_string = wall.isoformat().replace('+00:00', 'Z')
        if self.anchor is None:
            # N+delay already includes queued frames; do not add avail again.
            mapped_ns = realtime_ns + source_ns-paired_ns
            mapped = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=round(Fraction(mapped_ns, 1000)))
            self.anchor = SamplingAnchor(self.segment_id, sample+snapshot['delay'], 48000,
                source_ns, mapped.isoformat().replace('+00:00', 'Z'),
                'same-source ALSA MONOTONIC status; application N+delay boundary; '
                'userspace realtime pairing; codec and absolute UTC error unmeasured',
                after_ns-before_ns+1000, kind='software_estimate')
        else:
            self.check_clock(paired_ns, wall_string, self.tolerance_ns)
            expected = Fraction((sample+snapshot['delay']-self.anchor.original_sample)*10**9, 48000)
            if abs((source_ns-self.anchor.monotonic_ns)-expected) > self.tolerance_ns:
                self.invalidate('sample_clock_discontinuity')
        self.last_sample, self.last_monotonic = sample, paired_ns
        self.latest_source_ns = source_ns
        self.observation_window_ns = after_ns-before_ns

    def candidate_time(self, candidate):
        result = super().candidate_time(candidate)
        result.update(sampling_utc_verified=False, clock_source_error_ns=None,
                      codec_delay_error_ns=None, total_uncertainty_ns=None,
                      observation_window_ns=self.observation_window_ns,
                      time_quality='software_estimate' if result['status']=='software_estimate' else 'unavailable')
        return result
