// Run the production popup callbacks with a minimal DOM and capture fetch.
const fs = require('node:fs');
const vm = require('node:vm');
const elements = [];
function element(tag) {
    const el = {tag, style: {}, children: [], value: '',
        appendChild(child) { this.children.push(child); child.parentNode = this; },
        removeChild() {}, setAttribute() {},
        addEventListener(name, fn) { this[name] = fn; }};
    elements.push(el);
    return el;
}
const document = {readyState: 'loading', body: element('body'),
    createElement: element, addEventListener() {},
    querySelector() { return null; }, getElementById() { return null; }};
const calibre = {annotationsApiBase: '/annotations/1'};
const context = {document, calibre, window: {calibre, innerWidth: 1000, innerHeight: 800},
    fetch(url, options) {
        process.stdout.write(options.body);
        // Keep post-save rendering outside this payload contract.
        return new Promise(() => {});
    }};
vm.createContext(context);
let source = fs.readFileSync(process.argv[2], 'utf8');
// Expose the closure's entry points without replacing any production logic.
const end = source.lastIndexOf('})();');
source = source.slice(0, end) +
    'globalThis.popups = {showCreatePopup, showEditPopup};\n' + source.slice(end);
vm.runInContext(source, context);
if (process.argv[3] === 'create') {
    context.popups.showCreatePopup({start_kobospan: 'kobo.1.1'}, {}, {});
} else {
    context.popups.showEditPopup({annotation_id: 'saved', note_text: 'Old note'}, null, null);
}
elements.find(el => el.tag === 'textarea').value = '';
elements.find(el => el.className === 'cwa-ann-save').click();
