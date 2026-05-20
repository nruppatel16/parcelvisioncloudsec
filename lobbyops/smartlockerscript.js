/**
 * 1Valet auto-listener -- injected by inject_tab.py via Chrome DevTools Protocol.
 * SERVER_URL and BUILDING are filled at injection time. Do not edit manually.
 */
(function () {
    'use strict';

    const SERVER_URL = "__SERVER_URL__";
    const BUILDING   = "__BUILDING__";

    const CONFIG = { pollInterval: 5000, autoStart: true };
    const state  = {
        isRunning:      false,
        pollTimer:      null,
        processedUnits: new Set(),
        isProcessing:   false,
    };

    function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

    function headers(extra) {
        const h = { 'Content-Type': 'application/json', ...extra };
        if (SERVER_URL.includes('ngrok') || SERVER_URL.includes('trycloudflare')) {
            h['ngrok-skip-browser-warning'] = 'true';
        }
        return h;
    }

    async function addUnit(unitNumber) {
        const retryDelays = [2000, 4000, 8000];

        for (let attempt = 0; attempt <= retryDelays.length; attempt++) {
            try {
                const input = [...document.querySelectorAll('input')].find(
                    inp => inp.placeholder &&
                           inp.placeholder.toLowerCase().includes('suite') &&
                           inp.offsetParent !== null
                );
                if (!input) {
                    console.error('Input field not found -- is the popup still open?');
                    return false;
                }

                input.value = '';
                input.focus();
                for (const char of unitNumber) {
                    input.value += char;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    await sleep(50);
                }
                input.dispatchEvent(new Event('change', { bubbles: true }));
                await sleep(800);

                const match = [...document.querySelectorAll('div, li, span')].find(el => {
                    const t = el.textContent.trim();
                    return t === unitNumber &&
                           el.offsetParent !== null &&
                           el.getBoundingClientRect().height > 10;
                });

                if (match) {
                    match.click();
                    console.log('Unit selected: ' + unitNumber);
                } else {
                    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, bubbles: true }));
                    await sleep(300);
                    console.log('Enter dispatched for: ' + unitNumber);
                }

                await sleep(1000);
                const refocus = [...document.querySelectorAll('input')].find(
                    inp => inp.placeholder &&
                           inp.placeholder.toLowerCase().includes('suite') &&
                           inp.offsetParent !== null
                );
                if (refocus) { refocus.focus(); }
                return true;

            } catch (err) {
                if (attempt < retryDelays.length) {
                    console.warn('addUnit attempt ' + (attempt + 1) + ' failed: ' + err.message +
                                 '. Retrying in ' + (retryDelays[attempt] / 1000) + 's');
                    await sleep(retryDelays[attempt]);
                } else {
                    console.error('addUnit failed after ' + (retryDelays.length + 1) + ' attempts. Flagging unit.');
                    fetch(SERVER_URL + '/valet/flag', {
                        method: 'POST',
                        headers: headers(),
                        body: JSON.stringify({ unit: unitNumber, building: BUILDING }),
                    }).catch(e => console.error('flag request failed: ' + e.message));
                    return false;
                }
            }
        }
        return false;
    }

    async function checkForPendingUnits() {
        if (state.isProcessing) return;
        try {
            const res = await fetch(SERVER_URL + '/valet/pending?building=' + BUILDING, {
                headers: headers(),
            });
            if (!res.ok) return;
            const data = await res.json();
            if (data.status === 'empty' || data.units.length === 0) return;

            console.log(data.count + ' pending unit(s) for building ' + BUILDING);
            const unitData = data.units[0];
            const unit = unitData.unit;

            if (state.processedUnits.has(unit)) {
                console.log('Skipping ' + unit + ' (already processed this session)');
                await fetch(SERVER_URL + '/valet/complete', {
                    method: 'POST',
                    headers: headers(),
                    body: JSON.stringify({ unit, success: true, building: BUILDING }),
                });
                return;
            }

            console.log('Processing: ' + unit + ' (' + (unitData.name || 'unknown') + ')');
            state.isProcessing = true;
            const success = await addUnit(unit);
            if (success) {
                state.processedUnits.add(unit);
                await fetch(SERVER_URL + '/valet/complete', {
                    method: 'POST',
                    headers: headers(),
                    body: JSON.stringify({ unit, success: true, building: BUILDING }),
                });
                console.log('Complete: ' + unit);
            }
            state.isProcessing = false;
        } catch (err) {
            console.error('Polling error: ' + err.message);
            state.isProcessing = false;
        }
    }

    function startListener() {
        if (state.isRunning) {
            console.log('Listener already running.');
            return;
        }
        const input = [...document.querySelectorAll('input')].find(
            inp => inp.placeholder && inp.placeholder.toLowerCase().includes('suite')
        );
        if (!input) {
            console.error('ADD DELIVERY popup not open. Open it first, then call startValetListener().');
            return;
        }
        state.isRunning = true;
        console.log('1Valet auto-listener started | building: ' + BUILDING +
                    ' | server: ' + SERVER_URL + ' | poll: ' + (CONFIG.pollInterval / 1000) + 's');
        state.pollTimer = setInterval(checkForPendingUnits, CONFIG.pollInterval);
        checkForPendingUnits();
    }

    function stopListener() {
        if (!state.isRunning) { console.log('Listener not running.'); return; }
        clearInterval(state.pollTimer);
        state.isRunning = false;
        console.log('Listener stopped.');
    }

    function checkStatus() {
        console.log('Running:    ' + state.isRunning);
        console.log('Processing: ' + state.isProcessing);
        console.log('Processed:  ' + ([...state.processedUnits].join(', ') || 'none'));
        console.log('Server:     ' + SERVER_URL);
        console.log('Building:   ' + BUILDING);
    }

    function clearCache() {
        state.processedUnits.clear();
        console.log('Session cache cleared.');
    }

    window.startValetListener = startListener;
    window.stopValetListener  = stopListener;
    window.valetStatus        = checkStatus;
    window.valetClearCache    = clearCache;
    window.addUnit            = addUnit;

    console.log('1Valet auto-listener injected | building: ' + BUILDING + ' | server: ' + SERVER_URL);
    console.log('Commands: startValetListener() | stopValetListener() | valetStatus() | valetClearCache()');

    if (CONFIG.autoStart) {
        console.log('Auto-starting in 3s...');
        setTimeout(startListener, 3000);
    }
})();
