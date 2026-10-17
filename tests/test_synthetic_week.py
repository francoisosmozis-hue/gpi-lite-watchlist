"""Offline scheduler/orchestrator tests using explicit FAKE emitters, NOT real engines."""
from __future__ import annotations
import json
from hashlib import sha256
from pathlib import Path
import pytest

from tools.synthetic_week import (DAYS, run_week, verify_week, output_guard)
from tools.synthetic_daily_pipeline import PROOF_CLASS, FIXTURE_VERSION, SOURCE_COMMIT_BASE
from tools.synthetic_week import canonical

COMMIT='f'*40  # Synthetic source marker, not a genuine GitHub identity.
NAMES=('program.json','snapshots.json','watchlist.json','movements.json','decisions.json',
       'fictional_results.json','tracking.csv','summary.json','dashboard.html')

def fake_emit(day, output):
    """Deterministic nine-artifact FAKE fixture: no engine imported or invoked."""
    output.mkdir(parents=True)
    files={name:b'FAKE_ONLY\n' for name in NAMES}
    files['program.json']=canonical([{'race_uid':x,'date':day,'source_kind':PROOF_CLASS}
                              for x in ('SYNTHETIC_R1C1','SYNTHETIC_R1C2')])
    files['snapshots.json']=canonical([{'race_uid':race,'fixture_timestamp':day+'T'+hour,
                                        'source_kind':PROOF_CLASS}
                    for race in ('SYNTHETIC_R1C1','SYNTHETIC_R1C2')
                    for hour in ('09:00:00+01:00','14:30:00+01:00','14:55:00+01:00')])
    files['decisions.json']=canonical([{'race_uid':race,'source_kind':PROOF_CLASS,
        'stake_eur':0,'ticket_real':'NO_BET','engine_output':{
            'verdict':'NO_BET','ticket_exact':'NO_BET','strict_allowed_oui_non':'non'}}
            for race in ('SYNTHETIC_R1C1','SYNTHETIC_R1C2')])
    files['summary.json']=canonical({'all_no_bet':True,'operational_proof':False,
        'prospective_proof':False,'daily_pipeline_qualified':False,
        'strict_allowed':False,
        'real_bet_allowed':False,'governance_transition':'NONE','proof_class':PROOF_CLASS,
        'race_count':2,'snapshot_count':6})
    for name,content in files.items():
        (output/name).write_bytes(content)
    manifest={'proof_class':PROOF_CLASS,'scenario_version':FIXTURE_VERSION,
        'source_commit_base':SOURCE_COMMIT_BASE,'artifacts':{
            name:sha256(content).hexdigest() for name,content in files.items()},
        'strict_allowed':False,'real_bet_allowed':False,'governance_transition':'NONE'}
    (output/'audit_manifest.json').write_bytes(canonical(manifest))


def test_seven_distinct_days_complete(tmp_path):
    root=tmp_path/'synthetic_week'
    result=run_week(root,execute_day=fake_emit,source_commit=COMMIT)
    assert [d['logical_date'] for d in result['days']]==list(DAYS)
    assert verify_week(root)
    assert all(d['verdict']=='NO_BET' and d['stake_eur']==0 for d in result['days'])
    assert (root/'dashboard.html').read_bytes().count(b'NO_BET')==7


def test_second_independent_run_deterministic(tmp_path):
    a,b=tmp_path/'synthetic_a',tmp_path/'synthetic_b'
    run_week(a,execute_day=fake_emit,source_commit=COMMIT)
    run_week(b,execute_day=fake_emit,source_commit=COMMIT)
    def names(root):
        return {str(x.relative_to(root)):x.read_bytes() for x in root.rglob('*') if x.is_file()}
    assert names(a)==names(b)


def test_resume_after_third_day_crash(tmp_path):
    root=tmp_path/'synthetic_resume'
    seen=[]
    def flaky(day,output):
        if len(seen)==3:
            raise RuntimeError('INJECTED_FAILURE')
        fake_emit(day,output)
        seen.append(day)
    with pytest.raises(RuntimeError,match='INJECTED_FAILURE'):
        run_week(root,execute_day=flaky,source_commit=COMMIT)
    assert len(list((root/'days').iterdir()))==3
    seen.clear()
    def on_resume(day, output):
        seen.append(day)
        fake_emit(day,output)
    run_week(root,execute_day=on_resume,source_commit=COMMIT,resume=True)
    assert seen==list(DAYS[3:]) and verify_week(root)


