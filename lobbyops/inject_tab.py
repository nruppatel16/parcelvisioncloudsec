#!/usr/bin/env python3
"""
Injects the 1Valet auto-listener into a Chrome tab via Chrome DevTools Protocol.

Chrome must be launched with --remote-debugging-port=9222:
  macOS:   open -a "Google Chrome" --args --remote-debugging-port=9222
  Linux:   google-chrome --remote-debugging-port=9222 &
  Windows: chrome.exe --remote-debugging-port=9222

Usage: python inject_tab.py <server_url> [building]
  building: G1 or G2 (default G2)
"""

import sys
import json
import os
import time
import urllib.request

CDP_PORT        = 9222
TAB_INDEX       = 2
VALID_BUILDINGS = {"G1", "G2"}


def _get_tabs():
    try:
        with urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json/list", timeout=4) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _find_tab(tabs):
    page_tabs = [t for t in tabs if t.get("type") == "page"]

    for t in page_tabs:
        if "1valetbas" in t.get("url", "").lower():
            return t, "1Valet URL match"

    if TAB_INDEX < len(page_tabs):
        return page_tabs[TAB_INDEX], f"tab index {TAB_INDEX}"

    if page_tabs:
        return page_tabs[0], "first available tab"

    return None, "no tabs found"


def _inject(tab, js):
    try:
        import websocket
    except ImportError:
        print("websocket-client not installed: pip3 install websocket-client")
        sys.exit(1)

    ws_url = tab.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError(
            "Tab has no webSocketDebuggerUrl -- close any open DevTools window on this tab."
        )

    ws = websocket.create_connection(ws_url, timeout=10)
    ws.send(json.dumps({
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {"expression": js, "returnByValue": False},
    }))
    result = json.loads(ws.recv())
    ws.close()
    return result


def _load_script(server_url: str, building: str) -> str:
    script_path = os.path.join(os.path.dirname(__file__), "smartlockerscript.js")
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"Script not found: {script_path}")
    with open(script_path) as f:
        js = f.read()
    js = js.replace("__SERVER_URL__", server_url)
    js = js.replace("__BUILDING__", building)
    return js


def _launch_chrome_with_debug():
    import subprocess
    import platform
    plat = platform.system()
    try:
        if plat == "Darwin":
            subprocess.Popen(
                ["open", "-a", "Google Chrome", "--args", f"--remote-debugging-port={CDP_PORT}"]
            )
        elif plat == "Windows":
            paths = [
                os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            ]
            for path in paths:
                if os.path.exists(path):
                    subprocess.Popen([path, f"--remote-debugging-port={CDP_PORT}"])
                    return True
            return False
        elif plat == "Linux":
            for exe in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
                try:
                    subprocess.Popen([exe, f"--remote-debugging-port={CDP_PORT}"])
                    break
                except FileNotFoundError:
                    continue
        return True
    except Exception:
        return False


def main():
    server_url = (
        sys.argv[1] if len(sys.argv) > 1 else os.getenv("SERVER_URL", "http://localhost:5002")
    ).rstrip("/")
    building = (
        sys.argv[2] if len(sys.argv) > 2 else os.getenv("BUILDING", "G2")
    ).upper()

    if building not in VALID_BUILDINGS:
        print(f"Invalid building '{building}'. Valid values: {', '.join(sorted(VALID_BUILDINGS))}")
        sys.exit(1)

    tabs = _get_tabs()

    if tabs is None:
        print(f"Chrome CDP not reachable on port {CDP_PORT}. Attempting to launch Chrome...")
        _launch_chrome_with_debug()
        for _ in range(10):
            time.sleep(1)
            tabs = _get_tabs()
            if tabs is not None:
                break

    if tabs is None:
        print(f"Cannot reach Chrome on port {CDP_PORT}.")
        print("Launch Chrome with --remote-debugging-port=9222 (Chrome must be fully closed first).")
        sys.exit(1)

    tab, reason = _find_tab(tabs)
    if not tab:
        print("No page tabs found in Chrome.")
        sys.exit(1)

    print(f"Target ({reason}): [{tab.get('title', '?')[:55]}]")
    print(f"URL: {tab.get('url', '?')[:75]}")
    print(f"Building: {building}")

    js = _load_script(server_url, building)
    result = _inject(tab, js)

    err = result.get("result", {}).get("exceptionDetails") or result.get("error")
    if err:
        print(f"Injection error: {err}")
        return

    print(f"SERVER_URL injected: {server_url}")

    verify = _inject(tab, "typeof window.startValetListener === 'function'")
    confirmed = verify.get("result", {}).get("result", {}).get("value", False)
    if not confirmed:
        print("startValetListener not found -- injection may have failed.")
        return

    print("Script confirmed on window.")

    popup_check = _inject(tab, """
        (function() {
            var inputs = Array.prototype.slice.call(document.querySelectorAll('input'));
            var found = inputs.find(function(inp) {
                return inp.placeholder &&
                       inp.placeholder.toLowerCase().includes('suite') &&
                       inp.offsetParent !== null;
            });
            return !!found;
        })()
    """)
    popup_open = popup_check.get("result", {}).get("result", {}).get("value", False)
    if popup_open:
        print("ADD DELIVERY popup detected. Listener starts automatically.")
    else:
        print("ADD DELIVERY popup is not open. Open it now -- listener auto-starts in 3s.")
        print("Or call startValetListener() from the console.")

    print("Console: valetStatus() | startValetListener() | stopValetListener() | valetClearCache()")


if __name__ == "__main__":
    main()
