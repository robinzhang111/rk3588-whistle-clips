"""Local delivery entry for the existing controlled ten-second playback workflow."""
import argparse
from contextlib import closing, redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
import traceback
import urllib.request

from controlled_playback import ROOT, validate_observation
sys.path.insert(0, str(ROOT/'阶段1_离线手动触发'))
from basketclip.media import extract_clip, inspect_video, require_tool
from basketclip.relative import sha
from basketclip.storage import load_json, save_json
from basketclip.timing import make_alignment
from basketclip.mapped import validate_mapping
import eventlink

BOARD = '/home/basketclip-project/stage2'
RELEASE = BOARD+'/runtime-software-labels-20261015-01'
PYTHON = BOARD+'/deploy-20261013-01/venv-npu-run/bin/python'
MODEL = BOARD+'/deploy-20261013-01/conversion-onnx-fp16/WhistleNetMk16-fp16.rknn'
FILES = ('status.json', 'events.jsonl', 'event-metadata.jsonl',
         'candidates.relative.jsonl', 'producer.log', 'sampling-diagnostics.jsonl')


def jsonl(path):
    with Path(path).open(encoding='utf-8-sig') as source:
        return [json.loads(line) for line in source if line.strip()]


def utc(milliseconds):
    return datetime.fromtimestamp(milliseconds/1000, timezone.utc).isoformat().replace('+00:00', 'Z')


def playback_mapping(records, session, video, preview, observations, historical=False):
    """Fail closed; limits are continuity gates, not an accuracy measurement."""
    rows = [record['observation'] for record in records]
    if not historical and any(record.get('session_id') != session or
            row.get('sequence') != i for i, (record, row) in enumerate(zip(records, rows), 1)):
        raise ValueError('Session mismatch or missing/duplicate/out-of-order observation sequence')
    if not rows or rows[0]['kind'] != 'begin' or sum(r['kind']=='begin' for r in rows) != 1:
        raise ValueError('Exactly one recorded begin required')
    for record, row in zip(records, rows):
        validate_observation(row)
        if record.get('time_quality') != 'software_playback_observation':
            raise ValueError('Playback evidence quality missing')
    starts = [i for i, row in enumerate(rows) if row['kind'] == 'playing']
    if len(starts) != 1:
        raise ValueError('Exactly one playing interval required')
    index = starts[0]
    if any(r['kind'] not in ('begin', 'seeking', 'seeked', 'waiting') for r in rows[:index]):
        raise ValueError('Invalid playback startup')
    active = rows[index:]
    if (len(active) < 3 or active[-1]['kind'] not in ('user_stop', 'limit_stop', 'ended')
            or any(r['kind'] != 'sample' for r in active[1:-1])):
        raise ValueError('Missing stop, pause, seek, stall, rate change or incomplete observations')
    if active[0]['media_seconds'] != 0:
        raise ValueError('Observed playback must begin at media zero; no inferred origin')
    gap = divergence = 0.
    first, last = active[0], active[-1]
    for a, b in zip(active, active[1:]):
        wall = (b['wall_ms']-a['wall_ms'])/1000
        performance = (b['performance_ms']-a['performance_ms'])/1000
        media = b['media_seconds']-a['media_seconds']
        if not 0 < wall <= .35 or not 0 < performance <= .35 or media <= 0:
            raise ValueError('Clock/order anomaly, frozen media or missing observations')
        if b is not last and (b['paused'] or b['ready_state'] < 3):
            raise ValueError('Playback not continuously ready')
        gap = max(gap, performance)
        divergence = max(divergence, abs((b['wall_ms']-first['wall_ms'])-
                                       (b['performance_ms']-first['performance_ms']))/1000)
    if first['paused'] or first['ready_state'] < 3 or divergence > .05:
        raise ValueError('Player state or wall/performance clock anomaly')
    alignment = make_alignment(session, utc(first['wall_ms']), 0,
                               utc(last['wall_ms']), last['media_seconds'])
    scale = alignment['video_seconds_per_event_second']
    residual = max(abs(r['media_seconds']-(r['wall_ms']-first['wall_ms'])/1000*scale) for r in active)
    if not .95 <= scale <= 1.05 or residual > .15:
        raise ValueError('Playback progression inconsistent with unit rate')
    info, duration = inspect_video(video)
    stream = next(s for s in info['streams'] if s['codec_type']=='video')
    mapping = dict(schema='bounded-video-utc-v1', kind='software_playback_observation',
        evidence='Recorded playing/stop and continuous samples; total clock/audio error unknown',
        session_id=session, source_video_sha256=sha(video), video_duration=duration,
        video_first_pts=float(stream['start_time']), format_start_pts=float(info['format']['start_time']),
        event_time_quality='software_estimate', real_capture_utc_verified=False,
        total_uncertainty_seconds=None, observation_source_sha256=sha(observations),
        playback_preview_sha256=sha(preview), intervals=[dict(
            utc_start=utc(first['wall_ms']), utc_end=utc(last['wall_ms']),
            video_start=0, video_end=last['media_seconds'], alignment=alignment)])
    validate_mapping(mapping)
    return mapping, dict(records=len(rows), max_observation_gap_seconds=gap,
        max_wall_performance_divergence_seconds=divergence,
        max_internal_residual_seconds=residual, total_uncertainty_seconds=None)


