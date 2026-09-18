import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get('V3_TEST_BASH') or shutil.which('bash')


class SessionPersistenceTests(unittest.TestCase):
    def test_config_changes_only_requested_values(self):
        config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
        self.assertEqual([s['code'] for s in config['portfolio']], ['6740','6573','4596','4597','6721'])
        self.assertEqual(config['portfolio'][-1], {'code':'6721','shares':100,'priority':'high'})
        self.assertEqual(next(s['shares'] for s in config['portfolio'] if s['code']=='4597'),300)
        self.assertEqual([s['code'] for s in config['watchlist']],['6177','2134','2410','4583'])
        self.assertEqual([config['daily_report'][s+'_time_jst'] for s in ['morning','noon','evening']],['08:30','12:30','16:00'])

    @unittest.skipUnless(BASH, 'bash required for actual workflow shell tests')
    def test_actual_session_selection(self):
        text=(ROOT/'.github/workflows/stock-monitor.yml').read_text(encoding='utf-8')
        block=textwrap.dedent(text[text.index('          if [[ "$EVENT_NAME"'):text.index('      - name: Run stock monitor')])
        with tempfile.TemporaryDirectory() as folder:
            for cron,expected in [('30 23 * * 0-4','morning'),('30 3 * * 1-5','noon'),('0 7 * * 1-5','evening')]:
                with self.subTest(session=expected):
                    dest=Path(folder)/'output'
                    dest.write_text('')
                    env={**os.environ,'EVENT_NAME':'schedule','SCHEDULE':cron,'MANUAL_SESSION':'','GITHUB_OUTPUT':dest.as_posix()}
                    subprocess.run([BASH,'-e','-c',block],env=env,check=True,capture_output=True)
                    self.assertEqual(dest.read_text().strip(),'session='+expected)
        self.assertIn('cron: "30 23 * * 0-4"',text)
        self.assertNotIn('30 0 * * 1-5',text)

    @unittest.skipUnless(BASH, 'bash required for actual persistence test')
    def test_actual_persistence_without_optional_backup_and_push_errors(self):
        text=(ROOT/'.github/workflows/v3-operations.yml').read_text(encoding='utf-8')
        block=textwrap.dedent(text[text.index('          shopt -s nullglob'):text.index('      - uses: actions/cache/save')])
        block=block.replace('${{ steps.session.outputs.name }}','morning')
        def shell(code,folder,check=True):
            return subprocess.run([BASH,'-e','-o','pipefail','-c',code],cwd=folder,check=check,capture_output=True,text=True)
        with tempfile.TemporaryDirectory() as folder:
            shell("git init --bare remote.git; git init -b v3-operation-data state; git -C state config user.name Test; git -C state config user.email test@example.com; git -C state remote add origin ../remote.git",folder)
            state=Path(folder)/'state'
            files=['report_morning.md','report_noon.md','report_evening.md','report.md','signals.csv','backtest_report.md','v3_report.md','v3_final_ranking.json','operations_status.json']
            for file in files:(state/file).write_text('fixture',encoding='utf-8')
            shell(block,folder)
            saved=shell('git --git-dir=remote.git ls-tree --name-only refs/heads/v3-operation-data',folder).stdout.splitlines()
            self.assertEqual(set(saved),set(files))
            shell(block,folder)  # idempotent, no new commit required
            (state/'signals.csv.v1.bak').write_text('optional')
            shell(block,folder)
            self.assertIn('signals.csv.v1.bak',shell('git --git-dir=remote.git ls-tree --name-only refs/heads/v3-operation-data',folder).stdout)
            shell('git -C state remote set-url origin ../missing.git',folder)
            (state/'report.md').write_text('changed')
            self.assertNotEqual(shell(block,folder,False).returncode,0)


if __name__=='__main__':unittest.main()
