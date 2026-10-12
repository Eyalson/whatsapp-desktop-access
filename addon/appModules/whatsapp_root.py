# -*- coding: UTF-8 -*-
# WhatsApp Desktop Access - an NVDA add-on.
# Copyright (C) 2026 Sontech - Eyal Sonsino
# This file is covered by the GNU General Public License, version 2.
# See the file LICENSE for details.
"""NVDA support for WhatsApp Desktop on Windows (WinUI 3 window hosting a WebView2 page).

Original work, written against NVDA's public developer API.

Focus guard
-----------
Two things make NVDA lose the message field while typing:

1. When text is selected, WhatsApp shows a small popup with role "menu" next to the
   selection. Chromium fires a menu-popup-start event for it and NVDA moves its focus
   to the popup ("menu"), although the real keyboard focus stays in the text field.
2. When that popup closes, NVDA asks Windows where the focus is. Windows answers with
   the WinUI pane that hosts the WebView2 (window class DesktopChildSiteBridge), so NVDA
   stays on that pane ("pane") and stops echoing typed characters.

The guard ignores the popup while an editable field has the focus, and turns focus on
the hosting pane back into the element that really has the focus inside the page. When
that element is the one NVDA already had, the return is silent.

The same pattern was used by NVDA itself for Microsoft Teams (nvaccess/nvda#11822).

Navigation
----------
Alt+1, Alt+2 and Alt+D move the focus to the chat list, the message list and the message
field. The targets are found by role and state, never by CSS class names or child indices,
so they keep working when WhatsApp changes its page markup and in every UI language.

Speed: creating an NVDA object costs several milliseconds when many add-ons are installed,
so walking the page takes about a second. The first command walks the page and remembers
what it found; later commands check that the remembered object is still alive and use it
directly. Finding the newest message needs only a few "last child" steps.
"""

import ctypes
from collections import deque
from ctypes import wintypes
import time

import addonHandler
import api
import appModuleHandler
import braille
import controlTypes
import oleacc
import ui
import vision
import winUser
from logHandler import log
from NVDAObjects import NVDAObject
from NVDAObjects.IAccessible import IAccessible, getNVDAObjectFromEvent
from scriptHandler import script

try:
	# Extend NVDA's own WhatsApp support (it turns browse mode off by default) instead of replacing it.
	from nvdaBuiltin.appModules.whatsapp_root import AppModule as _BaseAppModule
except ImportError:
	# NVDA versions released before the built-in WhatsApp app module.
	class _BaseAppModule(appModuleHandler.AppModule):
		disableBrowseModeByDefault = True


addonHandler.initTranslation()

_LOG_TAG = "WA-GUARD"
_NAV_LOG_TAG = "WA-NAV"
#: Window class of the WinUI pane that hosts the WebView2.
_HOST_PANE_CLASS = "Microsoft.UI.Content.DesktopChildSiteBridge"
#: Window class of the Chromium window that holds the WhatsApp page.
_PAGE_WINDOW_CLASS = "Chrome_RenderWidgetHostHWND"
#: Top-level Chromium window class whose title ends with "WhatsApp".
_PAGE_TOP_WINDOW_CLASS = "Chrome_WidgetWin_1"
#: How long a pending silent return stays valid, in seconds.
_SILENT_RETURN_SECONDS = 1.0

#: Limits for one search of the page, so a very large chat can never freeze NVDA.
_SEARCH_MAX_NODES = 4000
_SEARCH_MAX_SECONDS = 3.0
#: How far down the "first child" or "last child" chain the quick paths follow.
_QUICK_MAX_DEPTH = 4

_user32 = ctypes.windll.user32
_ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class _GuardState:
	"""State shared by both app module instances.

	NVDA runs one instance for WhatsApp.Root.exe (the WinUI host) and one for the
	msedgewebview2.exe process that renders the page; both import this module.
	"""

	pageWindow = 0
	silentTarget = None
	silentUntil = 0.0


class _NavCache:
	"""Navigation targets remembered between commands. Every use is validated first."""

	composer = None
	chatTable = None
	lastChatCell = None
	#: Containers to follow "last child" steps from to reach the newest message row.
	messageContainers = ()

	@classmethod
	def reset(cls):
		cls.composer = None
		cls.chatTable = None
		cls.lastChatCell = None
		cls.messageContainers = ()


def _isEditableField(obj) -> bool:
	try:
		states = obj.states
		if controlTypes.State.READONLY in states:
			return False
		return obj.role == controlTypes.Role.EDITABLETEXT or controlTypes.State.EDITABLE in states
	except Exception:
		return False


