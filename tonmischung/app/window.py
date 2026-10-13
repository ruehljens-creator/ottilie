"""Hauptfenster: NSWindow mit WKWebView (PyObjC), lädt die Oberfläche aus `ui/` lokal.

JS → Python: `window.webkit.messageHandlers.tm.postMessage(text)`.
Python → JS: `TM.receive(objekt)` per evaluateJavaScript, immer im Hauptthread.
Schließen versteckt das Fenster nur – Aufträge laufen weiter, die Menüleiste öffnet es wieder.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import objc
from AppKit import (
    NSApp, NSBackingStoreBuffered, NSMakeRect, NSMakeSize, NSViewHeightSizable, NSViewWidthSizable,
    NSWindow, NSWindowStyleMaskClosable, NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled)
from Foundation import NSURL, NSObject
from PyObjCTools import AppHelper
from WebKit import WKWebView, WKWebViewConfiguration

UI_DIR = Path(__file__).resolve().parent / "ui"
log = logging.getLogger("tonmischung.app")


class _ScriptHandler(NSObject, protocols=[objc.protocolNamed("WKScriptMessageHandler")]):
    callback = None

    def userContentController_didReceiveScriptMessage_(self, controller, message):  # noqa: N802
        if self.callback is not None:
            self.callback(str(message.body()))


class _WindowDelegate(NSObject, protocols=[objc.protocolNamed("NSWindowDelegate")]):
    def windowShouldClose_(self, window):  # noqa: N802
        window.orderOut_(None)               # nur verstecken
        return False


def _js_done(result, error) -> None:
    if error is not None:
        log.warning("JavaScript-Fehler: %s", error)


class _NavDelegate(NSObject, protocols=[objc.protocolNamed("WKNavigationDelegate")]):
    def webView_didFinishNavigation_(self, web, nav):  # noqa: N802
        log.debug("Oberfläche geladen")

    def webView_didFailProvisionalNavigation_withError_(self, web, nav, error):  # noqa: N802
        log.warning("Oberfläche nicht geladen: %s", error)

    def webView_didFailNavigation_withError_(self, web, nav, error):  # noqa: N802
        log.warning("Oberfläche gescheitert: %s", error)


class MainWindow:
    def __init__(self, on_message: Callable[[str], None], title: str = "Ottilie", fragment: str = "") -> None:
        cfg = WKWebViewConfiguration.alloc().init()
        self._handler = _ScriptHandler.alloc().init()
        self._handler.callback = on_message
        cfg.userContentController().addScriptMessageHandler_name_(self._handler, "tm")
        rect = NSMakeRect(0, 0, 1440, 900)          # wie der Prototyp
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
                 | NSWindowStyleMaskResizable)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False)
        self.window.setTitle_(title)
        self.window.setMinSize_(NSMakeSize(1100, 700))
        self.window.setReleasedWhenClosed_(False)
        self._delegate = _WindowDelegate.alloc().init()
        self.window.setDelegate_(self._delegate)
        self.web = WKWebView.alloc().initWithFrame_configuration_(rect, cfg)
        self.web.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self._nav = _NavDelegate.alloc().init()
        self.web.setNavigationDelegate_(self._nav)
        self.window.setContentView_(self.web)
        self.window.center()
        index = NSURL.fileURLWithPath_(str(UI_DIR / "index.html"))
        if fragment:                                  # nur Selbsttest (#auto=…)
            index = NSURL.URLWithString_relativeToURL_("#" + fragment, index)
        self.web.loadFileURL_allowingReadAccessToURL_(index, NSURL.fileURLWithPath_(str(UI_DIR)))

    def show(self) -> None:
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    def send(self, payload: dict) -> None:
        """An das Fenster schicken – aus jedem Thread."""
        js = "window.TM && TM.receive(" + json.dumps(payload) + ")"
        log.debug("Kern → Fenster: %s", js[:200])
        AppHelper.callAfter(self.web.evaluateJavaScript_completionHandler_, js, _js_done)


class _MenuActions(NSObject):
    """Ziele der Menüpunkte „Darstellung“ (Zoom, Neu laden), „Fenster“ und „Ottilie beenden“."""
    window = None
    quit = None
    zoom = 1.0

    def _apply(self):
        web = self.window.web
        if hasattr(web, "setPageZoom_"):
            web.setPageZoom_(self.zoom)
        else:                                            # vor macOS 11: CSS-Zoom
            web.evaluateJavaScript_completionHandler_(
                f"document.documentElement.style.zoom = {self.zoom}", None)

    def zoomIn_(self, sender):  # noqa: N802
        self.zoom = min(3.0, round(self.zoom + 0.1, 2))
        self._apply()

    def zoomOut_(self, sender):  # noqa: N802
        self.zoom = max(0.5, round(self.zoom - 0.1, 2))
        self._apply()

    def zoomReset_(self, sender):  # noqa: N802
        self.zoom = 1.0
        self._apply()

    def reloadPage_(self, sender):  # noqa: N802
        self.window.web.reload()

    def showWindow_(self, sender):  # noqa: N802
        self.window.show()

    def quitApp_(self, sender):  # noqa: N802
        self.quit(sender)


def install_main_menu(window: "MainWindow", quit: Callable[[Any], None] | None = None) -> list[str]:
    """Hauptmenü wie bei Mac-Apps üblich – ohne „Bearbeiten“ gehen Kopieren/Einsetzen im
    Suchfeld nicht, ohne „Darstellung“ kein Zoom (Barrierefreiheit). Gibt die Menütitel zurück."""
    from AppKit import NSEventModifierFlagCommand, NSEventModifierFlagShift, NSMenu, NSMenuItem
    actions = _MenuActions.alloc().init()
    actions.window = window
    actions.quit = quit
    window._menu_actions = actions                      # Referenz halten
    main = NSMenu.alloc().init()

    def submenu(title, items):
        top = NSMenuItem.alloc().init()
        menu = NSMenu.alloc().initWithTitle_(title)
        for entry in items:
            if entry is None:
                menu.addItem_(NSMenuItem.separatorItem())
                continue
            label, action, key, *rest = entry
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, action, key)
            if rest and rest[0] == "shift":
                mi.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand | NSEventModifierFlagShift)
            if action in ("zoomIn:", "zoomOut:", "zoomReset:", "reloadPage:", "showWindow:", "quitApp:"):
                mi.setTarget_(actions)
            menu.addItem_(mi)
        top.setSubmenu_(menu)
        main.addItem_(top)

    submenu("Ottilie", [("Ottilie ausblenden", "hide:", "h"), None,
                        ("Ottilie beenden", "quitApp:" if quit else "terminate:", "q")])
    submenu("Bearbeiten", [("Widerrufen", "undo:", "z"), ("Wiederholen", "redo:", "z", "shift"), None,
                           ("Ausschneiden", "cut:", "x"), ("Kopieren", "copy:", "c"), ("Einsetzen", "paste:", "v"),
                           ("Alles auswählen", "selectAll:", "a")])
    submenu("Darstellung", [("Neu laden", "reloadPage:", "r"), None, ("Größer", "zoomIn:", "+"),
                            ("Kleiner", "zoomOut:", "-"), ("Originalgröße", "zoomReset:", "0")])
    submenu("Fenster", [("Ottilie", "showWindow:", "1"), ("Minimieren", "performMiniaturize:", "m"),
                        ("Schließen", "performClose:", "w")])
    NSApp.setMainMenu_(main)
    return [main.itemAtIndex_(i).submenu().title() for i in range(main.numberOfItems())]
