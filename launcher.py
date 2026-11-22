"""One program, three doors: the dashboard by default, the terminal app with --tui, and the browser server for itself.

Frozen into one executable, `sys.executable` is this file, so `python -m invisible_playwright_mcp` cannot be started that
way: AIHawk asks this file for `--invisible-mcp` instead.
"""

import os
import sys


def install() -> None:
    """This file copied to the user's programs folder, with a desktop and a start-menu shortcut."""
    import shutil
    from pathlib import Path

    target = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / "Rainy"
    target.mkdir(parents=True, exist_ok=True)
    exe = target / "Rainy.exe"
    source = Path(sys.executable).parent
    if source.resolve() != target.resolve():
        if (source / "_internal").is_dir():  # a folder build: the program is the folder, not one file
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(sys.executable, exe)

    links = [Path.home() / "Desktop" / "Rainy.lnk"]
    start_menu = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    if start_menu.is_dir():
        links.append(start_menu / "Rainy.lnk")
    try:
        from win32com.client import Dispatch

        shell = Dispatch("WScript.Shell")
        for link in links:
            shortcut = shell.CreateShortcut(str(link))
            shortcut.TargetPath = str(exe)
            shortcut.WorkingDirectory = str(target)
            shortcut.Description = "Rainy: job search and applications"
            shortcut.IconLocation = f"{exe},0"
            shortcut.Save()
    except Exception as error:  # noqa: BLE001  the copy is the install; shortcuts are a convenience
        print(f"Installed to {exe}, but the shortcuts could not be made: {error}")
        return
    print(f"Installed to {exe}")
    for link in links:
        print(f"  shortcut: {link}")


def run() -> None:
    if sys.argv[1:2] == ["--install"]:
        install()
        return
    if sys.argv[1:2] == ["--invisible-mcp"]:
        from invisible_playwright_mcp.cli import main as mcp_main

        sys.argv = [sys.argv[0]]
        mcp_main()
        return
    if sys.argv[1:2] == ["--tui"]:
        from aihawk.cli import run as tui

        sys.argv = [sys.argv[0], *sys.argv[2:]]
        tui()
        return
    import threading
    import time
    import urllib.request
    import webbrowser

    import uvicorn

    from dashboard import app

    def open_browser_when_ready() -> None:
        """The page opened once it answers, so a shortcut feels like the app it starts."""
        for _ in range(90):
            try:
                urllib.request.urlopen("http://127.0.0.1:8765/", timeout=1)
            except Exception:  # noqa: BLE001  not up yet
                time.sleep(1)
                continue
            webbrowser.open("http://127.0.0.1:8765")
            return

    print("Rainy dashboard on http://127.0.0.1:8765")
    threading.Thread(target=open_browser_when_ready, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")


if __name__ == "__main__":
    run()
