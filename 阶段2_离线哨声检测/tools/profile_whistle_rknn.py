"""Query Runtime compute time and separately time input/run/output calls.

ABI fields follow the archived official RKNN SDK 2.3.0 rknn_api.h.
Performance collection is enabled; these measurements include its overhead.
"""
import argparse
import ctypes as ct
from pathlib import Path
import time

import numpy as np
import detect_file as d


class Input(ct.Structure):
    _fields_ = [('index',ct.c_uint32),('buf',ct.c_void_p),('size',ct.c_uint32),
                ('pass_through',ct.c_uint8),('type',ct.c_int),('fmt',ct.c_int)]


class Output(ct.Structure):
    _fields_ = [('want_float',ct.c_uint8),('is_prealloc',ct.c_uint8),('index',ct.c_uint32),
                ('buf',ct.c_void_p),('size',ct.c_uint32)]


class Version(ct.Structure):
    _fields_ = [('api',ct.c_char*256),('driver',ct.c_char*256)]


class Detail(ct.Structure):
    _fields_ = [('data',ct.c_void_p),('length',ct.c_uint64)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--export',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--library',type=Path,required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    data = np.ascontiguousarray(np.load(str(args.export/'features.npy')),dtype=np.float32).reshape(-1,513)
    silent = np.load(str(args.export/'silent.npy')).reshape(-1)
    lib = ct.CDLL(str(args.library))
    context = ct.c_uint64()
    lib.rknn_init.argtypes = [ct.POINTER(ct.c_uint64),ct.c_void_p,ct.c_uint32,ct.c_uint32,ct.c_void_p]
    lib.rknn_query.argtypes = [ct.c_uint64,ct.c_int,ct.c_void_p,ct.c_uint32]
    lib.rknn_inputs_set.argtypes = [ct.c_uint64,ct.c_uint32,ct.POINTER(Input)]
    lib.rknn_run.argtypes = [ct.c_uint64,ct.c_void_p]
    lib.rknn_outputs_get.argtypes = [ct.c_uint64,ct.c_uint32,ct.POINTER(Output),ct.c_void_p]
    lib.rknn_outputs_release.argtypes = [ct.c_uint64,ct.c_uint32,ct.POINTER(Output)]
    lib.rknn_destroy.argtypes = [ct.c_uint64]
    def check(result):
        if result != 0:
            raise RuntimeError('Runtime return '+str(result))
    start = time.perf_counter()
    check(lib.rknn_init(ct.byref(context),ct.c_char_p(str(args.model).encode()),0,8,None))
    report = dict(initialize_seconds=time.perf_counter()-start,library_sha256=d.sha(args.library),
        model_sha256=d.sha(args.model),features_sha256=d.sha(args.export/'features.npy'),
        performance_collection_enabled=True,not_online_latency=True)
    scores = np.zeros(len(data),dtype=np.float32)
    timings = []
    try:
        counts = (ct.c_uint32*2)()
        check(lib.rknn_query(context,0,ct.byref(counts),ct.sizeof(counts)))
        if list(counts)!=[1,1]:
            raise ValueError('Expected one input/output')
        version = Version()
        check(lib.rknn_query(context,5,ct.byref(version),ct.sizeof(version)))
        report.update(api_version=version.api.decode(),driver_version=version.driver.decode())
        for i,feature in enumerate(data):
            if silent[i]:
                continue
            inp = Input(0,feature.ctypes.data,feature.nbytes,0,0,1)  # FLOAT32, NHWC
            out = Output(1,0,0,None,0)
            a = time.perf_counter()
            check(lib.rknn_inputs_set(context,1,ct.byref(inp)))
            b = time.perf_counter()
            check(lib.rknn_run(context,None))
            c = time.perf_counter()
            check(lib.rknn_outputs_get(context,1,ct.byref(out),None))
            end = time.perf_counter()
            if out.size!=4:
                raise ValueError('Expected scalar float32 output')
            scores[i] = ct.cast(out.buf,ct.POINTER(ct.c_float))[0]
            duration = ct.c_int64()
            check(lib.rknn_query(context,4,ct.byref(duration),ct.sizeof(duration)))
            timings.append([i,b-a,c-b,end-c,duration.value])
            detail = Detail()
            check(lib.rknn_query(context,3,ct.byref(detail),ct.sizeof(detail)))
            (args.output/'last-frame-perf.txt').write_bytes(ct.string_at(detail.data,detail.length))
            check(lib.rknn_outputs_release(context,1,ct.byref(out)))
        if not np.isfinite(scores).all() or not ((scores>=0)&(scores<=1)).all():
            raise ValueError('Invalid profile score')
        np.save(str(args.output/'scores.npy'),scores,allow_pickle=False)
        np.savetxt(str(args.output/'timings.csv'),timings,delimiter=',',
            header='window,input_seconds,run_call_seconds,output_get_seconds,runtime_run_microseconds',comments='')
        t = np.asarray(timings)
        report.update(status='completed',active_calls=len(timings),
            runtime_reported_run_total_seconds=float(t[:,4].sum()/1e6),
            runtime_reported_run_median_microseconds=float(np.median(t[:,4])),
            input_total_seconds=float(t[:,1].sum()),run_call_total_seconds=float(t[:,2].sum()),
            output_get_total_seconds=float(t[:,3].sum()))
    except Exception as error:
        report.update(status='failed',error=str(error))
        raise
    finally:
        lib.rknn_destroy(context)
        d.write(args.output/'profile.json',report)
    print(report)


if __name__=='__main__':
    main()
