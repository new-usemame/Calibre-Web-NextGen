"""Additive synthetic local-cadence control frozen AFTER C48 writer, before first execution."""
import importlib.util
from pathlib import Path
FIXTURE=Path(__file__).with_name('test_round7_native_authored_source_boundaries.py')
spec=importlib.util.spec_from_file_location('frozen_authored_boundary_fixture',FIXTURE)
frozen=importlib.util.module_from_spec(spec);spec.loader.exec_module(frozen)

def test_local_double_spaced_paragraph_survives_other_page_cadence(tmp_path):
    f=frozen.Original(tmp_path,pitch=14)
    try:
        rows=[f.row(0,[['t',t]],70,310+i*27)for i,t in enumerate((
            'The original ordinary account continues without termination',
            'while its next physical row develops the same argument',
            'until the final original row completes this explanation.'))]
        for row in rows:f.element(0,[row])
        r=f.publish();exact=' '.join(row['text']for row in rows)
        assert sum(frozen.tag(p)=='p'and frozen.plain(p)==exact for p in r.paragraphs)==1,'A distinct same-column genuine double-spaced paragraph cannot inherit another section normal cadence'
        assert r.report['degraded_unit_count']==0
    finally:f.close()
