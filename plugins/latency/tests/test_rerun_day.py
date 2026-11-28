"""Reviewer regression: CI reruns moving UTC days, across hash seeds."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('latency_rerun_fixture', ROOT/'__init__.py', submodule_search_locations=[str(ROOT)])
pkg = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pkg
spec.loader.exec_module(pkg)
from latency_rerun_fixture import github
from latency_rerun_fixture.store import Store


class Rerun(unittest.TestCase):
    def test_rerun_moving_to_next_utc_day(self):
        day = 86400
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'series.sqlite')
            defs = [dict(id='ci',source='github.example',start='run_started_at',end='updated_at',workflow='ci.yml')]
            cfg = {'example':{'repo':'example/repo'}}
            row = dict(id=1,run_attempt=1,path='ci.yml',status='completed',conclusion='success',
                       created_at=github.iso(day-100),run_started_at=github.iso(day-90),updated_at=github.iso(day-60))
            with patch.object(github,'runs',return_value=[row]):
                github.collect_github(tmp,store,defs,cfg,0,2*day)
            revised = dict(row,run_attempt=2,run_started_at=github.iso(day+500),updated_at=github.iso(day+800))
            with patch.object(github,'runs',return_value=[revised]):
                github.collect_github(tmp,store,defs,cfg,0,2*day+60)
            view = store.view('ci',0,3*day)
            self.assertEqual(view['n'],1)
            self.assertEqual([(r['id'] is not None,r['start'],r['dur_ms']) for r in view['slowest']],[(True,day+500,300000)])

    def test_cross_day_rerun_under_ten_hash_seeds(self):
        for seed in range(10):
            with self.subTest(seed=seed):
                result = subprocess.run([sys.executable,str(Path(__file__).resolve()),'Rerun.test_rerun_moving_to_next_utc_day'],
                    env={**os.environ,'PYTHONHASHSEED':str(seed)},capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__ == '__main__':
    unittest.main()
