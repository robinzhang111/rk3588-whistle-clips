"""Reference protocol checks for synthetic fixtures; no detector/model imports."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("legacy_event_evaluation", ROOT / "阶段2_离线哨声检测/tools/evaluate_testbench_events.py")
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


def merged(ranges):
    result = []
    for start, end in sorted(ranges):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def complement(ranges, frames):
    result, cursor = [], 0
    for start, end in merged(ranges):
        if start > cursor:
            result.append((cursor, start))
        cursor = end
    if cursor < frames:
        result.append((cursor, frames))
    return result


def interval(row, frames):
    start, end = row["start_sample"], row["end_sample"]
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= frames:
        raise ValueError("Invalid native sample interval")
    return start, end


def score_segment(predictions, labels, *, rate, frames, coverage, exclusions=(),
                  tolerance_samples, fp_hour_eligible=False):
    """Natural-onset track only; one recording, one uninterrupted sample grid."""
    if type(rate) is not int or rate <= 0 or type(frames) is not int or frames <= 0:
        raise ValueError("Invalid audio grid")
    if type(tolerance_samples) is not int or tolerance_samples < 0:
        raise ValueError("Tolerance must be explicit integer native samples")
    if type(fp_hour_eligible) is not bool:
        raise ValueError("Duration eligibility must be explicit boolean")
    cov = merged([interval(dict(start_sample=a, end_sample=b), frames) for a, b in coverage])
    holes = complement(cov, frames)
    holes += [interval(dict(start_sample=a, end_sample=b), frames) for a, b in exclusions]
    for label in labels:
        interval(label, frames)
        if label.get("boundary_kind") != "human_natural_approximate":
            raise ValueError("Publisher intervals, points and window labels require separate tracks")
        if label.get("boundary_clipped", False):
            holes.append((label["start_sample"], label["end_sample"]))
    # Fixed guard before observing predictions. Closure uses labels only.
    expand = lambda a, b: (max(0, a-tolerance_samples), min(frames, b+tolerance_samples))
    holes = merged([expand(a, b) for a, b in holes])
    while True:
        additions = [expand(label["start_sample"], label["end_sample"])
                     for label in labels if any(label["start_sample"] < b and a < label["end_sample"]
                                              for a, b in holes)]
        enlarged = merged(holes + additions)
        if enlarged == holes:
            break
        holes = enlarged
    islands = complement(holes, frames)
    scored_p, scored_g, ignored_p, excluded_g, crossing = [], [], [], [], []
    pairs = []
    for i, p in enumerate(predictions):
        start, end = interval(p, frames)
        island = next(((a, b) for a, b in islands if a <= start < b), None)
        if island is None:
            ignored_p.append(i)
        elif end > island[1]:
            crossing.append(i)
    for j, g in enumerate(labels):
        if not any(a <= g["start_sample"] and g["end_sample"] <= b for a, b in islands):
            excluded_g.append(j)
    # Do not match across a masked gap or input interruption.
    for a, b in islands:
        ps = [(i, p) for i, p in enumerate(predictions) if a <= p["start_sample"] < b]
        gs = [(j, g) for j, g in enumerate(labels) if a <= g["start_sample"] and g["end_sample"] <= b]
        scored_p.extend(i for i, p in ps)
        scored_g.extend(j for j, g in gs)
        native = [dict(p, estimated_start_sample=p["start_sample"]) for i, p in ps]
        pairs += [(ps[i][0], gs[j][0]) for i, j in legacy.match(native, [g for j, g in gs], tolerance_samples)]
    matched_p, matched_g = {i for i, j in pairs}, {j for i, j in pairs}
    fp = sorted(set(scored_p)-matched_p)
    fn = sorted(set(scored_g)-matched_g)
    duplicates = [i for i in fp if any(
        labels[j]["start_sample"]-tolerance_samples <= predictions[i]["start_sample"] < labels[j]["end_sample"]
        or abs(predictions[i]["start_sample"]-labels[j]["start_sample"]) <= tolerance_samples
        for j in matched_g)]
    tp = len(pairs)
    duration = sum(b-a for a, b in islands)/rate
    ratio = lambda n, d: n/d if d else None
    return dict(TP=tp, FP=len(fp), FN=len(fn), precision=ratio(tp, tp+len(fp)),
        recall=ratio(tp, tp+len(fn)), F1=ratio(2*tp, 2*tp+len(fp)+len(fn)),
        duplicate_FP=len(duplicates), duplicate_rate=ratio(len(duplicates), len(scored_p)),
        FP_per_hour=len(fp)*3600/duration if fp_hour_eligible and duration else None,
        FP_per_hour_reason=None if fp_hour_eligible and duration else "duration_not_eligible_or_zero",
        effective_seconds=duration, effective_ranges=islands, excluded_ranges=holes,
        matches=pairs, false_positive_indices=fp, false_negative_indices=fn,
        ignored_prediction_indices=ignored_p, excluded_label_indices=excluded_g,
        boundary_crossing_prediction_indices=crossing,
        onset_errors_seconds=[(predictions[i]["start_sample"]-labels[j]["start_sample"])/rate for i, j in pairs],
        offset_errors_seconds=[(predictions[i]["end_sample"]-labels[j]["end_sample"])/rate
                               for i, j in pairs if i not in crossing and not predictions[i].get("closed_at_boundary", False)
                               and labels[j].get("offset_reliable", False)])
