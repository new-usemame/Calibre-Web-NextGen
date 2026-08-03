/**
 * waits until queue is finished, meaning the book is done loading
 * @param callback
 */
function qFinished(callback){
    let timeout=setInterval(()=>{
        if (reader && reader.rendition && reader.rendition.q && reader.rendition.q.running === undefined) {
            clearInterval(timeout);
            callback();
        }
        },300
    )
}

function calculateProgress(){
    if (!reader || !reader.rendition || !reader.rendition.location || !reader.rendition.location.end) {
        return 0;
    }
    let data=reader.rendition.location.end;
    if (!data || !data.cfi || !epub || !epub.locations) {
        return 0;
    }
    return Math.round(epub.locations.percentageFromCfi(data.cfi)*100);
}

/**
 * Compute the user's progress within the CURRENT spine section (chapter),
 * complementing the book-wide calculateProgress() above. Backport of
 * janeczku/calibre-web#3370 (@ryan-c-scott) adapted to our split
 * epub-progress.js architecture.
 *
 * Returns a number 0..100, or null if not enough state to compute.
 * Uses the `epub` global's `locations._locations` (the array of CFIs
 * that our `epub.locations.generate()` call in qFinished() produced).
 * Note: upstream uses `reader.book.locations` because their PR runs
 * inside epub.js where `reader.book === epub`. Our progress logic
 * lives in a separate file and uses its own `epub` instance; the
 * reader has a different ePub instance whose locations are never
 * generated. We rely on the `epub` global throughout for consistency.
 *
 * The section's start CFI is the first locations-array entry whose
 * string contains the spine item's `cfiBase`; the section's end CFI
 * is the last such entry. We map
 * (current - sectionStart) / (sectionEnd - sectionStart) to 0..100.
 */
function calculateSectionProgress(){
    if (!reader || !reader.rendition || !reader.rendition.location) {
        return null;
    }
    const loc = reader.rendition.location;
    if (!loc.start || typeof loc.start.index !== "number" || !loc.start.cfi) {
        return null;
    }
    // Use the `epub` global throughout — that's the instance whose
    // locations we actually generate. `reader.book` is a separate ePub
    // instance whose `locations._locations` stays empty in our setup.
    if (!epub || !epub.spine || !epub.locations) {
        return null;
    }
    const spineItem = epub.spine.get(loc.start.index);
    if (!spineItem || !spineItem.cfiBase) {
        return null;
    }
    const allLocations = epub.locations._locations;
    if (!Array.isArray(allLocations) || allLocations.length === 0) {
        return null;
    }
    const baseCfi = spineItem.cfiBase;
    const sectionStartCfi = allLocations.find(cfi => cfi.includes(baseCfi));
    // findLast is ES2023 (Safari 15.4+, Chrome 97+, Firefox 104+); the
    // reader is a modern-browser surface anyway. Manual fallback via
    // reverse iteration kept tight in case the runtime is older.
    let sectionEndCfi;
    if (typeof allLocations.findLast === "function") {
        sectionEndCfi = allLocations.findLast(cfi => cfi.includes(baseCfi));
    } else {
        for (let i = allLocations.length - 1; i >= 0; i--) {
            if (allLocations[i].includes(baseCfi)) {
                sectionEndCfi = allLocations[i];
                break;
            }
        }
    }
    if (!sectionStartCfi || !sectionEndCfi) {
        return null;
    }
    if (sectionStartCfi === sectionEndCfi) {
        // Single-location section: by definition we've covered all of it.
        return 100;
    }
    const startNorm = epub.locations.percentageFromCfi(sectionStartCfi);
    const endNorm = epub.locations.percentageFromCfi(sectionEndCfi);
    const sectionSpan = endNorm - startNorm;
    if (!sectionSpan || sectionSpan <= 0) {
        return null;
    }
    const bookNorm = epub.locations.percentageFromCfi(loc.start.cfi);
    const sectionNorm = (bookNorm - startNorm) / sectionSpan;
    return Math.max(0, Math.min(100, Math.round(sectionNorm * 100)));
}

let cfiSaveTimer = null;
let pendingCfi = null;
let pendingPercentage = null;

function cfiStorageKey() {
    return window.calibre && window.calibre.bookUrl
        ? "calibre.reader.cfi." + window.calibre.bookUrl : null;
}

function storeCfi(cfi, dirty) {
    let key = cfiStorageKey();
    if (key && cfi) {
        localStorage.setItem(key, JSON.stringify({cfi: cfi, dirty: dirty, savedAt: Date.now()}));
    }
}

// #324: the CFI is only meaningful to this reader. The percentage travels —
// the server hands it to the shared Kobo/KOReader progress carrier so reading
// in the browser shows up on the user's devices. Only ever sent alongside a
// percentage the caller has already validated against generated locations
// (a pre-generate() sample is a meaningless 0 — CWA #1364).
function persistCfi(cfi, keepalive, percentage) {
    if (!cfi || !window.calibre || !window.calibre.bookmarkUrl) return Promise.resolve();
    let token = window.calibre.csrfToken || document.querySelector("input[name='csrf_token']")?.value;
    let body = 'bookmark=' + encodeURIComponent(cfi);
    if (typeof percentage === 'number' && isFinite(percentage) && percentage > 0) {
        body += '&percentage=' + encodeURIComponent(percentage);
    }
    return fetch(window.calibre.bookmarkUrl, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-CSRFToken': token || ''
        },
        body: body,
        credentials: 'same-origin',
        keepalive: !!keepalive
    }).then((response) => {
        if (!response.ok) throw new Error('bookmark save failed');
        storeCfi(cfi, false);
    }).catch(() => {
        storeCfi(cfi, true);
    });
}