def _searchPageWindow() -> int:
	"""Find the WhatsApp page window when no focus event has told us yet."""
	found = []

	def onChild(hwnd, lParam):
		if winUser.getClassName(hwnd) == _PAGE_WINDOW_CLASS:
			found.append(hwnd)
			return False
		return True

	def onTopLevel(hwnd, lParam):
		if (
			winUser.getClassName(hwnd) == _PAGE_TOP_WINDOW_CLASS
			and winUser.getWindowText(hwnd).endswith("WhatsApp")
			and appModuleHandler.getAppNameFromProcessID(winUser.getWindowThreadProcessID(hwnd)[0])
			in ("whatsapp.root", "msedgewebview2")
		):
			_user32.EnumChildWindows(hwnd, _ENUM_WINDOWS_PROC(onChild), 0)
			if found:
				return False
		return True

	_user32.EnumWindows(_ENUM_WINDOWS_PROC(onTopLevel), 0)
	return found[0] if found else 0


def _setPageWindow(hwnd: int) -> None:
	if hwnd != _GuardState.pageWindow:
		# A different page (WhatsApp was restarted): nothing remembered can be trusted.
		_NavCache.reset()
	_GuardState.pageWindow = hwnd


def _pageWindow() -> int:
	if _GuardState.pageWindow and _user32.IsWindow(_GuardState.pageWindow):
		return _GuardState.pageWindow
	_setPageWindow(_searchPageWindow())
	return _GuardState.pageWindow


def _pageDocument():
	"""The root accessible object of the WhatsApp page, or None."""
	hwnd = _pageWindow()
	if not hwnd:
		return None
	return getNVDAObjectFromEvent(hwnd, winUser.OBJID_CLIENT, 0) or None


def _realPageFocus():
	"""The element that really has the keyboard focus inside the WhatsApp page, or None."""
	document = _pageDocument()
	if document is None:
		return None
	try:
		focused = document.activeChild
	except Exception:
		focused = None
	return focused or document


def _requestSilentReturn(target) -> None:
	"""Mark target for a silent focus event if NVDA's focus is already on it."""
	if target == api.getFocusObject():
		_GuardState.silentTarget = target
		_GuardState.silentUntil = time.monotonic() + _SILENT_RETURN_SECONDS


def _takeSilentReturn(obj) -> bool:
	target = _GuardState.silentTarget
	if target is None:
		return False
	_GuardState.silentTarget = None
	return time.monotonic() <= _GuardState.silentUntil and obj == target


# Navigation ---------------------------------------------------------------------------


def _safeRole(obj):
	try:
		return obj.role
	except Exception:
		return None


def _safeStates(obj):
	try:
		return obj.states
	except Exception:
		return set()


def _safeChildren(obj):
	try:
		return obj.children or []
	except Exception:
		return []


def _safeFirstChild(obj):
	try:
		return obj.firstChild
	except Exception:
		return None


def _safeLastChild(obj):
	try:
		return obj.lastChild
	except Exception:
		return None


def _safeParent(obj):
	try:
		return obj.parent
	except Exception:
		return None


class NavigationTargets:
	"""What one walk of the page found. Every attribute is None or empty when not found."""

	def __init__(self):
		self.chatTable = None
		self.chatCell = None
		self.messageRow = None
		self.messageContainers = ()
		self.composer = None
		self.visited = 0
		self.timedOut = False


def _quickFirstCell(table):
	"""The first cell of a table, reached through first-child steps only."""
	node = table
	for _ in range(_QUICK_MAX_DEPTH):
		node = _safeFirstChild(node)
		if node is None:
			return None
		if _safeRole(node) == controlTypes.Role.TABLECELL:
			return node
	return None


def _newestRowIn(container):
	"""The newest message row below container, reached through last-child steps only."""
	node = container
	for _ in range(_QUICK_MAX_DEPTH):
		node = _safeLastChild(node)
		if node is None:
			return None
		if _safeRole(node) == controlTypes.Role.TABLEROW:
			return node
	return None


def _messageTarget(row):
	"""The element of a message row that takes the keyboard focus."""
	for child in _safeChildren(row):
		if controlTypes.State.FOCUSABLE in _safeStates(child):
			return child
	return row


