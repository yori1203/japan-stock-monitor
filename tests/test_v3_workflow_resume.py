"""Exercise the workflow's actual shell argument selection without acquisition."""
from pathlib import Path
import os
import shlex
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('event,attempt,mode,new_run,expected_mode,reset', [
    ('push','1','','','full',True),
    ('push','2','','','full',False),
    ('workflow_dispatch','1','full','false','full',False),
    ('workflow_dispatch','1','full','true','full',True),
    ('workflow_dispatch','1','cached','false','cached',True),
])
def test_workflow_preserves_full_resume(event,attempt,mode,new_run,expected_mode,reset):
    bash=os.environ.get('V3_TEST_BASH') or shutil.which('bash')
    if not bash:pytest.skip('Bash required for the Actions shell regression')
    text=(Path(__file__).resolve().parents[1]/'.github/workflows/v3-pipeline.yml').read_text(encoding='utf-8')
    body=text.split('      - name: Run bounded pipeline',1)[1].split('        run: |\n',1)[1].split('      - uses:',1)[0]
    body='\n'.join(line[10:] for line in body.splitlines())
    for expr,value in {"inputs.mode || 'full'":mode or 'full','inputs.new_run':new_run,
                       'github.event_name':event,'github.run_attempt':attempt}.items():
        body=body.replace('${{ '+expr+' }}',value)
    result=subprocess.run([bash,'--noprofile','--norc','-s'],input='python() { printf "%s\\n" "$*"; }\n'+body,
                          text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
    args=shlex.split(result.stdout.strip())
    assert args[0]=='v3_pipeline.py'
    assert args[args.index('--mode')+1]==expected_mode
    assert ('--new-run' in args)==reset
