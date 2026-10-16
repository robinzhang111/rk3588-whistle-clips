"""Offline merge of v1-qualified candidates separated by one negative window."""
from copy import deepcopy


def merge_qualified_candidates(events, sample_count):
    groups = []
    for event in events:
        if groups and event['first_window']-groups[-1][-1]['last_window']-1 == 1:
            groups[-1].append(event)
        else:
            groups.append([event])
    merged = []
    for group in groups:
        first, last = group[0], group[-1]
        result = deepcopy(first)
        for key in ['last_window', 'estimated_end_sample', 'estimated_end_seconds',
                    'closed_by', 'reaches_last_complete_window']:
            result[key] = last[key]
        peak = max(group, key=lambda e: e['peak_score'])
        result.update(event_index=len(merged), peak_score=peak['peak_score'],
                      audition_channel=peak['audition_channel'],
                      active_channels=sorted({c for e in group for c in e['active_channels']}),
                      components=deepcopy(group),
                      component_event_indices=[e['event_index'] for e in group],
                      bridged_negative_windows=[e['last_window']+1 for e in group[:-1]],
                      event_logic='v2A', closure_available_sample=sample_count,
                      closure_timing_scope='offline merge after complete recording; not online closure latency')
        merged.append(result)
    return merged