def prepare(directory, session, bind, video):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', session):
        raise ValueError('Session must use 1..80 ASCII letters, digits, underscores or hyphens')
    ipaddress.IPv4Address(bind)
    if directory.name != session:
        raise ValueError('Directory name must equal session ID')
    for stage in ('阶段1_离线手动触发', '阶段2_离线哨声检测', '阶段6_项目展示'):
        if (ROOT/stage/'outputs'/session).exists():
            raise ValueError('Session already exists; choose a new session ID')
    info, duration = inspect_video(video)
    stream = next(s for s in info['streams'] if s['codec_type']=='video')
    if float(stream['start_time']) != 0 or float(info['format']['start_time']) != 0:
        raise ValueError('This controlled preview entry requires zero video/format start PTS')
    digest = sha(video)
    directory.mkdir(parents=True, exist_ok=False)
    with (directory/'prepare.log').open('x', encoding='utf-8') as log:
        try:
            extract_clip(video, directory/'preview.mp4', 0, min(10, duration))
            subprocess.run([require_tool('ffmpeg'), '-v', 'error', '-xerror', '-i',
                            str(directory/'preview.mp4'), '-f', 'null', '-'], check=True, capture_output=True)
        except BaseException as error:
            traceback.print_exc(file=log)
            if isinstance(error, subprocess.CalledProcessError):
                log.write(str(error.stderr or ''))
            raise
    if sha(video) != digest:
        raise ValueError('Source changed during preparation')
    save_json(directory/'session.json', dict(session=session, bind=bind, video=str(video.absolute()),
        video_sha256=digest, preview_sha256=sha(directory/'preview.mp4'),
        board_output=BOARD+'/outputs/'+session, fixed='16x/FP16/0.65/v1', total_uncertainty_seconds=None))
    command = [PYTHON, '-B', RELEASE+'/阶段2_离线哨声检测/tools/start_software_time.py',
        '--model', MODEL, '--output', BOARD+'/outputs/'+session, '--session', session,
        '--header', RELEASE+'/abi-source/upstream-pcm-1.2.8.h',
        '--relation', RELEASE+'/native-relation.json', '--url', 'http://'+bind+':8765/events',
        '--allow-software-time']
    shell = ('#!/bin/bash\nset -euo pipefail\n'
        'if (( $# )); then\n'
        '  [[ $# == 1 && "$1" == --check-only ]] || { echo "Only --check-only supported"; exit 1; }\n'
        '  exec '+shlex.join(command)+' --check-only\nfi\noutput='+shlex.quote(BOARD+'/outputs/'+session)+'\n'
        'test ! -e "$output" || { echo "Session exists; refusing overwrite"; exit 1; }\n'
        'console="$output.console.log"\n(set -o noclobber; : > "$console")\n'
        +shlex.join(command)+' 2>&1 | tee -a "$console"\n')
    with (directory/'run-board.sh').open('x', encoding='utf-8', newline='\n') as target:
        target.write(shell)


