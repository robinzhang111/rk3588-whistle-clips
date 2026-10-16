"""Prepare/verify native float inputs and relative sample mapping, never load H5."""
import json
from pathlib import Path
import subprocess
import time
import numpy as np
from basketball_audio_adapter import FILTER,resample_array,target_boundary,source_boundary
from continuous_whistle import read_float_wav,starts_for_samples,features_for_starts
from evaluate_testbench_events import sha,read,write

STAGE=Path(__file__).absolute().parent.parent


def main():
    out=STAGE/'outputs/basketball-input-adaptation-20261012-01'
    destination=STAGE/'data/basketball-input-22050-20261012-01'
    out.mkdir(exist_ok=False);destination.mkdir(exist_ok=False)
    ffmpeg=STAGE.parent/'tools/ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe'
    material=read(STAGE/'outputs/basketball-manual-20261012-01/material.json')
    truth_path=STAGE/'outputs/basketball-manual-review-20261012-02/manual_annotations_coverage_corrected.json'
    confirmation_path=truth_path.parent/'user-final-confirmation.json'
    truth=read(truth_path);confirmation=read(confirmation_path)
    assert sha(truth_path)==confirmation['annotation_sha256']
    assert len(truth['events'])==confirmation['confirmed_event_count']==7
    protected=read(STAGE/'outputs/basketball-manual-20261012-01/verification.json')['protected_sha256']
    protected.update({str(truth_path):sha(truth_path),str(confirmation_path):sha(confirmation_path)})
    for f in material['files']:protected[f['audio_path']]=f['audio_sha256']
    assert all(sha(Path(p))==h for p,h in protected.items())
    tools=[Path(__file__),STAGE/'tools/basketball_audio_adapter.py',STAGE/'tools/continuous_whistle.py']
    write(out/'plan-before-adaptation.json',dict(filter=FILTER,source_sha256=protected,
        tool_sha256={str(p):sha(p) for p in tools},ffmpeg_sha256=sha(ffmpeg),
        official_options='https://ffmpeg.org/ffmpeg-resampler.html',input='original native float WAV, not attenuated playback WAV',
        output='float32 22050Hz original 1/2 channels; no gain/mixing/clipping or model calls',
        mapping='output index j -> original grid 2*j; annotation boundary nearest half up, EOF clamp',
        timestamp_scope='decoded-audio relative sample grid only; filter spreads transient energy; no exact UTC/onset claim'))
    files=[];mapped=[]
    for f in material['files']:
        stamp=time.perf_counter()
        audio,rate=read_float_wav(f['audio_path'])
        assert rate==44100 and audio.shape==(f['frames'],f['channels'])
        target=destination/(f['id']+'-22050-float.wav')
        command=[str(ffmpeg),'-nostdin','-v','warning','-xerror','-n','-i',f['audio_path'],'-map','0:a:0','-af',FILTER,'-c:a','pcm_f32le',str(target)]
        completed=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        (out/(f['id']+'-resample.log')).write_bytes(completed.stderr)
        assert completed.returncode==0
        y,output_rate=read_float_wav(target)
        assert output_rate==22050 and y.shape[1]==audio.shape[1]
        assert abs(2*len(y)-len(audio))<=1
        np.testing.assert_array_equal(y,resample_array(audio,ffmpeg))
        if y.shape[1]==2:
            for c in range(2):np.testing.assert_allclose(y[:,c],resample_array(audio[:,c:c+1],ffmpeg)[:,0],atol=1e-6,rtol=1e-6)
        # Linear-gain check: same fixed resampler at 0.5 amplitude should yield half output.
        np.testing.assert_allclose(resample_array(audio*.5,ffmpeg),y*.5,rtol=1e-6,atol=1e-7)
        starts=starts_for_samples(len(y));zeros=0;silent_count=0
        for offset in range(0,len(starts),128):
            features,silent,zero=features_for_starts(y,starts[offset:offset+128])
            assert features.shape[1:]==(f['channels'],513,1) and np.isfinite(features).all()
            zeros+=zero;silent_count+=int(silent.sum())
        for e in truth['events']:
            if e['file_id']!=f['id']:continue
            a,b=target_boundary(e['start_sample'],len(y)),target_boundary(e['end_sample'],len(y))
            assert a<b and abs(source_boundary(a,len(audio))-e['start_sample'])<=1 and abs(source_boundary(b,len(audio))-e['end_sample'])<=1
            mapped.append(dict(id=e['id'],file_id=f['id'],original_start_sample=e['start_sample'],original_end_sample=e['end_sample'],
                adapted_start_sample=a,adapted_end_sample=b,original_rate=44100,adapted_rate=22050,
                start_grid_error_seconds=a/22050-e['start_sample']/44100,
                end_grid_error_seconds=b/22050-e['end_sample']/44100))
        files.append(dict(id=f['id'],input_path=f['audio_path'],input_sha256=f['audio_sha256'],output_path=str(target),output_sha256=sha(target),
            command=command,exit_code=completed.returncode,input_frames=len(audio),output_frames=len(y),channels=y.shape[1],
            original_rate=44100,adapted_rate=22050,duration_difference_seconds=len(y)/22050-len(audio)/44100,
            input_peak_abs=np.max(np.abs(audio),axis=0).tolist(),output_peak_abs=np.max(np.abs(y),axis=0).tolist(),
            input_rms=np.sqrt(np.mean(audio.astype(float)**2,axis=0)).tolist(),output_rms=np.sqrt(np.mean(y.astype(float)**2,axis=0)).tolist(),
            outside_unit_range_output_samples=int(np.sum(np.abs(y)>1)),complete_windows=len(starts),
            uncomputed_tail_samples=len(y)-(int(starts[-1])+1024),digital_silent_channel_windows=silent_count,
            exact_zero_fft_bins_replaced=zeros,feature_shape_contract=[None,y.shape[1],513,1],
            adapter_check_seconds=time.perf_counter()-stamp))
    assert all(sha(Path(p))==h for p,h in protected.items())
    write(out/'manifest.json',dict(files=files,threshold_unchanged=.65,model_loaded=False,
        sampling_grid_mapping='2*j, EOF exclusive boundary clamped to input frame count',
        feature_contract='1024 np.hamming, hop512, first513 FFT bins,20log10 magnitude, float32; independently per actual channel; no channel duplication',
        scoring_split_status='unassigned, must fix intended diagnostic/evaluation use before model scores'))
    write(out/'annotation-grid-map.json',dict(source_annotation_sha256=sha(truth_path),intervals=mapped,
        scope='derived view only; seven original annotations not edited; grid rounding not human boundary accuracy'))
    write(out/'verification.json',dict(protected_sha256=protected,all_sources_truth_model_prior_evidence_unchanged=True,
        all_outputs_float_finite_22050_native_channels=True,file_vs_raw_pipeline_array_equal=True,
        real_stereo_matches_separate_mono_resampling=True,real_half_gain_linearity_checked=True,
        all_windows_features_finite_correct_shapes=True,seven_annotation_boundaries_roundtrip_within_one_source_sample=True,
        no_model_import_or_inference=True,no_new_dependency_installed=True,
        limitations=['not verified actual whistle preservation by new listening','not exact UTC or physical onset mapping','source normalized-training amplitude contract unknown','unassigned evaluation groups']))
    print(json.dumps(files,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
