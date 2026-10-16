import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import workbench as web


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/'source'
        (self.source/'capture').mkdir(parents=True)
        self.video=self.root/'video.mp4'
        self.video.write_bytes(b'fixture video, not decoded')
        self.row=dict(session_id='fixture',candidate=dict(segment_id='fixture:segment-1',
            estimated_start_sample=22050,closed_by='normal_stop'),timing=dict(status='relative_only',estimated_utc=None))
        (self.source/'capture'/'candidates.relative.jsonl').write_text(json.dumps(self.row)+'\n',encoding='utf-8')
        web.save_new(self.source/'status.json',dict(status='failed',session_id='fixture',stop_reason='external_stop'))
        web.save_new(self.source/'capture'/'status.json',dict(status='failed',session_id='fixture',
            error='sample_clock_discontinuity',resources_released=False,elapsed_seconds=95,
            time_basis='relative samples; software time invalid',candidates_saved=1))
        self.app=web.Workbench(self.root/'workbench',root=self.root)
        self.addCleanup(self.app.close)
        self.info=dict(format=dict(start_time='0'),streams=[dict(codec_type='video',start_time='0')])
        self.job=dict(id='test-job',status='running',message='')

    def imported(self):
        with patch.object(web,'inspect_video',return_value=(self.info,10)):
            return self.app.import_result(dict(session_dir=str(self.source),video=str(self.video)),self.job)

    def planned(self):
        entry=self.imported()
        self.app.plan(entry,dict(confirmed=True,evidence='explicit fixture start only',offset=1,
            segment_id='fixture:segment-1'),self.job)
        return entry

    def clipped(self):
        entry=self.planned()
        def extract(video,output,start,end):
            output.write_bytes(b'fixture clip')
        with patch.object(web,'extract_clip',side_effect=extract), \
                patch.object(web,'inspect_video',return_value=(self.info,5)), \
                patch.object(web.subprocess,'run'),patch.object(web,'require_tool',return_value='fixture-ffmpeg'):
            self.app.extract(entry,dict(numbers=[1]),self.job)
        return entry

    def test_import_preserves_failed_state_and_original_files(self):
        protected={str(p):web.sha(p) for p in self.source.rglob('*') if p.is_file()}
        entry=self.imported()
        self.assertEqual(entry['view']['state'],'failed')
        self.assertFalse(entry['view']['resources_released'])
        self.assertEqual(entry['view']['ten_minute_acceptance'],'待实机验收')
        self.assertIsNone(entry['alignment'])
        self.assertTrue(all(web.sha(Path(p))==digest for p,digest in protected.items()))

    def test_source_session_conflict_rejected(self):
        path=self.source/'capture'/'candidates.relative.jsonl'
        row=dict(self.row,session_id='other')
        path.write_text(json.dumps(row)+'\n',encoding='utf-8')
        with self.assertRaises(ValueError):
            self.imported()

    def test_read_paths_are_scoped_and_hidden_paths_rejected(self):
        with self.assertRaises((ValueError,FileNotFoundError)):
            self.app.path(str(self.root.parent))
        hidden=self.root/'.git'
        hidden.mkdir()
        with self.assertRaises(ValueError):
            self.app.path(str(hidden))

    def test_missing_evidence_invalid_offset_or_segment_blocks_plan(self):
        entry=self.imported()
        original=dict(confirmed=True,evidence='test',offset=1,segment_id='fixture:segment-1')
        for change in (dict(confirmed=False),dict(evidence=' '),dict(offset=-1),
                       dict(offset=float('nan')),dict(offset=True),dict(segment_id='other')):
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.app.plan(entry,dict(original,**change),self.job)
        self.assertIsNone(entry['alignment'])
        with self.assertRaises(ValueError):
            self.app.extract(entry,dict(numbers=[1]),self.job)

    def test_relative_sample_plan_never_uses_candidate_utc(self):
        entry=self.planned()
        self.assertEqual(entry['results'][0]['video_seconds'],2)
        self.assertFalse(entry['results'][0]['absolute_utc_used'])
        self.assertFalse(entry['alignment']['synchronization_verified'])
        self.assertIsNone(entry['alignment']['total_alignment_error_seconds'])

    def test_known_input_interruption_rejects_candidate(self):
        path=self.source/'capture'/'candidates.relative.jsonl'
        row=copy.deepcopy(self.row)
        row['candidate']['closed_by']='source_or_processing_error'
        path.write_text(json.dumps(row)+'\n',encoding='utf-8')
        entry=self.planned()
        self.assertEqual(entry['results'][0]['status'],'rejected')
        with self.assertRaises(ValueError):
            self.app.extract(entry,dict(numbers=[1]),self.job)

    def test_changed_video_blocks_extraction(self):
        entry=self.planned()
        self.video.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.app.extract(entry,dict(numbers=[1]),self.job)

    def test_extraction_calls_existing_functions_and_preserves_source(self):
        digest=web.sha(self.video)
        entry=self.clipped()
        result=entry['results'][0]
        self.assertTrue(result['full_decode_verified'])
        self.assertEqual(result['status'],'completed')
        self.assertEqual(web.sha(self.video),digest)
        self.assertEqual(list(Path(entry['folder']).glob('review-*.json')),[])

    def test_feedback_versions_and_replanning_do_not_overwrite(self):
        entry=self.clipped()
        review=dict(candidate_number=1,whistle='yes',actual_clip_seconds=2,
            position_confirmed=True,same_whistle_as=None,note='fixture user feedback')
        self.app.review(entry,review,self.job)
        first=list(Path(entry['folder']).glob('review-*.json'))[0]
        digest=web.sha(first)
        self.app.review(entry,dict(review,whistle='uncertain',position_confirmed=False),self.job)
        self.assertEqual(len(list(Path(entry['folder']).glob('review-*.json'))),2)
        self.assertEqual(web.sha(first),digest)
        self.app.plan(entry,dict(confirmed=True,evidence='different fixture start',offset=2,
            segment_id='fixture:segment-1'),self.job)
        self.assertEqual(entry['reviews'],{})
        self.assertIsNone(entry['results'][0]['clip'])
        self.assertTrue(first.exists())

    def test_position_and_duplicate_feedback_validation(self):
        entry=self.clipped()
        original=dict(candidate_number=1,whistle='yes',actual_clip_seconds=2,
            position_confirmed=True,same_whistle_as=None)
        for change in (dict(actual_clip_seconds=None),dict(actual_clip_seconds=99),
                       dict(same_whistle_as=1),dict(candidate_number=True),dict(whistle='automatic')):
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.app.review(entry,dict(original,**change),self.job)

    def test_empty_ack_and_simulated_duration_never_pass_hardware_acceptance(self):
        view=web.capture_view({},dict(status='closed',automatic_labels=dict(saved=0,all_acknowledged=True)))
        self.assertIsNone(view['label_ack'])
        for scenario in ('waiting','running','closed','failed','relative'):
            entry=self.app.simulate(dict(scenario=scenario))
            self.assertTrue(entry['simulated'])
            self.assertFalse(entry['view']['full_product_accepted'])
            self.assertEqual(entry['view']['ten_minute_acceptance'],'待实机验收')
            if scenario=='relative':
                self.assertTrue(entry['view']['relative_only'])

    def test_worker_failure_keeps_diagnostic_and_state(self):
        job_id=self.app.submit('plan',dict(entry_id='missing'))
        self.app.pool.shutdown(wait=True)
        state=self.app.snapshot()
        self.assertEqual(state['jobs'][-1]['status'],'failed')
        self.assertTrue((self.app.output/(job_id+'.log')).exists())
        self.assertTrue((self.app.output/'state.json').exists())

    def test_resume_preserves_feedback_and_marks_unfinished_work(self):
        entry=self.clipped()
        self.app.review(entry,dict(candidate_number=1,whistle='no',position_confirmed=False),self.job)
        self.app.state['jobs'].append(dict(id='unfinished-fixture',status='running'))
        self.app.persist()
        second=web.Workbench(self.app.output,root=self.root,resume=True)
        try:
            self.assertEqual(second.entry(entry['id'])['reviews']['1']['whistle'],'no')
            self.assertEqual(second.state['jobs'][0]['status'],'failed')
        finally:
            second.close()
        with self.assertRaises(FileExistsError):
            web.Workbench(self.app.output,root=self.root)

    def test_http_range_and_cross_origin_or_path_access_rejected(self):
        entry=self.clipped()
        server=web.make_server(('127.0.0.1',0),self.app)
        thread=threading.Thread(target=server.serve_forever)
        thread.start()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base='http://127.0.0.1:%d'%server.server_port
        try:
            with opener.open(base+'/api/state') as response:
                self.assertFalse(json.load(response)['hardware_connected'])
            url=base+'/media?entry='+entry['id']+'&number=1'
            with opener.open(urllib.request.Request(url,headers={'Range':'bytes=0-3'})) as response:
                self.assertEqual(response.status,206)
                self.assertEqual(response.read(),b'fixt')
            for range_header in ('bytes=99999-','bytes=0-1,3-4'):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    opener.open(urllib.request.Request(url,headers={'Range':range_header}))
                self.assertEqual(error.exception.code,416)
            request=urllib.request.Request(base+'/api/simulate',b'{"scenario":"waiting"}',
                {'Content-Type':'application/json','Origin':'http://untrusted.example'})
            with self.assertRaises(urllib.error.HTTPError) as error:
                opener.open(request)
            self.assertEqual(error.exception.code,403)
            for path in ('/../../video.mp4','/source/status.json','/media?entry='+entry['id']+'&number=0'):
                with self.assertRaises(urllib.error.HTTPError):
                    opener.open(base+path)
            request=urllib.request.Request(base+'/api/close',b'{}',
                {'Content-Type':'application/json','Origin':base})
            with opener.open(request) as response:
                self.assertTrue(json.load(response)['stopping'])
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_occupied_port_never_loads_or_rewrites_workspace(self):
        with patch.object(web,'make_server',side_effect=OSError('port occupied')), \
                patch.object(web,'Workbench') as constructor:
            with self.assertRaises(OSError):
                web.main(['--output',str(self.app.output),'--resume'])
            constructor.assert_not_called()


if __name__=='__main__':
    unittest.main()
