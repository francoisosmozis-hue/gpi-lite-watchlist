"""Seven fictional-day GPI workflow, isolated, resumable, auditably fail-closed.

Only development. CLI executes authentic existing engines from an authenticated
clean git checkout; unit tests may inject explicitly FAKE executors and do NOT
constitute engine qualification. No GCP, real odds, bets or scheduler.
"""
from __future__ import annotations
import argparse
from datetime import date, timedelta
from hashlib import sha256, sha1
from html import escape
import json
import os
from pathlib import Path
import subprocess
import sys
import stat
from typing import Callable

from tools.synthetic_daily_pipeline import PROOF_CLASS, SOURCE_COMMIT_BASE, FIXTURE_VERSION, verify_bundle

VERSION = 'SYNTHETIC_WEEK_V1'
START_DATE = date(2030, 1, 2)
DAYS = tuple((START_DATE + timedelta(days=i)).isoformat() for i in range(7))
REQUIRED_ARTIFACTS = frozenset({'program.json', 'snapshots.json', 'watchlist.json',
    'movements.json', 'decisions.json', 'fictional_results.json',
    'tracking.csv', 'summary.json', 'dashboard.html', 'audit_manifest.json'})
RUN_CHILD = ('from pathlib import Path; import sys; '
             'from tools import synthetic_daily_pipeline as p; '
             'p.DAY=sys.argv[1]; p.run(outdir=Path(sys.argv[2]))')