def test_resume_completed_is_idempotent(tmp_path):
    root=tmp_path/'synthetic_resume'
    original=run_week(root,execute_day=fake_emit,source_commit=COMMIT)
    def must_not_run(*args):
        raise AssertionError('DUPLICATE_EXECUTION_FORBIDDEN')
    assert run_week(root,execute_day=must_not_run,source_commit=COMMIT,resume=True)==original
    assert verify_week(root)


def test_missing_artifact_tamper_blocks_resume(tmp_path):
    root=tmp_path/'synthetic_resume'
    run_week(root,execute_day=fake_emit,source_commit=COMMIT)
    (root/'days'/'synthetic_20300104'/'decisions.json').unlink()
    assert not verify_week(root)
    with pytest.raises(ValueError,match='TAMPERED_FINAL_INDEX'):
        run_week(root,execute_day=fake_emit,source_commit=COMMIT,resume=True)


def test_symlink_and_parent_traversal_refused(tmp_path):
    safe=tmp_path/'safe'
    safe.mkdir()
    link=tmp_path/'link'
    link.symlink_to(safe,target_is_directory=True)
    with pytest.raises(ValueError,match='SYMLINK'):
        output_guard(link/'synthetic_child')
    with pytest.raises(ValueError,match='NAMESPACE'):
        output_guard(tmp_path/'outside'/'..'/'synthetic_child')
    with pytest.raises(ValueError,match='OPERATIONAL'):
        output_guard(tmp_path/'data'/'output'/'synthetic_child')


def test_abandoned_staging_fails_closed(tmp_path):
    root=tmp_path/'synthetic_test'
    def interrupted(*args):
        raise RuntimeError('INJECTED_CRASH')
    with pytest.raises(RuntimeError,match='INJECTED_CRASH'):
        run_week(root,execute_day=interrupted,source_commit=COMMIT)
    days=root/'days'
    (days/'synthetic_20300102_pending').mkdir()
    with pytest.raises(ValueError,match='UNEXPECTED_DAY'):
        run_week(root,execute_day=fake_emit,source_commit=COMMIT,resume=True)


def test_source_commit_missing_or_mismatched_rejected(tmp_path):
    root=tmp_path/'synthetic_test'
    with pytest.raises(ValueError,match='SHA_REQUIRED'):
        run_week(root,execute_day=fake_emit,source_commit='ND')
    run_week(root,execute_day=fake_emit,source_commit=COMMIT)
    with pytest.raises(ValueError,match='SOURCE_COMMIT_MISMATCH'):
        run_week(root,execute_day=fake_emit,source_commit='a'*40,resume=True)


def test_tampered_week_governance_detected(tmp_path):
    root=tmp_path/'synthetic_test'
    run_week(root,execute_day=fake_emit,source_commit=COMMIT)
    index=root/'week_index.json';obj=json.loads(index.read_bytes())
    obj['STRICT_ALLOWED']=True
    index.write_bytes(canonical(obj))
    assert not verify_week(root)


def test_source_lock_rejects_cross_commit_partial_resume(tmp_path):
    root=tmp_path/'synthetic_partial'
    def interrupted(*args):
        raise RuntimeError('TEST_ONLY_CRASH')
    with pytest.raises(RuntimeError, match='TEST_ONLY_CRASH'):
        run_week(root, execute_day=interrupted, source_commit=COMMIT)
    with pytest.raises(ValueError, match='SOURCE_LOCK_MISMATCH'):
        run_week(root, execute_day=fake_emit, source_commit='a'*40, resume=True)
    (root/'source_lock.json').unlink()
    with pytest.raises(ValueError, match='SOURCE_LOCK_MISSING'):
        run_week(root, execute_day=fake_emit, source_commit=COMMIT, resume=True)


