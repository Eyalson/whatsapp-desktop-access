# Changelog

All notable changes to WhatsApp Desktop Access are listed here, newest first.
Versions follow [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-10-12

First version for the NVDA add-on store.

### Added

- **Alt+1** moves to the chat list: the chat you were on, or the first chat.
- **Alt+2** moves to the newest message in the open chat.
- **Alt+D** moves to the message field.
- Each command says so when there is nothing to move to, for example when no chat is open.
- The commands can be given other keys in NVDA's Input Gestures dialog, under "WhatsApp Desktop Access".
- The commands find their targets by role, not by WhatsApp's CSS class names, so they work in every WhatsApp language and keep working when WhatsApp changes its page markup.
- Hebrew translation of the command names and messages.
- Add-on documentation, available from the add-on store, and this changelog.

### Changed

- The navigation commands remember what they found, so repeated use is instant. The first Alt+2 in a chat still takes about a second.

### Performance

- Focus changes in WhatsApp are not slowed down by the add-on. Typing echo, Tab and the arrow keys measure the same as in other applications.

## [0.1.0] - 2026-10-12

First public version. It was published on GitHub only and was not submitted to the add-on store.

### Fixed

- NVDA no longer loses the message field after you select and delete text. Before, NVDA announced "menu" and then "pane", and stopped echoing typed characters and reporting the caret until you pressed Tab and Shift+Tab to get back into the field.