def config(directory):
    value = load_json(directory/'session.json')
    if directory.name != value['session']:
        raise ValueError('Session directory mismatch')
    return value


def pc_events(directory, session, database=None):
    path = database or directory/'pc'/'labels.sqlite3'
    with closing(sqlite3.connect(path.absolute().as_uri()+'?mode=ro', uri=True)) as db:
        events = [json.loads(r[0]) for r in db.execute('SELECT payload FROM events ORDER BY id')]
    for row in events:
        eventlink.validate(row, session)
    return events


def export(directory, session, database=None):
    events = pc_events(directory, session, database)
    with (directory/'received-events.jsonl').open('x', encoding='utf-8') as target:
        for row in events:
            target.write(json.dumps(row, ensure_ascii=False)+'\n')
    return events


def download(directory, cfg, host, identity):
    # This command is explicit; preparation/start/status never connect to the board.
    if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+', host):
        raise ValueError('Explicit user@host required')
    target = directory/'board-originals'
    target.mkdir(exist_ok=False)
    options = ['-i', str(identity), '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=8']
    remote = cfg['board_output']
    command = 'cd '+shlex.quote(remote)+' && sha256sum '+' '.join(FILES)+' '+shlex.quote(remote+'.console.log')
    before = subprocess.run(['ssh', *options, host, command], check=True, capture_output=True, text=True).stdout
    # Separate result download; never uploads source, deletes or moves board files.
    subprocess.run(['scp', *options, *[host+':'+remote+'/'+name for name in FILES], str(target)], check=True)
    subprocess.run(['scp', *options, host+':'+remote+'.console.log', str(target/'console.log')], check=True)
    after = subprocess.run(['ssh', *options, host, command], check=True, capture_output=True, text=True).stdout
    expected = {('console.log' if line.split()[1]==remote+'.console.log' else line.split()[1]):
                line.split()[0] for line in before.splitlines()}
    matched = (before == after and set(expected) == set(FILES+('console.log',))
               and all(sha(target/n)==digest for n, digest in expected.items()))
    save_json(directory/'download-verification.json', dict(matched=matched, remote_sha256=expected,
        remote_unchanged_during_download=before==after))
    if not matched:
        raise ValueError('Download hash verification failed; preserve partial files')
    state = load_json(target/'status.json')
    if state.get('session_id') != cfg['session'] or state.get('status') != 'closed':
        raise ValueError('Downloaded session is mismatched or not closed; preserve diagnostics')


