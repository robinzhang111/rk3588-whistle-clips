"""Explicit per-segment sampling anchors; never invent anchors from arrival time."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fractions import Fraction


def nonnegative_integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(name + ' must be a nonnegative integer')
    return value


def utc(value):
    if not isinstance(value, str):
        raise ValueError('UTC must be an explicit string')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError('Explicit UTC timezone required')
    return parsed


@dataclass(frozen=True)
class SamplingAnchor:
    segment_id: str
    original_sample: int
    sample_rate: int
    monotonic_ns: int
    utc_time: str
    evidence: str
    uncertainty_ns: int
    kind: str = 'synthetic'

    def __post_init__(self):
        if not isinstance(self.segment_id, str) or not self.segment_id:
            raise ValueError('Segment ID required')
        for name in ['original_sample', 'sample_rate', 'monotonic_ns', 'uncertainty_ns']:
            nonnegative_integer(getattr(self, name), name)
        if self.sample_rate == 0 or not isinstance(self.evidence, str) or not self.evidence.strip():
            raise ValueError('Positive rate and evidence required')
        if self.kind not in ['synthetic', 'verified_sampling', 'software_estimate']:
            raise ValueError('Unknown anchor kind')
        utc(self.utc_time)


class SegmentTime:
    """Caller supplies resampler correspondence; missing relation stays unknown.

    verified_sampling means externally accepted capture evidence, not verification
    performed by this class. Wall/monotonic checks cannot prove sample continuity.
    """
    def __init__(self, segment_id, anchor=None, target_rate=22050,
                 original_at_target_zero=None, relation_evidence=None):
        if not isinstance(segment_id, str) or not segment_id:
            raise ValueError('Segment ID required')
        nonnegative_integer(target_rate, 'target_rate')
        if not target_rate:
            raise ValueError('Positive target rate required')
        if anchor is not None and anchor.segment_id != segment_id:
            raise ValueError('Anchor belongs to another segment')
        if original_at_target_zero is not None:
            original_at_target_zero = Fraction(original_at_target_zero)
            if original_at_target_zero < 0:
                raise ValueError('Negative original sample')
        self.segment_id, self.anchor, self.target_rate = segment_id, anchor, target_rate
        self.original_at_target_zero = original_at_target_zero
        self.relation_evidence = relation_evidence
        self.invalid_reason = None

    def invalidate(self, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('Discontinuity reason required')
        self.invalid_reason = reason

    def check_clock(self, monotonic_ns, utc_time, tolerance_ns):
        nonnegative_integer(monotonic_ns, 'monotonic_ns')
        nonnegative_integer(tolerance_ns, 'tolerance_ns')
        current = utc(utc_time)
        if self.anchor is None:
            return dict(status='unknown_anchor', offset_change_ns=None)
        elapsed = current - utc(self.anchor.utc_time)
        wall_ns = (elapsed.days * 86400 + elapsed.seconds) * 10**9 + elapsed.microseconds * 1000
        mono_ns = monotonic_ns - self.anchor.monotonic_ns
        difference = wall_ns - mono_ns
        if mono_ns < 0 or abs(difference) > tolerance_ns:
            self.invalidate('clock_discontinuity')
        return dict(status='invalid' if self.invalid_reason else 'within_tolerance',
                    offset_change_ns=difference, tolerance_ns=tolerance_ns,
                    proves_sampling_continuity=False)

    def candidate_time(self, candidate):
        sample = nonnegative_integer(candidate['estimated_start_sample'], 'estimated_start_sample')
        if candidate.get('segment_id') != self.segment_id:
            raise ValueError('Candidate belongs to another segment')
        result = dict(segment_id=self.segment_id, target_start_sample=sample,
                      target_start_seconds=sample/self.target_rate,
                      status='relative_only', estimated_utc=None, synthetic_utc=None,
                      formal_utc_upload=False)
        reason = self.invalid_reason
        if self.anchor is None:
            reason = reason or 'unknown_sampling_anchor'
        elif self.original_at_target_zero is None or not isinstance(self.relation_evidence, str) or not self.relation_evidence.strip():
            reason = reason or 'unknown_resampling_relation'
        if reason:
            return dict(result, reason=reason)
        original = self.original_at_target_zero + Fraction(sample*self.anchor.sample_rate, self.target_rate)
        elapsed = (original-self.anchor.original_sample)/self.anchor.sample_rate
        # ISO datetime output rounds to microseconds; keep exact rational position too.
        mapped = utc(self.anchor.utc_time) + timedelta(microseconds=round(elapsed*10**6))
        key = 'synthetic_utc' if self.anchor.kind == 'synthetic' else 'estimated_utc'
        result.update(status=self.anchor.kind, reason=None,
                      original_sample_numerator=original.numerator,
                      original_sample_denominator=original.denominator,
                      anchor_evidence=self.anchor.evidence, relation_evidence=self.relation_evidence,
                      anchor_uncertainty_ns=self.anchor.uncertainty_ns,
                      output_rounding_ns=500, total_uncertainty_measured=False)
        result[key] = mapped.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
        return result
