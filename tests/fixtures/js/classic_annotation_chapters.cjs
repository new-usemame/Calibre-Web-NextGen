// Exercise the actual overlay and navigation callbacks with repeated span IDs.
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8');
const end = source.lastIndexOf('})();');
function run(chapter, hrefs) {
    const events = [];
    const sections = hrefs.map((href, index) => ({href, index}));
    const doc = {
        getElementById: () => ({nodeValue: 'abcdefghijklmnopqrst'}),
        createTreeWalker: span => ({nextNode: () => span}),
        createRange: () => ({collapsed: false, setStart() {}, setEnd() {}}),
    };
    const contents = index => ({document: doc, sectionIndex: index, cfiFromRange: () => `section-${index}-cfi`});
    const reader = {book: {path: {directory: '/OEBPS/'}, spine: {
        spineItems: sections, get: index => sections[index],
    }}, rendition: {
        annotations: {highlight: cfi => events.push(['paint', cfi])},
        getContents: () => [contents(0)], display: cfi => events.push(['display', cfi]),
    }};
    const context = {document: {readyState: 'loading', addEventListener() {}}, window: {}, reader,
        NodeFilter: {SHOW_TEXT: 4}, console};
    vm.createContext(context);
    vm.runInContext(source.slice(0, end) +
        'globalThis.entry = {applyToContents, jumpToAnnotation, setRows: rows => {allRows = rows;}};\n' +
        source.slice(end), context);
    const row = {annotation_id: 'test', content_id: 'book!!' + chapter,
        start_kobospan: 'kobo.1.1', end_kobospan: 'kobo.1.1', start_offset: 0, end_offset: 10};
    context.entry.setRows([row]);
    context.entry.applyToContents(contents(0));
    context.entry.jumpToAnnotation(row);
    if (sections.length > 1) context.entry.applyToContents(contents(1));
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
console.log(JSON.stringify({
    selection: selectionPayload(),
    exact: run('OEBPS/b/chapter.xhtml', ['a/chapter.xhtml', 'b/chapter.xhtml']),
    ambiguous: run('chapter.xhtml', ['a/chapter.xhtml', 'b/chapter.xhtml']),
    encoded: run('OEBPS/Text/first chapter.xhtml', ['Text/first%20chapter.xhtml']),
    missing: run('OEBPS/missing/chapter.xhtml', ['a/chapter.xhtml', 'b/chapter.xhtml']),
}));
