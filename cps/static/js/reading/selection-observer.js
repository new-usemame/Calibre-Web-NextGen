/** Observe selection from the parent when sandboxed frame events cannot escape.
 * WebKit 218086 blocks even parent-authored listeners inside a script-disabled
 * iframe. Keep that sandbox intact; read only the focused frame's DOM selection.
 * Native epub.js events remain authoritative and suppress duplicate delivery.
 */
export function observeReaderSelections(rendition, host = window) {
    let stopped = false, timer = null, candidate = null, delivered = null, stableSince = 0;
    const same = (a, b) => a && b && a.contents === b.contents
        && a.range.startContainer === b.range.startContainer && a.range.startOffset === b.range.startOffset
        && a.range.endContainer === b.range.endContainer && a.range.endOffset === b.range.endOffset;
    function selection(contents) {
        const selected = contents?.window?.getSelection();
        if (!selected || selected.isCollapsed || !selected.rangeCount) return null;
        const range = selected.getRangeAt(0);
        if (!range.toString().trim()) return null;
        return { contents, range: range.cloneRange() };
    }
    function remember(_cfi, contents) {
        try { delivered = selection(contents); candidate = null; } catch { /* disposed frame */ }
    }
    function pause() {
        if (timer !== null) host.clearTimeout(timer);
        timer = null;
        candidate = null;
    }
    function poll() {
        timer = null;
        if (stopped) return;
        schedule();
        if (host.document.hidden) { candidate = null; return; }
        const focused = host.document.activeElement;
        if (focused?.tagName !== 'IFRAME') { candidate = null; return; }
        try {
            const contents = (rendition.getContents() || []).find(c => c?.document?.defaultView?.frameElement === focused);
            if (!contents) { candidate = null; return; }
            const current = selection(contents);
            if (!current) { candidate = null; delivered = null; return; }
            if (same(current, delivered)) return;
            // Wait beyond epub.js's 250ms native debounce. Native events must
            // arrive first on engines that support them, without a late duplicate.
            if (!same(current, candidate)) {
                candidate = current;
                stableSince = host.performance.now();
                return;
            }
            if (host.performance.now() - stableSince < 300) return;
            const cfi = contents.cfiFromRange(current.range);
            if (!cfi) return;
            delivered = current;
            candidate = null;
            rendition.emit('selected', cfi, contents);
        } catch { candidate = null; /* frame navigated or was disposed */ }
    }
    function schedule() {
        if (!stopped && timer === null) timer = host.setTimeout(poll, 150);
    }
    rendition.on('selected', remember);
    host.addEventListener('pagehide', pause);
    host.addEventListener('pageshow', schedule);
    schedule();
    return () => {
        stopped = true;
        pause();
        delivered = null;
        rendition.off('selected', remember);
        host.removeEventListener('pagehide', pause);
        host.removeEventListener('pageshow', schedule);
    };
}