def status(directory, cfg, database=None):
    board = directory/'board-originals'
    state = load_json(board/'status.json') if (board/'status.json').exists() else {}
    if state and state.get('session_id') != cfg['session']:
        raise ValueError('Board session mismatch')
    events = jsonl(board/'events.jsonl') if (board/'events.jsonl').exists() else []
    for row in events:
        eventlink.validate(row, cfg['session'])
    database = database or directory/'pc'/'labels.sqlite3'
    received = pc_events(directory, cfg['session'], database) if database.exists() else []
    labels = state.get('automatic_labels', {})
    manifest = directory/'clips'/'manifest.json'
    jobs = load_json(manifest)['results'] if manifest.exists() else []
    completed = [r for r in jobs if r['status']=='completed' and (directory/'clips'/r['file']).is_file()]
    verification = directory/'clip-verification.json'
    decoded = load_json(verification)['results'] if verification.exists() else []
    verified = sum(any(v['file']==r['file'] and v['full_decode']=='passed'
                       and v['sha256']==sha(directory/'clips'/r['file']) for v in decoded) for r in completed)
    review_path = directory/'human-review.json'
    review = load_json(review_path) if review_path.exists() else None
    return dict(session=cfg['session'], capture_status=state.get('status', 'unknown'),
        receiver_database=str(database),
        resources_released=state.get('resources_released'), saved_labels=len(events),
        labels_submitted=labels.get('submitted'), pending_event_ids=labels.get('pending_event_ids'),
        pc_committed_labels=len(received), four_fields_equal=(bool(events) and sorted(events, key=lambda r:r['event_id'])==received),
        original_process_all_acknowledged=labels.get('all_acknowledged'),
        # Empty queues and empty sessions never imply successful delivery.
        pc_confirmed=bool(events) and sorted(events, key=lambda r:r['event_id'])==received,
        clips_generated=len(completed), clips_full_decode_verified=verified,
        clips_rejected_or_failed=len(jobs)-len(completed),
        human_confirmation=review if review else 'pending',
        total_uncertainty_seconds=None, full_product_accepted=False)


class Tee:
    def __init__(self, console, log):
        self.console, self.log = console, log

    def write(self, text):
        self.console.write(text)
        self.log.write(text)
        self.flush()

    def flush(self):
        self.console.flush()
        self.log.flush()


