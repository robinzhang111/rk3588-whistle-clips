"""B-Human 8-bit PCM, channel-0 traditional offline baseline (no UTC inference)."""
import math
import wave
from collections import deque

import numpy as np


def decode_u8(raw):
    return (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128) / 128


def read_channel0(path):
    with wave.open(str(path), "rb") as audio:
        if audio.getsampwidth() != 1 or audio.getnchannels() != 2 or audio.getcomptype() != "NONE":
            raise ValueError("This baseline accepts only original 8-bit stereo PCM")
        rate, count = audio.getframerate(), audio.getnframes()
        raw = audio.readframes(count)
    if len(raw) != count * 2:
        raise ValueError("PCM length does not match WAV header")
    return decode_u8(raw).reshape(-1, 2)[:, 0], rate


def frame_batches(signal, rate):
    width, hop = round(.02 * rate), round(.01 * rate)
    starts = np.arange(0, len(signal), hop)
    padded = np.pad(signal, (0, width))
    windows = np.lib.stride_tricks.sliding_window_view(padded, width)
    for offset in range(0, len(starts), 2048):
        positions = starts[offset:offset+2048]
        frames = windows[positions]
        centered = frames - frames.mean(axis=1, keepdims=True)
        yield positions, centered, np.abs(np.fft.rfft(centered * np.hanning(width), axis=1)) ** 2


def extract_features(signal, rate, band):
    freq = np.fft.rfftfreq(round(.02 * rate), 1 / rate)
    target = (freq >= band[0]) & (freq <= band[1])
    broad = (freq >= 300) & (freq <= 10000)
    starts, rms, ratio = [], [], []
    for positions, frames, power in frame_batches(signal, rate):
        starts.extend(positions.tolist())
        rms.extend(np.sqrt(np.mean(frames ** 2, axis=1)).tolist())
        denominator = power[:, broad].sum(axis=1)
        ratio.extend(np.divide(power[:, target].sum(axis=1), denominator,
                               out=np.zeros(len(power)), where=denominator > 0).tolist())
    return dict(starts=np.array(starts), rms=np.array(rms), ratio=np.array(ratio),
                rate=rate, width=round(.02*rate), hop=round(.01*rate))


def detect_events(features, config, nframes):
    positive = np.flatnonzero((features["rms"] >= config["rms_threshold"]) &
                              (features["ratio"] >= config["band_ratio_threshold"]))
    if not len(positive):
        return []
    rate, hop = features["rate"], features["hop"]
    # Gap counts unqualified frame hops, independent of ground truth.
    cuts = np.flatnonzero((np.diff(positive)-1)*hop > config["merge_gap_seconds"]*rate) + 1
    required = math.ceil(config["min_positive_seconds"] * rate / hop)
    events = []
    for group in np.split(positive, cuts):
        if len(group) < required:
            continue
        events.append(dict(estimated_start_sample=int(features["starts"][group[0]]),
                           estimated_end_sample=min(nframes, int(features["starts"][group[-1]])+features["width"]),
                           decision_sample=min(nframes, int(features["starts"][group[required-1]])+features["width"])))
    return events


def score_events(predictions, labels, rate, nframes, tolerance=.2):
    for prediction in predictions:
        start = prediction["estimated_start_sample"]
        if type(start) is not int or not 0 <= start < nframes:
            raise ValueError("Invalid prediction sample position")
    # Unit-capacity minimum-cost flow: augment to maximum count, then minimize
    # integer onset error. Binary edge rewards break equal-cost ties exactly by
    # original prediction index, then original label index (not time-sort order).
    count_p, count_g = len(predictions), len(labels)
    source, sink = count_p+count_g, count_p+count_g+1
    graph = [[] for _ in range(sink+1)]
    def edge(a, b, cost):
        forward = [b, len(graph[b]), 1, cost]
        graph[a].append(forward)
        graph[b].append([a, len(graph[a])-1, 0, -cost])
        return forward
    for pi in range(count_p):
        edge(source, pi, 0)
    for gi in range(count_g):
        edge(count_p+gi, sink, 0)
    eligible = []
    for pi, prediction in enumerate(predictions):
        for gi, label in enumerate(labels):
            error = abs(prediction["estimated_start_sample"]-label["original_start"])
            if error <= round(tolerance*rate):
                eligible.append((pi, gi, error))
    scale = 1 << len(eligible)
    links = [(pi, gi, edge(pi, count_p+gi, error*scale-(1 << (len(eligible)-rank-1))))
             for rank, (pi, gi, error) in enumerate(eligible)]
    while True:
        distances, parents = [None]*len(graph), [None]*len(graph)
        distances[source] = 0
        queue, queued = deque([source]), {source}
        while queue:
            a = queue.popleft()
            queued.remove(a)
            for index, item in enumerate(graph[a]):
                b, reverse, capacity, cost = item
                if capacity and (distances[b] is None or distances[a]+cost < distances[b]):
                    distances[b], parents[b] = distances[a]+cost, (a, index)
                    if b not in queued:
                        queue.append(b)
                        queued.add(b)
        if parents[sink] is None:
            break
        b = sink
        while b != source:
            a, index = parents[b]
            item = graph[a][index]
            item[2] = 0
            graph[b][item[1]][2] = 1
            b = a
    pairs = [(pi, gi) for pi, gi, item in links if item[2] == 0]
    used_p, used_g = {pi for pi, gi in pairs}, {gi for pi, gi in pairs}
    matches = []
    for pi, gi in pairs:
        prediction, label = predictions[pi], labels[gi]
        error = (prediction["estimated_start_sample"]-label["original_start"])/rate
        matches.append(dict(prediction_index=pi, label_index=gi, signed_onset_error_seconds=error,
                            absolute_onset_error_seconds=abs(error),
                            decision_delay_seconds=(prediction["decision_sample"]-label["original_start"])/rate
                            if "decision_sample" in prediction else None))
    unmatched = [i for i in range(len(predictions)) if i not in used_p]
    duplicates = sum(any(abs(predictions[i]["estimated_start_sample"]-labels[j]["original_start"])
                         <= round(tolerance*rate) for j in used_g) for i in unmatched)
    tp, fp, fn = len(pairs), len(unmatched), len(labels)-len(pairs)
    return dict(TP=tp, FP=fp, FN=fn, precision=tp/(tp+fp) if tp+fp else None,
                recall=tp/(tp+fn) if tp+fn else None, F1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,
                undefined_reasons={name: reason for name, reason, defined in
                    [("precision", "no predictions", tp+fp), ("recall", "no targets", tp+fn),
                     ("F1", "no predictions or targets", 2*tp+fp+fn)] if not defined},
                FP_per_hour=fp/(nframes/rate/3600), duplicate_FP=duplicates, matches=matches,
                unmatched_prediction_indices=unmatched,
                missed_label_indices=[i for i in range(len(labels)) if i not in used_g])
