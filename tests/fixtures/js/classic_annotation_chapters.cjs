// Exercise the actual overlay and navigation callbacks with repeated span IDs.
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8');
const end = source.lastIndexOf('})();');
async function run(chapter, hrefs, native = true, delayed = false, rejected = false) {
    const events = [];
    const sections = hrefs.map((href, index) => ({href, index}));
    const doc = {
        getElementById: () => ({nodeValue: 'abcdefghijklmnopqrst'}),
        createTreeWalker: span => ({nextNode: () => span}),
        createRange: () => ({collapsed: false, setStart() {}, setEnd() {}}),
    };
    const contents = index => ({document: doc, sectionIndex: index, cfiFromRange: () => `section-${index}-cfi`});
    let renderedIndex = 0;
    const reader = {book: {path: {directory: '/OEBPS/'}, spine: {
        spineItems: sections, get: index => sections[index],
    }}, rendition: {
        annotations: {highlight: cfi => events.push(['paint', cfi])},
        getContents: () => [contents(renderedIndex)], display: cfi => {
            events.push(['display', cfi]);
            if (!delayed) return;
            if (cfi === hrefs[1]) { renderedIndex = 1; return Promise.resolve(); }
            return rejected ? Promise.reject(new Error('Location no longer available')) : Promise.resolve();
        },
    }};
    // Minimal DOM dispatches the listener installed by the real sidebar renderer.
    const element = () => ({children: [], dataset: {}, style: {},
        appendChild(child) { this.children.push(child); },
        replaceChildren() { this.children = []; },
        addEventListener(type, callback) { this[type] = callback; },
    });
    const list = element();
    const context = {document: {readyState: 'loading', addEventListener() {},
        getElementById: () => list, createElement: element}, window: {}, reader,
        NodeFilter: {SHOW_TEXT: 4}, console};
    vm.createContext(context);
    vm.runInContext(source.slice(0, end) +
        'globalThis.entry = {applyToContents, renderSidebarList, setRows: rows => {allRows = rows;}};\n' +
        source.slice(end), context);
    const row = {annotation_id: 'test', content_id: 'book!!' + chapter,
        start_kobospan: native ? 'kobo.1.1' : null, end_kobospan: native ? 'kobo.1.1' : null,
        start_offset: 0, end_offset: 10, cfi_range: 'stored-cfi'};
    context.entry.setRows([row]);
    context.entry.applyToContents(contents(0));
    context.entry.renderSidebarList([row]);
    list.children[0].click();
    if (!delayed && sections.length > 1) context.entry.applyToContents(contents(1));
    await new Promise(resolve => setImmediate(resolve));
    return events;
}
function selectionPayload() {
    const span = {nodeType: 1, id: 'kobo.1.1'};
    const text = {nodeType: 3, parentNode: span, nodeValue: 'hello world'};
    const range = {startContainer: text, endContainer: text, startOffset: 0, endOffset: 5,
        toString: () => 'hello'};
    const context = {document: {readyState: 'loading', addEventListener() {}},
        NodeFilter: {SHOW_TEXT: 4},
        reader: {book: {spine: {get: () => ({href: 'Text/chapter.xhtml'})}}}};
    vm.createContext(context);
    vm.runInContext(source.slice(0, end) + 'globalThis.select = selectionToAnchor;\n' + source.slice(end), context);
    return context.select(range, {document: {createTreeWalker: () => ({nextNode: () => text})}, sectionIndex: 0});
}
(async () => {
console.log(JSON.stringify({
    selection: selectionPayload(),
    delayed: await run('OEBPS/b/chapter.xhtml', ['a/chapter.xhtml', 'b/chapter.xhtml'], true, true),
    rejected: await run('OEBPS/b/chapter.xhtml', ['a/chapter.xhtml', 'b/chapter.xhtml'], true, true, true),
    cfiOnly: await run('', ['a/chapter.xhtml'], false),
    exact: await run('OEBPS/b/chapter.xhtml', ['a/chapter.xhtml', 'b/chapter.xhtml']),
    ambiguous: await run('chapter.xhtml', ['a/chapter.xhtml', 'b/chapter.xhtml']),
    encoded: await run('OEBPS/Text/first chapter.xhtml', ['Text/first%20chapter.xhtml']),
    missing: await run('OEBPS/missing/chapter.xhtml', ['a/chapter.xhtml', 'b/chapter.xhtml']),
}));

})().catch(error => { console.error(error); process.exitCode = 1; });
