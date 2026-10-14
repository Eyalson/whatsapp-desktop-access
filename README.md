# WhatsApp Desktop Access

An NVDA add-on that improves the accessibility of WhatsApp Desktop for Windows: the Microsoft Store app built on WinUI 3 and WebView2.

## What it does

### Keyboard commands

| Keys | Action |
| --- | --- |
| Alt+1 | Move to the chat list: the open chat, or the first chat |
| Alt+2 | Move to the message list: the newest message |
| Alt+D | Move to the message field |

These keys are not used by WhatsApp itself. The targets are found by role and state, never by CSS class names, so the commands work in every WhatsApp language and survive page changes. If a target is missing, for example when no chat is open, NVDA says so. If the focus is already on the target, NVDA announces it again (WhatsApp often puts the focus in the message field by itself when a chat opens). Other keys can be assigned in NVDA's Input Gestures dialog, under "WhatsApp Desktop Access".

### Mouse

NVDA reads what is under the mouse: chats, messages and buttons in the page, "WhatsApp" over the title bar, and Minimize, Maximize or Close over the window buttons.

When the mouse enters a control (a button, link, text field, check box, slider, tab or menu item), NVDA announces it the way it announces a focus change, for example "Play voice message, button" or "Type a message to …, edit", and then reads the text under the mouse, if there is any.

Why it was needed: WebView2's own window lies at the bottom of the window order, so Windows reports WinUI's windows under the mouse instead of the page: a transparent title bar window named "Non Client Input Sink Window", and the nameless pane that hosts the page. The add-on uses NVDA's `objectFromPointRedirect` to hit-test the page itself. Chromium answers a hit test at a new point with the page and finds the element in the background, so NVDA checks once more after 50 ms. The work runs only while the mouse moves over WhatsApp.

### Keeping the focus on the message field

It keeps NVDA on the message field while you type.

Without it, NVDA can lose the message field after you select and delete text. NVDA says "menu", then "pane", and stops echoing typed characters and reporting the caret until you Tab away and back.

How the add-on prevents this:

- WhatsApp shows a small popup with role "menu" next to selected text. NVDA used to give it the focus, although the real focus stays in the message field. The add-on ignores that popup while an edit field in the page has focus.
- When the popup closes, NVDA asked Windows where the focus is and landed on the WinUI pane that hosts the page (`Microsoft.UI.Content.DesktopChildSiteBridge`). The add-on sends that focus back to the element that really has it inside the page. If it is the element NVDA already had, nothing is announced again.

The add-on builds on NVDA's own WhatsApp support, which turns browse mode off by default.

Background: [nvaccess/nvda#19772](https://github.com/nvaccess/nvda/issues/19772). The same approach was used in NVDA for Microsoft Teams in [nvaccess/nvda#11822](https://github.com/nvaccess/nvda/pull/11822).

## Requirements

- NVDA 2026.1 or later. Tested with NVDA 2026.2.
- WhatsApp Desktop from the Microsoft Store on Windows 11. Tested with version 2.2639.100.0.

## Installation

Download the `.nvda-addon` file from [Releases](https://github.com/Eyalson/whatsapp-desktop-access/releases) and open it. NVDA asks to install it and then to restart.

NVDA loads only one WhatsApp app module. If you have another WhatsApp add-on that includes one, such as WhatsApp NG or WhatsApp Desktop Enhancer, disable it while you use this add-on.

## Status and plans

The changes of every version are listed in [CHANGELOG.md](CHANGELOG.md).

Version 1.1 fixes the focus problem, reads what is under the mouse and adds the navigation commands. Planned next: reading whole messages, message actions such as playing a voice message from the keyboard, and fewer repeated announcements.

## Focus diagnostics add-on

The `diag-addon` folder holds a separate developer tool, "WhatsApp focus diagnostics". It was used to find the cause of the focus problem.

**Install it only while diagnosing.** It runs on every focus event in every application, and measurements showed that it roughly doubles the time focus changes take. Disable it afterwards.

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

## Contact and reporting problems

Written and maintained by Eyal Sonsino, [Sontech](https://sontech.co.il/en/). You do not need a GitHub account to reach the author: use the contact form in [English](https://sontech.co.il/en/contact/) or in [Hebrew](https://sontech.co.il/contact/).

For bugs and suggestions you can also open an issue in this repository. Please include your NVDA version, your WhatsApp version and, if you can, the NVDA log.

## License

Copyright (C) 2026 Sontech - Eyal Sonsino.

This add-on is covered by the GNU General Public License, version 2. See [LICENSE](LICENSE).
