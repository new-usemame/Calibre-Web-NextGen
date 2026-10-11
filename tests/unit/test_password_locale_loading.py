"""Exercise password-strength locale loading through the shipped i18next engine."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is needed for the real browser i18next engine")

SCRIPT = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const root = process.argv[1], locale = process.argv[2];
const reads = [], failures = [];
let strength;
const backend = {type: 'backend', init() {}, read(language, namespace, callback) {
  reads.push(language);
  const path = root + '/cps/static/js/libs/pwstrength/locales/' + language + '.json';
  try {callback(null, JSON.parse(fs.readFileSync(path, 'utf8')));}
  catch(error) {failures.push(language);callback(error, false);}
}};
const data = {lang: locale, verify: 'True', min: 8, lower: 'True', upper: 'True',
              word: 'False', number: 'True', special: 'True'};
const context = {setTimeout, clearTimeout, console, i18nextHttpBackend: backend,
                 document: {}, getPath: () => '',
                 $: selector => selector === context.document
                    ? {ready: callback => callback()}
                    : {data: name => data[name], pwstrength: options => {strength = options;}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(root + '/cps/static/js/libs/pwstrength/i18next.min.js', 'utf8'), context);
const original = context.i18next.init.bind(context.i18next);
let done;
const completion = new Promise(resolve => {done = resolve;});
context.i18next.init = (options, callback) => original(options, (...args) => {
  callback(...args);done();
});
vm.runInContext(fs.readFileSync(root + '/cps/static/js/password.js', 'utf8'), context);
completion.then(() => {
  console.log(JSON.stringify({reads, failures, strength,
    translated: context.i18next.t('wordMinLength'), language: context.i18next.resolvedLanguage}));
}).catch(error => {console.error(error);process.exitCode = 1;});
"""


@pytest.mark.parametrize("locale, expected", [("nl", "en"), ("fr", "fr"), ("fr-FR", "fr"), ("zh-TW", "zh-TW")])
def test_password_strength_uses_shipped_locale_or_english_fallback(locale, expected):
    result = subprocess.run([NODE, "-e", SCRIPT, str(ROOT), locale],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["failures"] == [], report
    assert expected in report["reads"], report
    assert report["language"] == expected, report
    resource = json.loads((ROOT / "cps/static/js/libs/pwstrength/locales" / (expected + ".json")).read_text())
    assert report["translated"] == resource["wordMinLength"], report
    assert report["reads"] and all(
        (ROOT / "cps/static/js/libs/pwstrength/locales" / (language + ".json")).is_file()
        for language in report["reads"]
    ), report
    assert report["strength"]["common"]["minChar"] == 8
    assert report["strength"]["rules"]["activated"]["wordLowercase"] is True
    assert report["strength"]["rules"]["activated"]["wordUppercase"] is True
    assert report["strength"]["rules"]["activated"]["wordOneNumber"] is True
    assert report["strength"]["rules"]["activated"]["wordOneSpecialChar"] is True
