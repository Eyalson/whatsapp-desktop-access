# Changelog

All notable changes to WhatsApp Desktop Access are listed here, newest first.
Versions follow [Semantic Versioning](https://semver.org/).

## [1.2.1] - 2026-11-02

### Fixed

- When a message menu opens, NVDA announces its first item and no longer interrupts it with "dialog" and "menu". WhatsApp reports the menu twice, and the second report moved NVDA away from the item.
- When the Copy image menu of the image viewer closes, NVDA returns to the page. Before, it stayed on an empty pane and announced "pane".

## [1.2.0] - 2026-10-29

### Changed

- The add-on can be enabled together with other WhatsApp add-ons. It is now a global plugin instead of an app module. NVDA loads only one app module per program, so before, this add-on replaced the app module of any other WhatsApp add-on, or was replaced by it. Now NVDA's own WhatsApp support, or the app module of another WhatsApp add-on, stays in place, and this add-on adds its fixes and commands to it.
- The add-on checks that an object or the foreground window belongs to WhatsApp before it does anything, so other programs, including WhatsApp Web in a browser, are not affected. Alt+1, Alt+2 and Alt+D reach other programs as before.
- If another WhatsApp add-on also uses Alt+1, Alt+2 or Alt+D, the command of that add-on runs for that key.

### Fixed

- Alt+1 finds the chat list also when another WhatsApp add-on changes the role NVDA reports for the chats, for example as panes. Before, it announced "Chat list not found" in that case, and NVDA could pause for about a second. The add-on now recognizes a chat by the role the WhatsApp page itself reports.
- Requires NVDA 2026.2 or later, the first version with NVDA's own WhatsApp support, which turns browse mode off in WhatsApp.

## [1.1.2] - 2026-10-20

### Performance

- The first Alt+2 in a chat is about four times faster: about 0.3 seconds instead of 1 to 2 seconds on the author's computer. The add-on now searches for the newest message from the end of the page, where the message field and the newest messages are, and stops at the first message it reaches, instead of walking the whole page. Later uses of the command were already immediate.
- If the quick search cannot be trusted, for example when a page element returns an error, the add-on makes the full search as before, so the command still moves to the newest message.

Tested with WhatsApp 2.2639.100.0 and NVDA 2026.2.

## [1.1.0] - 2026-10-14

### Added

- Controls are announced when the mouse enters them. Buttons, links, text fields, check boxes, sliders, tabs and menu items are announced the way NVDA announces a focus change: name, role and state, for example "Play voice message, button" or "Type a message to …, edit". Then NVDA reads the text under the mouse, if there is any. Before, buttons that show only an icon, such as Play on a voice message, and empty text fields were not announced at all.
- Alt+1, Alt+2 and Alt+D announce the target when the focus is already on it. Before, they announced nothing. This happened most often with Alt+D, because WhatsApp puts the focus in the message field when a chat opens.

### Fixed

- Mouse tracking in WhatsApp. NVDA now reads what is under the mouse: chats, messages and buttons in the page, "WhatsApp" over the title bar, and Minimize, Maximize or Close over the window buttons. Without the add-on, NVDA announced "Non Client Input Sink Window" over the title bar and nothing over the rest of the window, because Windows reports WinUI's own windows under the mouse instead of the page.
- Alt+1 finds the first chat even when the chat list does not start with a chat, by searching inside the list.
- When a command finds nothing, the NVDA log now lists the kinds of elements the page had (roles and counts only, never names), so the cause can be found.

### Performance

- The mouse work runs only while the mouse moves over WhatsApp: about 10 to 15 ms for each new element under the mouse on the author's computer, and almost nothing while the mouse stays within one element. Typing, focus changes and the navigation commands are not affected.

Tested with WhatsApp 2.2639.100.0 and NVDA 2026.2.

## [1.0.1] - 2026-10-12

### Added

- Contact details. The add-on help and the homepage link of the add-on lead to the author's website, where there is a contact form in English and in Hebrew, so users do not need a GitHub account to reach the author.

There is no change in behavior.

## [1.0.0] - 2026-10-12

### Added

- **Alt+1** moves to the chat list: the chat you were on, or the first chat.
- **Alt+2** moves to the newest message in the open chat.
- **Alt+D** moves to the message field.
- Each command says so when there is nothing to move to, for example when no chat is open.
- The commands can be given other keys in NVDA's Input Gestures dialog, under "WhatsApp Desktop Access".
- The commands find their targets by role, not by WhatsApp's CSS class names, so they work in every WhatsApp language and keep working when WhatsApp changes its page markup.

## [0.1.0] - 2026-10-12

### Fixed

- NVDA no longer loses the message field after you select and delete text. Before, NVDA announced "menu" and then "pane", and stopped echoing typed characters and reporting the caret until you pressed Tab and Shift+Tab to get back into the field.