def canonical(x: object) -> bytes:
    return (json.dumps(x, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def hash_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def output_guard(path: Path) -> None:
    if not path.name.startswith('synthetic_') or '..' in path.parts:
        raise ValueError('SYNTHETIC_NAMESPACE_REQUIRED')
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('SYMLINK_PATH_FORBIDDEN')
    parts = path.parts
    protected = (('data','output'),('data','h5'),('data','morning'),
                 ('data','results'),('sources','calibration'),('project_sources',))
    if any(parts[i:i+len(x)] == x for x in protected
           for i in range(len(parts)-len(x)+1)):
        raise ValueError('OPERATIONAL_PATH_FORBIDDEN')


def _validate_day(folder: Path, logical_date: str) -> dict:
    if folder.is_symlink() or not folder.is_dir() or not verify_bundle(folder):
        raise ValueError('DAY_BUNDLE_INTEGRITY_FAIL')
    if {p.name for p in folder.iterdir()} != REQUIRED_ARTIFACTS:
        raise ValueError('DAY_ARTIFACT_SET_INVALID')
    program = json.loads((folder / 'program.json').read_bytes())
    snapshots = json.loads((folder / 'snapshots.json').read_bytes())
    decisions = json.loads((folder / 'decisions.json').read_bytes())
    summary = json.loads((folder / 'summary.json').read_bytes())
    if (not isinstance(program, list) or len(program) != 2
        or {x['race_uid'] for x in program} != {'SYNTHETIC_R1C1','SYNTHETIC_R1C2'}
        or any(x.get('date') != logical_date or x.get('source_kind') != PROOF_CLASS for x in program)):
        raise ValueError('DAY_PROGRAM_INVALID')
    if (not isinstance(snapshots,list) or len(snapshots) != 6
        or any(not s.get('fixture_timestamp','').startswith(logical_date+'T')
               or s.get('source_kind') != PROOF_CLASS for s in snapshots)):
        raise ValueError('DAY_SNAPSHOT_INVALID')
    if (not isinstance(decisions,list) or len(decisions) != 2
        or {x['race_uid'] for x in decisions} != {'SYNTHETIC_R1C1','SYNTHETIC_R1C2'}
        or any(x.get('source_kind') != PROOF_CLASS or x.get('stake_eur') != 0
          or x.get('ticket_real') != 'NO_BET' or x.get('engine_output',{}).get('verdict') != 'NO_BET'
          or x.get('engine_output',{}).get('ticket_exact') != 'NO_BET'
          or x.get('engine_output',{}).get('strict_allowed_oui_non') != 'non' for x in decisions)):
        raise ValueError('DAY_DECISION_GATE_BREACH')
    if (summary.get('all_no_bet') is not True
        or summary.get('operational_proof') is not False
        or summary.get('prospective_proof') is not False
        or summary.get('daily_pipeline_qualified') is not False
        or summary.get('strict_allowed') is not False
        or summary.get('real_bet_allowed') is not False
        or summary.get('governance_transition') != 'NONE'
        or summary.get('proof_class') != PROOF_CLASS
        or summary.get('race_count') != 2 or summary.get('snapshot_count') != 6):
        raise ValueError('DAY_SUMMARY_GATE_BREACH')
    return {'logical_date':logical_date,'directory':folder.name,
            'manifest_sha256':hash_file(folder / 'audit_manifest.json'),
            'races':2,'decisions':2,'verdict':'NO_BET','stake_eur':0}


def verify_week(root: Path) -> bool:
    try:
        if root.is_symlink() or not root.is_dir() or (root/'days').is_symlink():
            return False
        if any((root/name).is_symlink() for name in ('source_lock.json','week_index.json','dashboard.html')):
            return False
        expected_top={'days','week_index.json','dashboard.html','source_lock.json'}
        if {p.name for p in root.iterdir()} != expected_top:
            return False
        lock=json.loads((root/'source_lock.json').read_bytes())
        if lock != _source_lock(lock.get('source_commit')):
            return False
        records=[_validate_day(root/'days'/('synthetic_'+day.replace('-','')),day) for day in DAYS]
        if {p.name for p in (root/'days').iterdir()} != {r['directory'] for r in records}:
            return False
        idx=json.loads((root/'week_index.json').read_bytes())
        return (idx == _index(records,idx.get('source_commit'))
                and idx.get('source_commit') not in ('',None,'ND')
                and (root/'dashboard.html').read_bytes() == _dashboard(records))
    except (OSError,TypeError,KeyError,ValueError,IndexError,AttributeError):
        return False


def _source_lock(source_commit: str) -> dict:
    return {'version': VERSION, 'proof_class': PROOF_CLASS,
            'source_commit': source_commit, 'real_bet_allowed': False,
            'strict_allowed': False, 'governance_transition': 'NONE'}

def _index(records: list[dict], source_commit: str) -> dict:
    return {'version':VERSION,'proof_class':PROOF_CLASS,'scenario_version':FIXTURE_VERSION,
            'source_base':SOURCE_COMMIT_BASE,'source_commit':source_commit,
            'days':records,'all_no_bet':True,'stakes_eur':0,
            'STRICT_ALLOWED':False,'REAL_BET_ALLOWED':False,
            'END_TO_END_PROSPECTIVE':'NOT_PROVEN','GOVERNANCE_TRANSITION':'NONE',
            'qualified_daily_scheduler':False,'android_remote_delivery':False}


def _dashboard(records: list[dict]) -> bytes:
    rows=''.join('<tr><td>'+escape(x['logical_date'])+'</td><td>2 fictives</td>'
                 '<td>NO_BET</td><td>0 €</td><td><a href="days/'
                 +escape(x['directory'])+'/dashboard.html">Voir</a></td></tr>' for x in records)
    return ('<!doctype html><html lang="fr"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>GPI — 7 journées fictives</title><style>body{font:16px system-ui;'
            'max-width:900px;margin:2em auto;padding:1em}table{border-collapse:collapse;'
            'width:100%}td,th{border:1px solid #aaa;padding:.7em}</style>'
            '<h1>GPI LITE — SEPT JOURNÉES FICTIVES</h1>'
            '<p>Démonstration isolée, sans collecte réelle, sans mise et sans programmation autonome.</p>'
            '<table><tr><th>Date logique</th><th>Courses</th><th>Décisions</th>'
            '<th>Mise</th><th>Ouvrir</th></tr>'+rows+'</table></html>').encode('utf-8')


def run_week(root: Path, *, execute_day: Callable[[str,Path],None],
             source_commit: str, resume: bool=False) -> dict:
    output_guard(root)
    if not isinstance(source_commit,str) or len(source_commit)!=40 or any(c not in '0123456789abcdef' for c in source_commit):
        raise ValueError('SOURCE_COMMIT_SHA_REQUIRED')
    if root.exists():
        if root.is_symlink() or not root.is_dir():
            raise ValueError('WEEK_ROOT_SYMLINK_OR_NOT_DIRECTORY')
        if not resume:
            raise FileExistsError('WEEK_EXISTS_REFUSE_OVERWRITE')
        if verify_week(root):
            old=json.loads((root/'week_index.json').read_bytes())
            if old['source_commit'] != source_commit:
                raise ValueError('SOURCE_COMMIT_MISMATCH')
            return old
        if (root/'week_index.json').exists() or (root/'dashboard.html').exists():
            raise ValueError('PARTIALLY_OR_TAMPERED_FINAL_INDEX')
        lock_file=root/'source_lock.json'
        if lock_file.is_symlink() or not lock_file.is_file():
            raise ValueError('SOURCE_LOCK_MISSING_ON_RESUME')
        try:
            lock=json.loads(lock_file.read_bytes())
        except (OSError, ValueError) as exc:
            raise ValueError('SOURCE_LOCK_CORRUPTED') from exc
        if lock != _source_lock(source_commit):
            raise ValueError('SOURCE_LOCK_MISMATCH')
    else:
        root.mkdir(parents=True,exist_ok=False)
        (root/'source_lock.json').write_bytes(canonical(_source_lock(source_commit)))
    days_dir=root/'days'
    if days_dir.is_symlink():
        raise ValueError('SYMLINK_DAYS_DIRECTORY')
    days_dir.mkdir(exist_ok=True)
    existing_names={p.name for p in days_dir.iterdir()}
    permitted={'synthetic_'+d.replace('-','') for d in DAYS}
    if not existing_names <= permitted:
        raise ValueError('UNEXPECTED_DAY_OR_ABANDONED_STAGING')
    records=[]
    for day in DAYS:
        name='synthetic_'+day.replace('-','')
        final=days_dir/name
        pending=days_dir/('synthetic_'+day.replace('-','')+'_pending')
        if pending.exists() or pending.is_symlink():
            raise ValueError('ABANDONED_STAGING_FAIL_CLOSED')
        if final.exists() or final.is_symlink():
            rec=_validate_day(final,day)
        else:
            execute_day(day,pending)
            rec=_validate_day(pending,day)
            os.replace(pending,final)  # atomic rename on same filesystem
            rec['directory']=final.name
        records.append(rec)
    index=_index(records,source_commit)
    (root/'dashboard.html').write_bytes(_dashboard(records))
    (root/'week_index.json').write_bytes(canonical(index))  # completion marker, last
    if not verify_week(root):
        raise RuntimeError('WEEK_COMPLETION_VERIFICATION_FAIL')
    return index


def git_preflight(checkout: Path,expected_commit: str) -> None:
    """Verify exact tracked tree bytes and file modes; no ignored or untracked extras."""
    def git(*args):
        return subprocess.check_output(['git','-C',str(checkout),*args],stderr=subprocess.PIPE)
    head=git('rev-parse','HEAD').decode().strip()
    if head != expected_commit or git('rev-parse','--show-toplevel').decode().strip() != str(checkout):
        raise ValueError('GIT_IDENTITY_NOT_PROVEN')
    tree=git('ls-tree','-rz','--full-tree','HEAD')
    tracked={}
    for entry in tree.split(b'\0'):
        if not entry:
            continue
        meta, sep, raw_path=entry.partition(b'\t')
        if not sep or not meta.startswith((b'100644 blob ',b'100755 blob ')):
            raise ValueError('NON_REGULAR_TRACKED_SOURCE')
        mode, typ, expected_sha=meta.split()
        path=raw_path.decode('utf-8')
        if path.startswith('/') or '..' in Path(path).parts or path in tracked:
            raise ValueError('INVALID_GIT_TREE_ENTRY')
        tracked[path]=(mode.decode(), expected_sha.decode())
    actual=set()
    for base,dirs,files in os.walk(checkout,topdown=True,followlinks=False):
        root=Path(base)
        if root==checkout:
            dirs[:]=[x for x in dirs if x!='.git']
        for directory in dirs:
            if (root/directory).is_symlink():
                raise ValueError('SYMLINK_SOURCE_DIRECTORY')
        for name in files:
            file=root/name
            relative=file.relative_to(checkout).as_posix()
            if file.is_symlink() or not file.is_file():
                raise ValueError('NON_REGULAR_SOURCE_FILE')
            actual.add(relative)
            if relative not in tracked:
                raise ValueError('IGNORED_OR_UNTRACKED_SOURCE:'+relative)
            mode,expected_blob=tracked[relative]
            data=file.read_bytes()
            actual_blob=sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
            if actual_blob!=expected_blob:
                raise ValueError('SOURCE_BYTES_DIFFER_FROM_GIT:'+relative)
            executable=bool(file.stat().st_mode & (stat.S_IXUSR|stat.S_IXGRP|stat.S_IXOTH))
            if executable != (mode=='100755'):
                raise ValueError('SOURCE_MODE_DIFFER_FROM_GIT:'+relative)
    if actual!=set(tracked):
        raise ValueError('TRACKED_SOURCE_MISSING')
    for path in ('tools/synthetic_week.py','tools/synthetic_week_offline_exec.py',
                 'tools/synthetic_daily_pipeline.py','engine/gpi_lite.py',
                 'engine/h5_decision.py','engine/odds/odds_movement_builder.py'):
        if path not in tracked:
            raise ValueError('REQUIRED_TRACKED_ENGINE_MISSING:'+path)


def child_executor(checkout: Path, python: str) -> Callable[[str,Path],None]:
    def run_one(day: str, output: Path):
        env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'HOME':'/nonexistent',
             'PYTHONPATH':str(checkout),'PYTHONDONTWRITEBYTECODE':'1',
             'PYTHONNOUSERSITE':'1','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'}
        cmd=[python,'-m','tools.synthetic_week_offline_exec',python,'-c',
             RUN_CHILD,day,str(output)]
        p=subprocess.run(cmd,cwd=checkout,env=env,text=True,capture_output=True,timeout=90)
        if p.returncode != 0 or 'OFFLINE_SECCOMP_LOADED' not in p.stderr:
            raise RuntimeError('SYNTHETIC_CHILD_FAILED:'+str(p.returncode)+':'+p.stderr[-1400:])
    return run_one


def main() -> int:
    ap=argparse.ArgumentParser(description='7-day fiction-only offline qualification candidate')
    ap.add_argument('--checkout',type=Path,required=True)
    ap.add_argument('--expected-commit',required=True)
    ap.add_argument('--outdir',type=Path,required=True)
    ap.add_argument('--resume',action='store_true')
    args=ap.parse_args()
    checkout=args.checkout.resolve(strict=True)
    git_preflight(checkout,args.expected_commit)
    output_abs=args.outdir.absolute()
    if output_abs==checkout or checkout in output_abs.parents:
        raise ValueError('OUTPUT_MUST_BE_OUTSIDE_GIT_CHECKOUT')
    result=run_week(args.outdir,source_commit=args.expected_commit,
                    execute_day=child_executor(checkout,sys.executable),resume=args.resume)
    print(json.dumps({'status':'SYNTHETIC_WEEK_COMPLETED','days':len(result['days']),
                      'STRICT_ALLOWED':False,'REAL_BET_ALLOWED':False,'NO_BET':'ACTIVE'},sort_keys=True))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
