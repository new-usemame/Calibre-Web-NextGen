/* Private per-user book review. Text is always rendered through textContent. */
(function () {
  function initialize() {
    'use strict';
    var root = document.getElementById('private-book-review');
    if (!root || root.dataset.reviewInitialized === 'true') return;
    root.dataset.reviewInitialized = 'true';
    var url = root.dataset.url;
    var review = null;
    var status = document.getElementById('private-review-status');
    var error = document.getElementById('private-review-error');
    var text = document.getElementById('private-review-text');
    var form = document.getElementById('private-review-form');
    var draft = document.getElementById('private-review-draft');
    var actions = document.getElementById('private-review-actions');
    var edit = document.getElementById('private-review-edit');
    var remove = document.getElementById('private-review-delete');
    var retry = document.getElementById('private-review-retry');
    var confirm = document.getElementById('private-review-confirm');
    function showError(message) { error.textContent = message; error.hidden = false; }
    function clearError() { error.textContent = ''; error.hidden = true; }
    function busy(value) {
        root.setAttribute('aria-busy', String(value));
        root.querySelectorAll('button, textarea').forEach(function (control) { control.disabled = value; });
    }
    async function request(method, value) {
        var response = await cwaFetch(url, { method: method, headers: { 'Content-Type': 'application/json' }, body: value === undefined ? undefined : JSON.stringify(value) });
        if (!response.ok) {
            var detail = await response.json().catch(function () { return null; });
            throw new Error(detail && detail.error && detail.error.message || response.statusText);
        }
        return response.status === 204 ? null : response.json();
    }
    function render() {
        text.textContent = review ? review.text : '';
        text.hidden = !review;
        form.hidden = true; confirm.hidden = true; actions.hidden = false;
        remove.hidden = !review;
        edit.textContent = review ? root.dataset.edit : root.dataset.add;
    }
    async function load() {
        clearError(); busy(true); retry.hidden = true;
        try { var data = await request('GET'); review = data.review; render(); status.textContent = ''; }
        catch (failure) { status.textContent = ''; showError(root.dataset.loadError); retry.hidden = false; }
        finally { busy(false); }
    }
    edit.addEventListener('click', function () {
        clearError(); draft.value = review ? review.text : ''; actions.hidden = true;
        text.hidden = true; form.hidden = false; draft.focus();
    });
    document.getElementById('private-review-cancel').addEventListener('click', function () { clearError(); render(); edit.focus(); });
    form.addEventListener('submit', async function (event) {
        event.preventDefault(); if (!draft.value.trim()) return;
        if (Array.from(draft.value).length > 10000) { showError(root.dataset.tooLong); draft.setAttribute('aria-invalid', 'true'); return; }
        clearError(); status.textContent = ''; draft.removeAttribute('aria-invalid'); busy(true);
        try { var data = await request('PUT', { text: draft.value }); review = data.review; render(); status.textContent = root.dataset.saved; }
        catch (failure) { showError(failure.message || root.dataset.saveError); }
        finally { busy(false); if (form.hidden) edit.focus(); }
    });
    remove.addEventListener('click', function () { clearError(); actions.hidden = true; confirm.hidden = false; document.getElementById('private-review-delete-cancel').focus(); });
    document.getElementById('private-review-delete-cancel').addEventListener('click', function () { confirm.hidden = true; actions.hidden = false; remove.focus(); });
    document.getElementById('private-review-delete-confirm').addEventListener('click', async function () {
        clearError(); status.textContent = ''; draft.removeAttribute('aria-invalid'); busy(true);
        try { await request('DELETE'); review = null; render(); status.textContent = root.dataset.deleted; }
        catch (failure) { showError(failure.message || root.dataset.deleteError); }
        finally { busy(false); if (confirm.hidden) document.getElementById('private-review-heading').focus(); }
    });
    retry.addEventListener('click', load);
    load();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize);
  else initialize();
})();