def findNavigationTargets(root, want=None, clock=time.monotonic) -> NavigationTargets:
	"""Walk the page once, breadth first, and pick the navigation targets.

	- Chat list: the first table. Its first cell is the target; the walk never goes
	  inside the table.
	- Message field: an edit field that is multiline or inside a landmark (the footer),
	  preferring one that is both. The single-line search box is neither.
	- Messages: table rows outside any table. The newest message is the last one in
	  document order that comes before the message field. The walk never goes inside rows.

	want is a set of "chatCell", "messageRow" and "composer"; the walk stops as soon as a
	single wanted target is certain. A node's position is the tuple of child indices from
	the root, so comparing positions compares document order without asking for geometry.
	"""
	targets = NavigationTargets()
	deadline = clock() + _SEARCH_MAX_SECONDS
	queue = deque([(root, False, (), None)])
	rows = []
	fields = []
	while queue:
		if targets.visited >= _SEARCH_MAX_NODES or clock() > deadline:
			targets.timedOut = True
			break
		node, inLandmark, position, parentNode = queue.popleft()
		targets.visited += 1
		for index, child in enumerate(_safeChildren(node)):
			role = _safeRole(child)
			childPosition = position + (index,)
			if role == controlTypes.Role.TABLE:
				if targets.chatTable is None:
					targets.chatTable = child
					targets.chatCell = _quickFirstCell(child)
				continue
			if role == controlTypes.Role.TABLEROW:
				rows.append((childPosition, child, node, parentNode))
				continue
			if role == controlTypes.Role.EDITABLETEXT:
				states = _safeStates(child)
				multiline = controlTypes.State.MULTILINE in states
				# A single-line field outside a landmark is the search box, not the message field.
				if controlTypes.State.READONLY not in states and (inLandmark or multiline):
					fields.append((inLandmark, multiline, childPosition, child))
			queue.append((child, inLandmark or role == controlTypes.Role.LANDMARK, childPosition, node))
		if want is not None and len(want) == 1:
			if "chatCell" in want and targets.chatCell is not None:
				break
			if "composer" in want and any(f[0] and f[1] for f in fields):
				break
	composerPosition = None
	if fields:
		best = max(fields, key=lambda f: (f[0], f[1]))
		targets.composer = best[3]
		composerPosition = best[2]
	if rows:
		if composerPosition is not None:
			candidates = [r for r in rows if r[0] < composerPosition] or rows
		else:
			candidates = rows
		newest = max(candidates, key=lambda r: r[0])
		targets.messageRow = newest[1]
		# Rows sit in groups of messages: remember both levels above the row, so a later
		# message in a new group is still reached from the higher one.
		targets.messageContainers = tuple(c for c in (newest[3], newest[2]) if c is not None)
	return targets


class SelectionPopup(IAccessible):
	"""A popup with role menu inside the WhatsApp page, such as the one shown next to selected text."""

	def _get_shouldAllowIAccessibleMenuStartEvent(self) -> bool:
		if controlTypes.State.FOCUSED in self.states:
			return True
		focus = api.getFocusObject()
		if focus is not None and _isEditableField(focus) and focus.windowHandle == self.windowHandle:
			log.info("%s ignored a menu popup while an edit field has focus" % _LOG_TAG)
			return False
		return super()._get_shouldAllowIAccessibleMenuStartEvent()


class HostPane(NVDAObject):
	"""The WinUI pane that hosts the WebView2. It never has useful focus of its own."""

	# Computed once per core cycle; NVDA reads focusRedirect more than once per event.
	_cache_focusRedirect = True

	def _get_focusRedirect(self):
		target = _realPageFocus()
		if target is None:
			return None
		_requestSilentReturn(target)
		log.info(
			"%s focus on the host pane returned to the page (%s, silent=%s)"
			% (_LOG_TAG, getattr(target.role, "name", target.role), _GuardState.silentTarget is not None),
		)
		return target


def _cachedTarget(which):
	"""A remembered target that is still alive, or None. Cheap: a few object calls."""
	if which == "composer":
		field = _NavCache.composer
		if (
			field is not None
			and _safeRole(field) == controlTypes.Role.EDITABLETEXT
			and controlTypes.State.READONLY not in _safeStates(field)
		):
			return field
	elif which == "chatCell":
		table = _NavCache.chatTable
		if table is None or _safeRole(table) != controlTypes.Role.TABLE:
			return None
		cell = _NavCache.lastChatCell
		if cell is not None and _safeRole(cell) == controlTypes.Role.TABLECELL:
			# The cell was remembered from any focus event; it counts only if it is in the chat list.
			row = _safeParent(cell)
			if row is not None and _safeParent(row) == table:
				return cell
		return _quickFirstCell(table)
	elif which == "messageRow":
		for container in _NavCache.messageContainers:
			if _safeRole(container) is None:
				continue
			row = _newestRowIn(container)
			if row is not None:
				return _messageTarget(row)
	return None


