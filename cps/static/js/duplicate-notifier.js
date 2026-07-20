/* Calibre-Web Automated – Modern Duplicates Notification System
 * Copyright (C) 2024-2025 Calibre-Web Automated contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

(function() {
    'use strict';
    
    const STORAGE_KEY = 'cwa_duplicates_notification_shown';
    const LAST_COUNT_KEY = 'cwa_duplicates_last_count';
    // #1288: the badge polls ONLY while a duplicate scan is actually in flight.
    // A settled instance makes no repeat requests at all — it refreshes on page
    // load and when the tab regains focus. Starting interval is brisk so a scan
    // that finishes quickly still updates the badge promptly; it backs off so a
    // long scan does not sustain that beat.
    const POLL_INTERVAL_MS = 2500;
    const POLL_MAX_INTERVAL_MS = 30000;
    const POLL_BACKOFF_FACTOR = 2;
    const POLL_MAX_ATTEMPTS = 60; // safety stop for a scan that never settles

    let currentDuplicateCount = 0;
    let pollAttempts = 0;
    let pollTimer = null;
    let pollDelayMs = POLL_INTERVAL_MS;
    // True while we are following a scan we have already seen in flight. Used so a
    // transient error retries instead of abandoning the scan, without letting a
    // failed page-load fetch start polling on an otherwise quiet instance (#1288).
    let followingScan = false;
    let lastPreviewSignature = '';
    
    /**
     * Check if notification was already shown in this session
     */
    function wasNotificationShown() {
        return sessionStorage.getItem(STORAGE_KEY) === 'true';
    }
    
    /**
     * Mark notification as shown for this session
     */
    function markNotificationShown() {
        sessionStorage.setItem(STORAGE_KEY, 'true');
    }

    function getLastNotifiedCount() {
        const val = sessionStorage.getItem(LAST_COUNT_KEY);
        const parsed = parseInt(val, 10);
        return Number.isFinite(parsed) ? parsed : 0;
    }

    function setLastNotifiedCount(count) {
        sessionStorage.setItem(LAST_COUNT_KEY, String(count || 0));
    }
    
    /**
     * Update the duplicate count badge in sidebar
     */
    function updateBadge(count) {
        currentDuplicateCount = count;
        const badge = document.getElementById('duplicate-count-badge');
        
        if (badge) {
            if (count > 0) {
                badge.textContent = count > 99 ? '99+' : count;
                badge.style.display = 'inline-block';
            } else {
                badge.style.display = 'none';
            }
        }
    }
    
    /**
     * Fetch duplicate status from API
     */
    function fetchDuplicateStatus() {
        const basePath = (typeof getPath === 'function') ? getPath() : '';
        const statusUrl = basePath + '/duplicates/status';
        return fetch(statusUrl, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json'
            },
            credentials: 'same-origin'
        })
        .then(response => response.json())
        .catch(error => {
            console.error('[CWA Duplicates] Error fetching status:', error);
            return { success: false, count: 0, preview: [], enabled: false };
        });
    }

    /**
     * Queue exactly one follow-up status check. Each response decides whether
     * another is warranted, so there is never a free-running timer to leak or
     * to re-arm itself (#1288).
     */
    function scheduleNextPoll() {
        if (pollTimer) {
            return;
        }
        if (pollAttempts >= POLL_MAX_ATTEMPTS) {
            // Give up on a scan that never settles, but clear the counters as we
            // go: a later page load or tab focus starts a clean episode instead
            // of inheriting an exhausted one.
            stopStatusPolling();
            return;
        }
        const delay = pollDelayMs;
        pollDelayMs = Math.min(pollDelayMs * POLL_BACKOFF_FACTOR, POLL_MAX_INTERVAL_MS);
        pollTimer = setTimeout(() => {
            pollTimer = null;
            pollAttempts += 1;
            fetchDuplicateStatus().then(handleStatusResponse);
        }, delay);
    }

    function stopStatusPolling() {
        if (pollTimer) {
            clearTimeout(pollTimer);
            pollTimer = null;
        }
        pollAttempts = 0;
        pollDelayMs = POLL_INTERVAL_MS;
        followingScan = false;
    }

    function isModalActive() {
        const modal = document.getElementById('duplicate-notification-modal');
        return modal && modal.classList.contains('active');
    }

    function isDuplicatesPage() {
        return window.location.pathname.replace(/\/+$/, '').endsWith('/duplicates');
    }
    
    /**
     * Show the notification modal
     */
    function showNotificationModal(data) {
        const { count, preview } = data;

        if (isModalActive()) {
            return;
        }
        
        const lastCount = getLastNotifiedCount();
        if (wasNotificationShown() && count <= lastCount) {
            return;
        }
        
        // Update count in modal
        const countBadge = document.getElementById('duplicate-notification-count');
        if (countBadge) {
            countBadge.textContent = count;
        }
        
        // Update preview list
        const previewList = document.getElementById('duplicate-notification-preview');
        if (previewList && preview && preview.length > 0) {
            const signature = preview.map(item => `${item.title}|${item.author}|${item.count}`).join('||');
            if (signature !== lastPreviewSignature) {
                lastPreviewSignature = signature;
                previewList.innerHTML = preview.map(item => `
                    <li class="duplicate-preview-item">
                        <strong>${escapeHtml(item.title)}</strong>
                        <small>${escapeHtml(item.author)} - ${item.count} copies</small>
                    </li>
                `).join('');
            }
        }
        
        // Show modal and backdrop
        const modal = document.getElementById('duplicate-notification-modal');
        const backdrop = document.getElementById('duplicate-notification-backdrop');
        
        if (modal && backdrop) {
            // Small delay for smooth animation
            setTimeout(() => {
                backdrop.classList.add('active');
                modal.classList.add('active');

                // Focus trap
                modal.focus();

                // Mark as shown and store count
                markNotificationShown();
                setLastNotifiedCount(count);
            }, 500);
        }
    }

    function handleStatusResponse(data) {
        if (!data || !data.success) {
            // #1288: a blip while following a scan retries (bounded by the attempt
            // cap and the backoff) rather than abandoning the scan — the old
            // free-running interval recovered from these on its own. A failure when
            // we were NOT following a scan leaves the instance quiet, and resets the
            // counters so a half-spent budget can't cap the next scan.
            if (followingScan) {
                scheduleNextPoll();
            } else {
                stopStatusPolling();
            }
            return;
        }

        // #1288: decide on polling BEFORE any early return below, so that showing
        // the modal (or being on the duplicates page) can't drop the chain and
        // leave a running scan unobserved until the tab is reloaded.
        //
        // Keep checking ONLY while a scan is genuinely in flight — `stale`
        // mirrors the server's `scan_pending`.
        //
        // `needs_scan` is deliberately excluded: it means "an admin must trigger
        // a full scan", and the server reports it alongside `stale` in that
        // branch. Polling cannot resolve a state that waits on a human, and
        // treating it as a reason to poll is what kept quiet instances calling
        // /duplicates/status every 2.5s for the life of the tab.
        if (data.stale && !data.needs_scan) {
            followingScan = true;
            scheduleNextPoll();
        } else {
            stopStatusPolling();
        }

        if (!window.CWADuplicateScanActive) {
            updateBadge(data.count);
        }
        document.dispatchEvent(new CustomEvent('cwa:duplicates-status', { detail: data }));

        if (isModalActive()) {
            return;
        }

        if (data.count > 0 && data.enabled && !isDuplicatesPage()) {
            showNotificationModal(data);
            if (isModalActive()) {
                return;
            }
        }

    }
    
    /**
     * Hide the notification modal
     */
    function hideNotificationModal() {
        const modal = document.getElementById('duplicate-notification-modal');
        const backdrop = document.getElementById('duplicate-notification-backdrop');
        
        if (modal && backdrop) {
            modal.classList.remove('active');
            backdrop.classList.remove('active');
        }
    }
    
    /**
     * Escape HTML to prevent XSS
     */
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    /**
     * Initialize event listeners
     */
    function initializeEventListeners() {
        // Close button
        const closeBtn = document.getElementById('duplicate-notification-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', hideNotificationModal);
        }
        
        // Remind me later button
        const remindBtn = document.getElementById('duplicate-notification-remind');
        if (remindBtn) {
            remindBtn.addEventListener('click', hideNotificationModal);
        }
        
        // Click outside to close
        const backdrop = document.getElementById('duplicate-notification-backdrop');
        if (backdrop) {
            backdrop.addEventListener('click', hideNotificationModal);
        }
        
        // Escape key to close
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                hideNotificationModal();
            }
        });
    }
    
    /**
     * Main initialization function
     */
    function init() {
        // Check if user has permission (admin or edit)
        const userHasPermission = document.getElementById('duplicate-notification-modal');
        if (!userHasPermission) {
            return; // Modal not rendered, user doesn't have permission
        }
        
        // Initialize event listeners
        initializeEventListeners();
        
        // Fetch initial status once on page load
        // No periodic updates - badge refreshes after ingest operations only
        const bootstrapData = window.cwaDuplicateBootstrap;
        if (bootstrapData && typeof bootstrapData === 'object') {
            handleStatusResponse({
                success: true,
                enabled: !!bootstrapData.enabled,
                count: Number(bootstrapData.count || 0),
                preview: bootstrapData.preview || [],
                cached: !!bootstrapData.cached,
                stale: !!bootstrapData.stale,
                needs_scan: !!bootstrapData.stale
            });
        }

        // One fetch on load; handleStatusResponse arms a follow-up only if a
        // scan is running (#1288).
        fetchDuplicateStatus().then(handleStatusResponse);

        document.addEventListener('visibilitychange', function() {
            if (!document.hidden) {
                fetchDuplicateStatus().then(handleStatusResponse);
            }
        });
    }
    
    // Expose functions globally for use by other scripts
    window.CWADuplicates = {
        updateBadge: updateBadge,
        fetchStatus: fetchDuplicateStatus,
        hideModal: hideNotificationModal
    };
    
    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    
})();