def test_git_preflight_checks_ignored_files_bytes_and_modes(tmp_path):
    """Adversarial Git preflight with a local synthetic repo, NOT a GPI checkout."""
    import os, subprocess
    from tools.synthetic_week import git_preflight
    base=tmp_path/'checkout';base.mkdir()
    required=('tools/synthetic_week.py','tools/synthetic_week_offline_exec.py',
              'tools/synthetic_daily_pipeline.py','engine/gpi_lite.py',
              'engine/h5_decision.py','engine/odds/odds_movement_builder.py')
    subprocess.run(['git','init','-q',str(base)],check=True)
    for name in required:
        path=base/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'GIT_PREFLIGHT_FAKE_FIXTURE\n')
    (base/'.gitignore').write_text('invisible.py\n')
    subprocess.run(['git','-C',str(base),'add','.'],check=True)
    env={**os.environ,'GIT_AUTHOR_NAME':'synthetic','GIT_AUTHOR_EMAIL':'no@example.invalid',
         'GIT_COMMITTER_NAME':'synthetic','GIT_COMMITTER_EMAIL':'no@example.invalid'}
    subprocess.run(['git','-C',str(base),'commit','-qm','synthetic preflight fixture'],check=True,env=env)
    head=subprocess.check_output(['git','-C',str(base),'rev-parse','HEAD'],text=True).strip()
    git_preflight(base,head)
    ignored=base/'invisible.py';ignored.write_text('print(1)')
    with pytest.raises(ValueError,match='IGNORED_OR_UNTRACKED'):
        git_preflight(base,head)
    ignored.unlink()
    p=base/required[0]
    p.write_bytes(b'MALICIOUS_MODIFICATION\n')
    subprocess.run(['git','-C',str(base),'update-index','--assume-unchanged',required[0]],check=True)
    with pytest.raises(ValueError,match='SOURCE_BYTES_DIFFER'):
        git_preflight(base,head)
    p.write_bytes(b'GIT_PREFLIGHT_FAKE_FIXTURE\n')
    p.chmod(0o755)
    with pytest.raises(ValueError,match='SOURCE_MODE_DIFFER'):
        git_preflight(base,head)


def test_running_while_source_lock_tampered_fails_closed(tmp_path):
    root=tmp_path/'synthetic_partial'
    def interrupted(*args):
        raise RuntimeError('TEST_CRASH')
    with pytest.raises(RuntimeError,match='TEST_CRASH'):
        run_week(root,execute_day=interrupted,source_commit=COMMIT)
    path=root/'source_lock.json';record=json.loads(path.read_bytes())
    record['strict_allowed']=True
    path.write_bytes(canonical(record))
    with pytest.raises(ValueError,match='SOURCE_LOCK_MISMATCH'):
        run_week(root,execute_day=fake_emit,source_commit=COMMIT,resume=True)


def test_week_rejects_symlinked_completion_index(tmp_path):
    root=tmp_path/'synthetic_verified'
    run_week(root,execute_day=fake_emit,source_commit=COMMIT)
    target=tmp_path/'fake_completion.json'
    target.write_bytes((root/'week_index.json').read_bytes())
    (root/'week_index.json').unlink()
    (root/'week_index.json').symlink_to(target)
    assert not verify_week(root)
    with pytest.raises(ValueError,match='TAMPERED_FINAL_INDEX'):
        run_week(root,execute_day=fake_emit,source_commit=COMMIT,resume=True)


def test_day_summary_cannot_claim_prospective_proof(tmp_path):
    root=tmp_path/'synthetic_verified'
    run_week(root,execute_day=fake_emit,source_commit=COMMIT)
    daily=root/'days'/'synthetic_20300102'
    path=daily/'summary.json'
    payload=json.loads(path.read_bytes())
    payload['prospective_proof']=True
    data=canonical(payload)
    path.write_bytes(data)
    manifest_path=daily/'audit_manifest.json'
    manifest=json.loads(manifest_path.read_bytes())
    manifest['artifacts']['summary.json']=sha256(data).hexdigest()
    manifest_path.write_bytes(canonical(manifest))
    assert not verify_week(root)