class AppModule(_BaseAppModule):
	# Translators: The category under which this add-on's commands appear in Input Gestures.
	scriptCategory = _("WhatsApp Desktop Access")

	def chooseNVDAObjectOverlayClasses(self, obj, clsList):
		# Runs for every object NVDA creates in WhatsApp, so it must stay cheap: a window
		# class compare first, and for page objects a single role query (not .role, which
		# also reads the states and is never cached).
		try:
			windowClass = obj.windowClassName
		except Exception:
			windowClass = ""
		if windowClass == _HOST_PANE_CLASS:
			clsList.insert(0, HostPane)
		elif windowClass == _PAGE_WINDOW_CLASS and isinstance(obj, IAccessible):
			try:
				isMenuPopup = obj.IAccessibleRole == oleacc.ROLE_SYSTEM_MENUPOPUP
			except Exception:
				isMenuPopup = False
			if isMenuPopup:
				clsList.insert(0, SelectionPopup)
		super().chooseNVDAObjectOverlayClasses(obj, clsList)

	def event_gainFocus(self, obj, nextHandler):
		try:
			if obj.windowClassName == _PAGE_WINDOW_CLASS:
				_setPageWindow(obj.windowHandle)
				self._rememberFocus(obj)
		except Exception:
			pass
		if _takeSilentReturn(obj):
			# NVDA's focus is already back on this element. Update braille and vision,
			# but do not announce the field again.
			log.info("%s focus returned silently" % _LOG_TAG)
			braille.handler.handleGainFocus(obj)
			vision.handler.handleGainFocus(obj)
			return
		nextHandler()

	def _rememberFocus(self, obj):
		"""Remember where the user is, so Alt+1 and Alt+D return there without a search.

		This runs on every focus event, so it does almost nothing: one role read, and
		nothing that creates objects. A remembered cell is checked against the chat list
		only when it is used (see _cachedTarget).
		"""
		role = _safeRole(obj)
		if role == controlTypes.Role.TABLECELL:
			_NavCache.lastChatCell = obj
		elif role == controlTypes.Role.EDITABLETEXT:
			states = _safeStates(obj)
			if (
				controlTypes.State.MULTILINE in states
				and controlTypes.State.READONLY not in states
				and any(_safeRole(a) == controlTypes.Role.LANDMARK for a in api.getFocusAncestors())
			):
				_NavCache.composer = obj

	# Navigation commands --------------------------------------------------------------

	def _moveTo(self, which, notFoundMessage):
		started = time.monotonic()
		picked = _cachedTarget(which)
		cached = picked is not None
		visited = 0
		timedOut = False
		if picked is not None:
			try:
				picked.setFocus()
			except Exception:
				log.debugWarning("%s remembered target is gone" % _NAV_LOG_TAG, exc_info=True)
				picked = None
				cached = False
		if picked is None:
			document = _pageDocument()
			if document is not None:
				targets = findNavigationTargets(document, want={which})
				visited = targets.visited
				timedOut = targets.timedOut
				if targets.chatTable is not None:
					_NavCache.chatTable = targets.chatTable
				if targets.composer is not None:
					_NavCache.composer = targets.composer
				if targets.messageContainers:
					_NavCache.messageContainers = targets.messageContainers
				picked = {
					"chatCell": targets.chatCell,
					"messageRow": _messageTarget(targets.messageRow) if targets.messageRow else None,
					"composer": targets.composer,
				}[which]
			if picked is not None:
				if which == "chatCell":
					_NavCache.lastChatCell = picked
				try:
					picked.setFocus()
				except Exception:
					log.debugWarning("%s could not set focus" % _NAV_LOG_TAG, exc_info=True)
					picked = None
		log.info(
			"%s %s found=%s cached=%s visited=%d timedOut=%s %.2fs"
			% (
				_NAV_LOG_TAG,
				which,
				picked is not None,
				cached,
				visited,
				timedOut,
				time.monotonic() - started,
			),
		)
		if picked is None:
			ui.message(notFoundMessage)

	@script(
		# Translators: Description of a command that moves the focus to the chat list.
		description=_("Move to the chat list"),
		gesture="kb:alt+1",
	)
	def script_goToChatList(self, gesture):
		# Translators: Spoken when the chat list could not be found.
		self._moveTo("chatCell", _("Chat list not found"))

	@script(
		# Translators: Description of a command that moves the focus to the newest message.
		description=_("Move to the message list"),
		gesture="kb:alt+2",
	)
	def script_goToMessageList(self, gesture):
		# Translators: Spoken when the message list could not be found.
		self._moveTo("messageRow", _("Message list not found. Open a chat first."))

	@script(
		# Translators: Description of a command that moves the focus to the message field.
		description=_("Move to the message field"),
		gesture="kb:alt+d",
	)
	def script_goToMessageField(self, gesture):
		# Translators: Spoken when the message field could not be found.
		self._moveTo("composer", _("Message field not found. Open a chat first."))
