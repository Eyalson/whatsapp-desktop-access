# -*- coding: UTF-8 -*-
# WhatsApp Desktop Access - focus diagnostics add-on.
# Copyright (C) 2026 Sontech - Eyal Sonsino
# This file is covered by the GNU General Public License, version 2.
# See the file LICENSE for details.
"""WhatsApp focus diagnostics: an NVDA global plugin.

Original work, written from NVDA's public developer API only.

Purpose: find out where unexpected focus changes come from while typing in
WhatsApp Desktop. The plugin writes one log line per focus event, with the
object's role, state, window, process and its ancestors, so that "phantom"
focus events can be traced to their source.

Privacy: message text is never written. Names are written only for control
roles (buttons, menus, edit fields...) and are cut short; for content roles
only the length is written. Typed characters are counted, never logged.
"""

import addonHandler
import api
import controlTypes
import globalPluginHandler
import ui
from logHandler import log
from scriptHandler import script

addonHandler.initTranslation()

_TAG = "WA-DIAG"
_MAX_ANCESTORS = 8
# 18 characters: enough for "Type a message to " and short labels, never reaches a phone number.
_NAME_LIMIT = 18
_MAX_DEV_INFO_LINES = 14
# Typed-character lines written after each focus change (the rest are counted).
_TYPED_LINES_PER_FOCUS = 3

_NAMED_ROLE_NAMES = (
	"BUTTON",
	"TOGGLEBUTTON",
	"MENU",
	"MENUITEM",
	"MENUBAR",
	"TAB",
	"TABCONTROL",
	"EDITABLETEXT",
	"TOOLBAR",
	"DIALOG",
	"WINDOW",
	"PANE",
	"DOCUMENT",
	"CHECKBOX",
	"COMBOBOX",
	"LINK",
)
_NAMED_ROLES = frozenset(
	getattr(controlTypes.Role, roleName)
	for roleName in _NAMED_ROLE_NAMES
	if hasattr(controlTypes.Role, roleName)
)


def _safe(getter, default=None):
	"""Run getter(); return default if the accessibility object has gone stale."""
	try:
		return getter()
	except Exception:
		return default


def _roleName(obj):
	role = _safe(lambda: obj.role)
	return getattr(role, "name", str(role))


def _nameText(obj):
	"""The object's name, shortened, and only for control roles."""
	name = _safe(lambda: obj.name) or ""
	if not name:
		return "''"
	if _safe(lambda: obj.role) in _NAMED_ROLES:
		return repr(name[:_NAME_LIMIT])
	return "<%d chars>" % len(name)


def _statesText(obj):
	states = _safe(lambda: obj.states) or ()
	return ",".join(sorted(getattr(state, "name", str(state)) for state in states))


def _describe(obj):
	handle = _safe(lambda: obj.windowHandle)
	return "role=%s name=%s states=[%s] hwnd=%s cls=%s pid=%s app=%s" % (
		_roleName(obj),
		_nameText(obj),
		_statesText(obj),
		hex(handle) if isinstance(handle, int) else "?",
		_safe(lambda: obj.windowClassName, "?"),
		_safe(lambda: obj.processID, "?"),
		_safe(lambda: obj.appModule.appName, "?"),
	)


def _ancestors(obj):
	found = []
	node = _safe(lambda: obj.parent)
	while node is not None and len(found) < _MAX_ANCESTORS:
		found.append(node)
		node = _safe(lambda current=node: current.parent)
	return found


def _isEditable(obj):
	states = _safe(lambda: obj.states) or ()
	if controlTypes.State.READONLY in states:
		return False
	return _safe(lambda: obj.role) == controlTypes.Role.EDITABLETEXT or controlTypes.State.EDITABLE in states


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	# Translators: The category under which this add-on's commands appear in Input Gestures.
	scriptCategory = _("WhatsApp focus diagnostics")

	def __init__(self):
		super().__init__()
		self._enabled = True
		self._focusCount = 0
		self._typedSinceFocus = 0
		self._markCount = 0
		log.info("%s started" % _TAG)

	def terminate(self):
		log.info("%s stopped" % _TAG)
		super().terminate()

	# Events -----------------------------------------------------------------

	def event_gainFocus(self, obj, nextHandler):
		if self._enabled:
			try:
				self._recordFocus(obj)
			except Exception:
				log.debugWarning("%s could not record a focus event" % _TAG, exc_info=True)
		nextHandler()

	def event_foreground(self, obj, nextHandler):
		if self._enabled:
			try:
				log.info("%s foreground %s" % (_TAG, _describe(obj)))
			except Exception:
				log.debugWarning("%s could not record a foreground event" % _TAG, exc_info=True)
		nextHandler()

	def event_typedCharacter(self, obj, nextHandler, ch):
		if self._enabled:
			try:
				self._typedSinceFocus += 1
				if self._typedSinceFocus <= _TYPED_LINES_PER_FOCUS:
					log.info(
						"%s typed #%d afterFocus=#%d editable=%s role=%s name=%s"
						% (
							_TAG,
							self._typedSinceFocus,
							self._focusCount,
							_isEditable(obj),
							_roleName(obj),
							_nameText(obj),
						),
					)
			except Exception:
				log.debugWarning("%s could not record a typed character" % _TAG, exc_info=True)
		nextHandler()

	def _recordFocus(self, obj):
		self._focusCount += 1
		self._typedSinceFocus = 0
		ancestors = _ancestors(obj)
		inDocument = _roleName(obj) == "DOCUMENT" or any(_roleName(a) == "DOCUMENT" for a in ancestors)
		chain = " < ".join("%s:%s" % (_roleName(a), _nameText(a)) for a in ancestors)
		log.info(
			"%s focus #%d %s inDoc=%s editable=%s chain=[%s]"
			% (_TAG, self._focusCount, _describe(obj), inDocument, _isEditable(obj), chain),
		)
		# Full developer info only for the suspicious kind: unnamed and outside any web document.
		if not inDocument and not (_safe(lambda: obj.name) or ""):
			devInfo = _safe(lambda: obj.devInfo) or []
			text = " | ".join(str(line).replace("\n", " ") for line in devInfo[:_MAX_DEV_INFO_LINES])
			log.info("%s focus #%d devInfo: %s" % (_TAG, self._focusCount, text))

	# Commands ---------------------------------------------------------------

	@script(
		# Translators: Description of a command that writes a marker line to the NVDA log.
		description=_("Write a marker line to the log"),
		gesture="kb:NVDA+shift+f9",
	)
	def script_mark(self, gesture):
		self._markCount += 1
		log.info("%s MARK #%d" % (_TAG, self._markCount))
		# Translators: Spoken after a marker line was written.
		ui.message(_("Marker written"))

	@script(
		# Translators: Description of a command that turns the diagnostic logging on or off.
		description=_("Toggle focus diagnostics logging"),
		gesture="kb:NVDA+shift+f10",
	)
	def script_toggle(self, gesture):
		self._enabled = not self._enabled
		log.info("%s logging %s" % (_TAG, "on" if self._enabled else "off"))
		if self._enabled:
			# Translators: Spoken when diagnostic logging is turned on.
			ui.message(_("Focus diagnostics on"))
		else:
			# Translators: Spoken when diagnostic logging is turned off.
			ui.message(_("Focus diagnostics off"))
