/* Personal ratings use the same authenticated API in Classic and New UI. */
(function () {
    'use strict';
    function initialize() {
        var root = document.getElementById('personal-rating');
        if (!root || root.dataset.initialized) return;
        root.dataset.initialized = 'true';
        var form = document.getElementById('personal-rating-form');
        var select = document.getElementById('personal-rating-score');
        var save = form.querySelector('button');
        var error = document.getElementById('personal-rating-error');
        var status = document.getElementById('personal-rating-status');
        var retry = document.getElementById('personal-rating-retry');
        var average = document.getElementById('personal-rating-average');
        function busy(value) { select.disabled = value; save.disabled = value; form.setAttribute('aria-busy', String(value)); }
        function failure(message) { error.hidden = false; error.textContent = message; }
        async function request(method, body) {
            var response = await fetch(root.dataset.url, {
                method: method, credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': form.elements.csrf_token.value },
                body: body === undefined ? undefined : JSON.stringify(body)
            });
            if (!response.ok) throw new Error('Rating request failed');
            return response.json();
        }
        async function load() {
            busy(true); retry.hidden = true; error.hidden = true;
            try {
                var data = await request('GET');
                select.value = String(data.personal_rating || 0);
                average.hidden = data.household_rating == null;
                if (!average.hidden) average.querySelector('span').textContent = String(Math.round(data.household_rating * 5) / 10);
                busy(false); status.textContent = '';
            } catch (_) { failure(root.dataset.loadError); retry.hidden = false; status.textContent = ''; }
        }
        form.addEventListener('submit', async function (event) {
            event.preventDefault(); busy(true); error.hidden = true; status.textContent = '';
            try {
                await request('PUT', { rating: Number(select.value) });
                await load();
                status.textContent = root.dataset.saved;
            } catch (_) { failure(root.dataset.saveError); busy(false); }
        });
        retry.addEventListener('click', load);
        load();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize);
    else initialize();
})();