function scheduleCfiSave(cfi, percentage) {
    pendingCfi = cfi;
    pendingPercentage = percentage;
    if (cfiSaveTimer) clearTimeout(cfiSaveTimer);
    cfiSaveTimer = setTimeout(() => {
        cfiSaveTimer = null;
        let currentCfi = pendingCfi;
        let currentPercentage = pendingPercentage;
        pendingCfi = null;
        pendingPercentage = null;
        persistCfi(currentCfi, false, currentPercentage);
    }, 800);
}

function flushCfiSave() {
    if (cfiSaveTimer) clearTimeout(cfiSaveTimer);
    cfiSaveTimer = null;
    let currentCfi = pendingCfi || reader?.rendition?.currentLocation?.()?.start?.cfi;
    let currentPercentage = pendingPercentage;
    pendingCfi = null;
    pendingPercentage = null;
    if (currentCfi) persistCfi(currentCfi, true, currentPercentage);
}

document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushCfiSave();
});
window.addEventListener('pagehide', flushCfiSave);

// register new event emitter locationchange that fires on urlchange
// source: https://stackoverflow.com/a/52809105/21941129
(() => {
    let oldPushState = history.pushState;
    history.pushState = function pushState() {
        let ret = oldPushState.apply(this, arguments);
        window.dispatchEvent(new Event('locationchange'));
        return ret;
    };

    let oldReplaceState = history.replaceState;
    history.replaceState = function replaceState() {
        let ret = oldReplaceState.apply(this, arguments);
        window.dispatchEvent(new Event('locationchange'));
        return ret;
    };

    window.addEventListener('popstate', () => {
        window.dispatchEvent(new Event('locationchange'));
    });
})();

window.addEventListener('locationchange',()=>{
    let newPos=calculateProgress();
    if (progressDiv) {
        // CW #3370 (@ryan-c-scott) backport: also show section progress
        // alongside the book percentage. Falls back to book-only if
        // section computation isn't ready yet (e.g. before
        // locations.generate() finishes).
        const sectionPct = calculateSectionProgress();
        if (sectionPct !== null) {
            progressDiv.textContent = sectionPct + "% (" + newPos + "% in book)";
        } else {
            progressDiv.textContent = newPos + "%";
        }
    }
    // CWA #1364 root-cause fix: only save to localStorage AFTER
    // `epub.locations.generate()` has resolved. Before that point,
    // `calculateProgress()` returns 0 because there are no locations
    // to map the current CFI against — saving that fake 0 wipes the
    // user's prior valid position. The qFinished/restore path then
    // reads localStorage=0, calls `display(cfiFromPercentage(0))`, and
    // the user lands at the beginning of the book even though they
    // were reading at e.g. 35% before. This is the headline symptom
    // in the upstream report: "opens at the correct cached position
    // then immediately snaps back to the beginning".
    if (window.calibre && window.calibre.bookUrl
            && epub && epub.locations
            && Array.isArray(epub.locations._locations)
            && epub.locations._locations.length > 0) {
        let bookKey = window.calibre.bookUrl;
        localStorage.setItem("calibre.reader.progress." + bookKey, newPos);
        let cfi = reader && reader.rendition && reader.rendition.currentLocation
            ? reader.rendition.currentLocation()?.start?.cfi : null;
        // newPos is trustworthy here: this branch already required generated
        // locations, which is the guard that makes it a real percentage and not
        // the pre-generate() 0 that CWA #1364 was about.
        if (cfi) scheduleCfiSave(cfi, newPos);
    }
});

var epub=ePub(calibre.bookUrl)

let progressDiv=document.getElementById("progress");

qFinished(()=>{
    if (!epub || !epub.locations) {
        return;
    }
    epub.locations.generate().then(()=> {
        // A dirty CFI is an unsynced offline edit; otherwise the server is the
        // source of truth and legacy percentage storage is only a fallback.
        if (window.calibre && window.calibre.bookUrl && reader && reader.rendition) {
            let bookKey = window.calibre.bookUrl;
            let savedProgress = localStorage.getItem("calibre.reader.progress." + bookKey);
            let local = null;
            let restoreCfi = null;
            try {
                local = JSON.parse(localStorage.getItem("calibre.reader.cfi." + bookKey) || 'null');
            } catch (_) { /* corrupt cache falls through */ }
            if (local && local.dirty && local.cfi) {
                restoreCfi = local.cfi;
                persistCfi(local.cfi, false);
            } else if (window.calibre.bookmark && window.calibre.bookmark.length > 0) {
                restoreCfi = window.calibre.bookmark;
            } else if (local && local.cfi) {
                restoreCfi = local.cfi;
            } else if (savedProgress) {
                let percentage = parseInt(savedProgress, 10) / 100;
                let cfi = epub.locations.cfiFromPercentage(percentage);
                if (cfi) {
                    restoreCfi = cfi;
                }
            } else if (window.calibre.kosyncPercent !== null && window.calibre.kosyncPercent !== undefined) {
                let kosyncPercent = parseFloat(window.calibre.kosyncPercent);
                if (!isNaN(kosyncPercent) && kosyncPercent > 0) {
                    let percentage = kosyncPercent / 100;
                    let cfi = epub.locations.cfiFromPercentage(percentage);
                    if (cfi) {
                        restoreCfi = cfi;
                    }
                }
            }
            if (restoreCfi) {
                return Promise.resolve(reader.rendition.display(restoreCfi));
            }
        }
    }).catch(() => {
        // A malformed stale CFI must not prevent the reader's normal progress
        // event from initializing the UI.
    }).finally(() => {
        window.dispatchEvent(new Event('locationchange'));
    });
})
