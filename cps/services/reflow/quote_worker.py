"""Standalone source preparation process; importing the web app is unnecessary."""
import importlib.util
import json
import os
from pathlib import Path
import sys


def main():
    # Load this exact installed package without cps.__init__ (web configuration,
    # CLI parsing, database/session setup). All request/render code is shared.
    root=Path(__file__).resolve().parent
    name='_reflow_source_quote_runtime'
    spec=importlib.util.spec_from_file_location(name,root/'__init__.py',submodule_search_locations=[str(root)])
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    quote=importlib.import_module(name+'.structural_quote')
    import pymupdf
    request=json.load(sys.stdin);control=Path(request['control']);status=Path(request['progress'])
    def progress(event):
        data={'stage':str(event.stage),'page':int(event.page),'pages':int(event.pages)}
        temp=status.with_suffix('.tmp');temp.write_text(json.dumps(data));os.replace(temp,status)
    options=request['options'];cache=Path(request['cache_root'])
    with pymupdf.open(request['source']) as doc:
        result=quote.measure(doc,recovery_opts={'mode':options['source_recovery'],
            'language':options['ocr_language'],'cache_dir':str(cache/'ocr-cache'),
            'scratch_dir':str(cache/'ocr-scratch')},progress=progress,should_stop=control.exists)
    if control.exists():return 2
    Path(request['output']).write_text(json.dumps(result,ensure_ascii=False,allow_nan=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