def service_request(directory, cfg, stop=False):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8766/session-status', timeout=3) as response:
        live = json.load(response)
    if live['session'] != cfg['session'] or live['output'] != str((directory/'pc').absolute()):
        raise ValueError('Another PC session is running; refusing control')
    if live['stopping']:
        raise ValueError('PC service is stopping; do not start a capture')
    if stop:
        with opener.open(urllib.request.Request('http://127.0.0.1:8766/finish-session', b''), timeout=3):
            pass
    live['pc_committed_labels'] = len(pc_events(directory, cfg['session']))
    return live


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'start', 'live-status', 'stop', 'download',
                                           'export', 'finish', 'status', 'review'))
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--session')
    parser.add_argument('--bind')
    parser.add_argument('--video', type=Path)
    parser.add_argument('--host')
    parser.add_argument('--identity', type=Path)
    parser.add_argument('--database', type=Path, help='Explicit recovery database for export/status/finish')
    parser.add_argument('--decision', choices=('accepted', 'rejected'))
    parser.add_argument('--note')
    args = parser.parse_args(argv)
    directory = args.directory.absolute()
    if args.command == 'prepare':
        if not all((args.session, args.bind, args.video)):
            parser.error('prepare requires --session --bind --video')
        prepare(directory, args.session, args.bind, args.video)
        return 0
    cfg = config(directory)
    command = args.command
    if command == 'start':
        if sha(Path(cfg['video'])) != cfg['video_sha256'] or sha(directory/'preview.mp4') != cfg['preview_sha256']:
            raise ValueError('Prepared media hash mismatch')
        from controlled_playback import main as run
        with (directory/'pc-console.log').open('x', encoding='utf-8') as log:
            with redirect_stdout(Tee(sys.stdout, log)), redirect_stderr(Tee(sys.stderr, log)):
                try:
                    run(['--bind', cfg['bind'], '--session', cfg['session'], '--preview',
                         str(directory/'preview.mp4'), '--output', str(directory/'pc'), '--open-browser'])
                except BaseException:
                    traceback.print_exc()
                    raise
    elif command in ('live-status', 'stop'):
        print(json.dumps(service_request(directory, cfg, command=='stop'), ensure_ascii=False, indent=2))
    elif command == 'download':
        if not args.host or not args.identity:
            parser.error('download requires --host and --identity')
        with (directory/'download.log').open('x', encoding='utf-8') as log:
            try:
                download(directory, cfg, args.host, args.identity)
            except BaseException as error:
                traceback.print_exc(file=log)
                if isinstance(error, subprocess.CalledProcessError):
                    log.write(str(error.stdout or '')+'\n'+str(error.stderr or ''))
                raise
    elif command == 'export':
        export(directory, cfg['session'], args.database)
    elif command == 'finish':
        if not (directory/'pc'/'service-closed.json').exists():
            raise ValueError('Stop PC service normally before finalizing; preserve live evidence')
        # Reserve an attempt before writing outputs; failures retain traceback and inputs.
        with (directory/'finish.log').open('x', encoding='utf-8') as log:
            try:
                verification = load_json(directory/'download-verification.json')
                expected = verification['remote_sha256']
                if (not verification['matched'] or not set(FILES).issubset(expected)
                        or any(sha(directory/'board-originals'/name) != expected[name] for name in FILES)):
                    raise ValueError('Downloaded originals missing, changed or not hash verified')
                events = pc_events(directory, cfg['session'], args.database)
                if (directory/'received-events.jsonl').exists():
                    if jsonl(directory/'received-events.jsonl') != events:
                        raise ValueError('Export differs from current database; preserve both snapshots')
                else:
                    export(directory, cfg['session'], args.database)
                summary = status(directory, cfg, args.database)
                if not events or not summary['pc_confirmed']:
                    raise ValueError('No candidates or PC/board labels differ; mapping blocked')
                observations = directory/'pc'/'playback-observations.jsonl'
                video = Path(cfg['video'])
                if sha(video) != cfg['video_sha256'] or sha(directory/'preview.mp4') != cfg['preview_sha256']:
                    raise ValueError('Prepared media hash mismatch')
                mapping, check = playback_mapping(jsonl(observations), cfg['session'], video,
                                                  directory/'preview.mp4', observations)
                save_json(directory/'playback-check.json', check)
                save_json(directory/'playback-mapping.json', mapping)
                result = subprocess.run([sys.executable, '-B', '-m', 'basketclip.mapped',
                    '--video', str(video), '--events', str(directory/'received-events.jsonl'),
                    '--mapping', str(directory/'playback-mapping.json'), '--output', str(directory/'clips'),
                    '--software-playback'], cwd=ROOT/'阶段1_离线手动触发',
                    capture_output=True, text=True, encoding='utf-8', errors='replace')
                log.write(result.stdout+result.stderr)
                result.check_returncode()
                jobs = load_json(directory/'clips'/'manifest.json')['results']
                verified = []
                for job in jobs:
                    if job['status']=='completed':
                        subprocess.run([require_tool('ffmpeg'), '-v', 'error', '-xerror', '-i',
                            str(directory/'clips'/job['file']), '-f', 'null', '-'],
                            check=True, stdout=log, stderr=log)
                        verified.append(dict(file=job['file'], sha256=sha(directory/'clips'/job['file']),
                                             full_decode='passed'))
                save_json(directory/'clip-verification.json', dict(results=verified))
                save_json(directory/'summary.json', status(directory, cfg, args.database))
                if len(verified) != len(events):
                    raise ValueError('Some clips rejected or failed; inspect manifest and summary')
            except BaseException:
                traceback.print_exc(file=log)
                save_json(directory/'failure.json', dict(error=traceback.format_exc(),
                    full_product_accepted=False))
                raise
    elif command == 'review':
        summary = status(directory, cfg)
        if not args.decision or not args.note or not args.note.strip():
            parser.error('review requires --decision and user-provided --note')
        if (not summary['clips_generated'] or summary['clips_rejected_or_failed']
                or summary['clips_full_decode_verified'] != summary['clips_generated']):
            raise ValueError('Review requires all requested clips to be generated and decode/hash verified')
        save_json(directory/'human-review.json', dict(session=cfg['session'],
            decision=args.decision, user_statement=args.note,
            recorded_utc=datetime.now(timezone.utc).isoformat(),
            scope='user review of clip content and position; no quality benchmark'))
    elif command == 'status':
        print(json.dumps(status(directory, cfg, args.database), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
