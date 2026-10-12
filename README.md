# WhatsApp Desktop Access

An NVDA add-on that improves the accessibility of WhatsApp Desktop for Windows: the Microsoft Store app built on WinUI 3 and WebView2.

## What it does (version 0.1.0)

It keeps NVDA on the message field while you type.

Without it, NVDA can lose the message field after you select and delete text. NVDA says "menu", then "pane", and stops echoing typed characters and reporting the caret until you Tab away and back.

How the add-on prevents this:

- WhatsApp shows a small popup with role "menu" next to selected text. NVDA used to give it the focus, although the real focus stays in the message field. The add-on ignores that popup while an edit field in the page has focus.
- When the popup closes, NVDA asked Windows where the focus is and landed on the WinUI pane that hosts the page (`Microsoft.UI.Content.DesktopChildSiteBridge`). The add-on sends that focus back to the element that really has it inside the page. If it is the element NVDA already had, nothing is announced again.

The add-on builds on NVDA's own WhatsApp support, which turns browse mode off by default.

Background: [nvaccess/nvda#19772](https://github.com/nvaccess/nvda/issues/19772). The same approach was used in NVDA for Microsoft Teams in [nvaccess/nvda#11822](https://github.com/nvaccess/nvda/pull/11822).

## Requirements

- NVDA 2024.1 or later. Tested with NVDA 2026.2.
- WhatsApp Desktop from the Microsoft Store on Windows 11. Tested with version 2.2638.102.0.

## Installation

Download the `.nvda-addon` file from [Releases](https://github.com/Eyalson/whatsapp-desktop-access/releases) and open it. NVDA asks to install it and then to restart.

NVDA loads only one WhatsApp app module. If you have another WhatsApp add-on that includes one, such as WhatsApp NG or WhatsApp Desktop Enhancer, disable it while you use this add-on.

## Status and plans

This is an early version that fixes the focus problem only. Planned next: navigation shortcuts, message reading, fewer repeated announcements, and reading under the mouse pointer.

## Focus diagnostics add-on

The `diag-addon` folder holds a separate developer tool, "WhatsApp focus diagnostics". It was used to find the cause of the focus problem.

- It writes one line to the NVDA log for each focus event: role, states, window, process and ancestors.
- It never writes message text. Names are written only for controls such as buttons and edit fields, and are cut short. Typed characters are counted, never written.
- NVDA+Shift+F9 writes a marker line. NVDA+Shift+F10 turns logging on or off.
- The NVDA log level must be "info" or more detailed.

## Building

You need Python 3.

```
python build.py              # builds whatsappDesktopAccess-<version>.nvda-addon
python build.py diag-addon   # builds whatsappFocusDiag-<version>.nvda-addon
```

## Reporting problems

Please open an issue in this repository. Include your NVDA version, your WhatsApp version and, if you can, the NVDA log.

## License

Copyright (C) 2026 Sontech - Eyal Sonsino.

This add-on is covered by the GNU General Public License, version 2. See [LICENSE](LICENSE).
