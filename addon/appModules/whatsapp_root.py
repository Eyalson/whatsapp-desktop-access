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
so they keep working when WhatsApp changes its page markup and in every UI language. When
the focus is already on the target (WhatsApp puts it in the message field when a chat
opens), the target is announced anyway, as a focus change would.

Speed: creating an NVDA object costs several milliseconds when many add-ons are installed,
so walking the page takes about a second. The first command walks the page and remembers
what it found; later commands check that the remembered object is still alive and use it
directly. Finding the newest message needs only a few "last child" steps.

Mouse
-----
The page is drawn inside the WinUI window, but its own window lies at the bottom of the
window order, so Windows never reports it under the mouse. Instead NVDA finds:

- over the title bar, a transparent WinUI window named "Non Client Input Sink Window",
  which also covers the Minimize, Maximize and Close buttons;
- everywhere else, the pane that hosts the page, which has no name, so NVDA says nothing.

Both now send NVDA to what is really under the mouse: the page element, the window title,
or the caption button. Chromium answers a hit test at a new point with the page itself and
finds the real element in the background, so in that case NVDA asks again a moment later.
This work runs only when the mouse moves over WhatsApp, never on focus changes or typing.

NVDA reads only the text under the mouse, which leaves icon buttons (such as "Play voice
message") and empty text fields silent. Controls in the page are therefore announced like
a focus change when the mouse enters them. When the hit test returns the paragraph inside
the message field or the icon inside a button, the control one or two levels up is used.
"""

import ctypes
from collections import deque
from ctypes import wintypes
import time

from comtypes import COMError
from comInterfaces import Accessibility as IA
from comInterfaces import IAccessible2Lib as IA2

import addonHandler
import api
import appModuleHandler
import braille
import config
import controlTypes
import core
import IAccessibleHandler
import locationHelper
import mouseHandler
import oleacc
import speech
import textInfos
import textUtils
import ui
import vision
import winUser
from logHandler import log
from NVDAObjects import NVDAObject, NVDAObjectTextInfo
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
_MOUSE_LOG_TAG = "WA-MOUSE"
#: Window class of the WinUI pane that hosts the WebView2.
_HOST_PANE_CLASS = "Microsoft.UI.Content.DesktopChildSiteBridge"
#: Window class of the transparent WinUI window that takes the mouse over the title bar.
_TITLE_BAR_INPUT_CLASS = "InputNonClientPointerSource"
#: Window class of the WinUI window that holds the Minimize, Maximize and Close buttons.
_CAPTION_BUTTONS_CLASS = "ReunionWindowingCaptionControls"
#: How long to wait before asking the page again when it answered with the page itself, in ms.
_HOVER_RECHECK_MS = 50
#: Hit tests normally reach the element in one step; this only stops a misbehaving page.
_HIT_TEST_MAX_STEPS = 8
#: Page elements that are announced like a focus change when the mouse enters them.
_CONTROL_ROLES = frozenset(
	(
		oleacc.ROLE_SYSTEM_PUSHBUTTON,
		oleacc.ROLE_SYSTEM_BUTTONMENU,
		oleacc.ROLE_SYSTEM_BUTTONDROPDOWN,
		oleacc.ROLE_SYSTEM_BUTTONDROPDOWNGRID,
		oleacc.ROLE_SYSTEM_SPLITBUTTON,
		oleacc.ROLE_SYSTEM_OUTLINEBUTTON,
		oleacc.ROLE_SYSTEM_LINK,
		oleacc.ROLE_SYSTEM_TEXT,
		oleacc.ROLE_SYSTEM_CHECKBUTTON,
		oleacc.ROLE_SYSTEM_RADIOBUTTON,
		oleacc.ROLE_SYSTEM_COMBOBOX,
		oleacc.ROLE_SYSTEM_SLIDER,
		oleacc.ROLE_SYSTEM_SPINBUTTON,
		oleacc.ROLE_SYSTEM_MENUITEM,
		oleacc.ROLE_SYSTEM_PAGETAB,
	),
)
#: Text and icons, which the hit test returns for the label or icon inside a button or link.
_CONTROL_CONTENT_ROLES = frozenset((oleacc.ROLE_SYSTEM_GRAPHIC, oleacc.ROLE_SYSTEM_STATICTEXT))
#: How many objects the search for a chat list cell may look at when the quick path fails.
_CELL_SEARCH_MAX_NODES = 60
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


class _HoverState:
	"""What the mouse was last found on, so moving within one element creates no new object."""

	pageWindow = 0
	#: (IAccessible, child ID) of the page document in pageWindow.
	pageRoot = None
	#: What the last returned object stands for, such as ("page", window, element ID).
	lastKey = None
	lastObject = None
	#: The mouse position a second check was last scheduled for.
	recheckPos = None
	#: The IAccessible of a control while its NVDA object is being made (see HoverControl).
	controlBeingMade = None


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
		#: How many children of each role the walk saw; logged when a target is missing.
		self.roleCounts = {}


def _firstCell(table):
	"""The first cell of the chat list: first-child steps, or a short search inside it."""
	cell = _quickFirstCell(table)
	if cell is not None:
		return cell
	queue = deque([table])
	visited = 0
	while queue and visited < _CELL_SEARCH_MAX_NODES:
		node = queue.popleft()
		visited += 1
		for child in _safeChildren(node):
			if _safeRole(child) == controlTypes.Role.TABLECELL:
				log.info("%s first chat found by a search inside the list (%d objects)" % (_NAV_LOG_TAG, visited))
				return child
			queue.append(child)
	return None


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
			targets.roleCounts[role] = targets.roleCounts.get(role, 0) + 1
			childPosition = position + (index,)
			if role == controlTypes.Role.TABLE:
				if targets.chatTable is None:
					targets.chatTable = child
					targets.chatCell = _firstCell(child)
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


# Mouse --------------------------------------------------------------------------------


def _logMouse(message, started):
	# Written only when NVDA logs at the input/output level, so normal use adds nothing.
	if log.isEnabledFor(log.IO):
		log.io("%s %s %.1fms" % (_MOUSE_LOG_TAG, message, (time.perf_counter() - started) * 1000))


def _roleName(obj):
	role = _safeRole(obj)
	return getattr(role, "name", role)


def _containsPoint(rect, x, y) -> bool:
	return rect.left <= x < rect.right and rect.top <= y < rect.bottom


def _windowRect(hwnd):
	rect = wintypes.RECT()
	if not _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
		return None
	return rect


def _elementID(acc):
	try:
		return acc.uniqueID
	except Exception:
		# Not IAccessible2, or the element is gone.
		return None


def _reuseOrMake(key, make):
	"""The object for key: the one returned last time if NVDA still has it under the mouse.

	Moving within one element then creates no new object. An object is reused only while
	it is NVDA's mouse object, because NVDA remembers on it what it last read there and
	would stay silent when the mouse comes back to it later.
	"""
	last = _HoverState.lastObject
	if key is not None and key == _HoverState.lastKey and last is not None:
		mouseObject = api.getMouseObject()
		# NVDA keeps its own instance when a new one compares equal, so hand that one back.
		if mouseObject is not None and (mouseObject is last or mouseObject == last):
			_HoverState.lastObject = mouseObject
			return mouseObject, False
	obj = make()
	_HoverState.lastKey = key
	_HoverState.lastObject = obj
	return obj, True


def _pageHitTest(x, y):
	"""Ask the WhatsApp page which element is at the point.

	Returns (IAccessible, child ID, reachedElement), or None when the point is outside the
	page or the page cannot be asked. reachedElement is False when the page answered with
	itself: Chromium does that for a point it has not looked up yet, and finds the element
	in the background for the next question.
	"""
	hwnd = _pageWindow()
	if not hwnd:
		return None
	if _HoverState.pageWindow != hwnd or _HoverState.pageRoot is None:
		root = IAccessibleHandler.accessibleObjectFromEvent(hwnd, winUser.OBJID_CLIENT, 0)
		if not root:
			return None
		_HoverState.pageWindow = hwnd
		_HoverState.pageRoot = root
	acc, childID = _HoverState.pageRoot
	reachedElement = False
	for _ in range(_HIT_TEST_MAX_STEPS):
		try:
			hit = acc.accHitTest(x, y)
		except COMError:
			# The page may have been replaced; fetch it again next time.
			_HoverState.pageRoot = None
			return None
		if hit is None:
			if not reachedElement:
				# The point is outside the page.
				return None
			break
		if isinstance(hit, int):
			if hit != 0:
				childID = hit
				reachedElement = True
			break
		try:
			acc = IAccessibleHandler.normalizeIAccessible(hit)
		except (COMError, RuntimeError):
			return None
		childID = 0
		reachedElement = True
	return acc, childID, reachedElement


def _isEditable(acc) -> bool:
	try:
		return bool(acc.states & IA2.IA2_STATE_EDITABLE)
	except Exception:
		# Not IAccessible2, or the element is gone.
		return False


def _controlFor(acc, childID):
	"""The control the element at the mouse belongs to, or None.

	Runs for every new element under the mouse, so it uses plain MSAA calls (far cheaper than
	NVDA objects) and climbs only where a control can be:
	- text or an icon sits directly inside its button or link: one level up;
	- inside an editable field such as the message field, the hit test returns the paragraph
	  (one level below the field) or the text in it (two levels below).
	"""
	if childID != 0:
		return None
	try:
		role = acc.accRole(0)
	except COMError:
		return None
	if role in _CONTROL_ROLES:
		return acc
	if role in _CONTROL_CONTENT_ROLES:
		levels = 2 if _isEditable(acc) else 1
	elif role == oleacc.ROLE_SYSTEM_GROUPING and _isEditable(acc):
		levels = 1
	else:
		return None
	node = acc
	for _ in range(levels):
		try:
			node = node.accParent.QueryInterface(IA.IAccessible)
			role = node.accRole(0)
		except (COMError, AttributeError):
			return None
		if role in _CONTROL_ROLES:
			return IAccessibleHandler.normalizeIAccessible(node)
	return None


def _makePageObject(acc, childID):
	control = _controlFor(acc, childID)
	if control is None:
		return IAccessible(IAccessibleObject=acc, IAccessibleChildID=childID)
	# Tells chooseNVDAObjectOverlayClasses to add HoverControl to exactly this object.
	_HoverState.controlBeingMade = control
	try:
		return IAccessible(IAccessibleObject=control, IAccessibleChildID=0)
	finally:
		_HoverState.controlBeingMade = None


def _pageElementAt(x, y):
	"""The NVDA object for the page element at the point, or None."""
	started = time.perf_counter()
	hit = _pageHitTest(x, y)
	if hit is None:
		return None
	acc, childID, reachedElement = hit
	if not reachedElement:
		if _askAgainSoon():
			_logMouse("page not ready, asking again", started)
		return None
	# Keyed by what the hit test returned, so moving within it skips the control search too.
	elementID = _elementID(acc) if childID == 0 else None
	key = ("page", _HoverState.pageWindow, elementID) if elementID is not None else None
	element, isNew = _reuseOrMake(key, lambda: _makePageObject(acc, childID))
	if isNew:
		# The first answer for a new element can still be a larger container: ask once more.
		_askAgainSoon()
		_logMouse(
			"page %s %s" % ("control" if isinstance(element, HoverControl) else "element", _roleName(element)),
			started,
		)
	return element


def _isReadable(text) -> bool:
	"""Whether text has something to say: NVDA's own test for text under the mouse."""
	return any(not ch.isspace() and ch != textUtils.OBJ_REPLACEMENT_CHAR for ch in text or "")


class HoverControl(NVDAObject):
	"""A control in the page that the mouse entered: a button, link, text field, slider and so on.

	NVDA reads only the text under the mouse, so a button that shows an icon (such as "Play
	voice message") and an empty text field said nothing at all. On entering, this says the
	control like a focus change (name, role, states and value), then the text under the mouse
	when it adds something. Inside the control, NVDA's own handling continues unchanged.
	"""

	def event_mouseMove(self, x, y):
		if self._mouseEntered or config.conf["mouse"]["reportObjectRoleOnMouseEnter"]:
			# Already inside, or NVDA's own option already reports controls on entering.
			super().event_mouseMove(x, y)
			return
		text = self._readTextUnderMouse(x, y)
		speech.cancelSpeech()
		editable = (
			_safeRole(self) == controlTypes.Role.EDITABLETEXT
			or controlTypes.State.EDITABLE in _safeStates(self)
		)
		speech.speakObjectProperties(
			self,
			reason=controlTypes.OutputReason.FOCUS,
			name=True,
			role=True,
			states=True,
			# The value of a text field is all of its text; the part under the mouse follows.
			value=not editable,
			description=config.conf["presentation"]["reportObjectDescriptions"],
		)
		if _isReadable(text) and text.strip() != (self.name or "").strip():
			speech.speakText(text)
		# NVDA's own handling updates braille, vision and the review cursor. It finds the text
		# under the mouse already read, so it says nothing more.
		super().event_mouseMove(x, y)

	def _readTextUnderMouse(self, x, y):
		"""The text NVDA would read under the mouse, remembered as already read (or None)."""
		try:
			info = self.makeTextInfo(locationHelper.Point(x, y))
		except NotImplementedError:
			info = NVDAObjectTextInfo(self, textInfos.POSITION_FIRST)
		except LookupError:
			return None
		info.expand(info.unit_mouseChunk)
		self._lastMouseTextInfoObject = info
		return info.text


def _askAgainSoon() -> bool:
	"""Run NVDA's mouse handling once more shortly, if the mouse stays where it is.

	Happens at most once per mouse position, so it can never repeat on its own.
	Returns whether a new check was scheduled.
	"""
	try:
		pos = tuple(winUser.getCursorPos())
	except Exception:
		return False
	if pos == _HoverState.recheckPos:
		return False
	_HoverState.recheckPos = pos
	core.callLater(_HOVER_RECHECK_MS, _recheckMouse, pos)
	return True


def _recheckMouse(pos):
	if not config.conf["mouse"]["enableMouseTracking"]:
		return
	try:
		if tuple(winUser.getCursorPos()) != pos:
			# The mouse moved on; NVDA handles the new position itself.
			return
		mouseHandler.executeMouseMoveEvent(*pos)
	except Exception:
		log.debugWarning("%s second check failed" % _MOUSE_LOG_TAG, exc_info=True)


def _captionButtonAt(topWindow, x, y):
	"""The Minimize, Maximize or Close button at the point, or None."""
	captionWindow = _user32.FindWindowExW(wintypes.HWND(topWindow), None, _CAPTION_BUTTONS_CLASS, None)
	if not captionWindow:
		return None
	rect = _windowRect(captionWindow)
	if rect is None or not _containsPoint(rect, x, y):
		return None
	import UIAHandler

	handler = UIAHandler.handler
	if handler is None:
		return None
	started = time.perf_counter()
	try:
		window = handler.clientObject.ElementFromHandleBuildCache(captionWindow, handler.baseCacheRequest)
		buttons = window.FindAllBuildCache(
			UIAHandler.TreeScope_Children,
			handler.clientObject.CreateTrueCondition(),
			handler.baseCacheRequest,
		)
		for index in range(buttons.Length):
			button = buttons.GetElement(index)
			if _containsPoint(button.CurrentBoundingRectangle, x, y):
				from NVDAObjects.UIA import UIA

				obj, isNew = _reuseOrMake(("caption", captionWindow, index), lambda: UIA(UIAElement=button))
				if isNew:
					_logMouse("caption button %d" % index, started)
				return obj
	except COMError:
		log.debugWarning("%s caption buttons not available" % _MOUSE_LOG_TAG, exc_info=True)
	return None


def _titleBarObjectAt(inputWindow, x, y):
	"""What is under the mouse over the title bar: a caption button, or the window itself."""
	topWindow = winUser.getAncestor(inputWindow, winUser.GA_ROOT)
	if not topWindow:
		return None
	button = _captionButtonAt(topWindow, x, y)
	if button is not None:
		return button
	started = time.perf_counter()
	window, isNew = _reuseOrMake(
		("title", topWindow),
		lambda: getNVDAObjectFromEvent(topWindow, winUser.OBJID_CLIENT, 0) or None,
	)
	if isNew:
		_logMouse("title bar", started)
	return window


class TitleBarInput(NVDAObject):
	"""The transparent WinUI window over the title bar, named "Non Client Input Sink Window"."""

	def objectFromPointRedirect(self, x, y):
		try:
			return _titleBarObjectAt(self.windowHandle, x, y)
		except Exception:
			log.debugWarning("%s title bar redirect failed" % _MOUSE_LOG_TAG, exc_info=True)
			return None


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
	"""The WinUI pane that hosts the WebView2. It never has useful focus of its own.

	It is also what Windows reports under the mouse everywhere below the title bar.
	"""

	def objectFromPointRedirect(self, x, y):
		try:
			return _pageElementAt(x, y)
		except Exception:
			log.debugWarning("%s page redirect failed" % _MOUSE_LOG_TAG, exc_info=True)
			return None

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
		return _firstCell(table)
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
		elif windowClass == _TITLE_BAR_INPUT_CLASS:
			clsList.insert(0, TitleBarInput)
		elif windowClass == _PAGE_WINDOW_CLASS and isinstance(obj, IAccessible):
			beingMade = _HoverState.controlBeingMade
			if beingMade is not None and obj.IAccessibleObject is beingMade:
				clsList.insert(0, HoverControl)
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

	@staticmethod
	def _focus(target):
		"""Move the focus to target. Returns (moved, alreadyThere), or (False, False) on failure."""
		alreadyThere = target == api.getFocusObject()
		try:
			target.setFocus()
		except Exception:
			log.debugWarning("%s could not set focus" % _NAV_LOG_TAG, exc_info=True)
			return False, False
		return True, alreadyThere

	def _moveTo(self, which, notFoundMessage):
		started = time.monotonic()
		picked = _cachedTarget(which)
		cached = picked is not None
		visited = 0
		timedOut = False
		alreadyThere = False
		targets = None
		if picked is not None:
			moved, alreadyThere = self._focus(picked)
			if not moved:
				# The remembered target is gone; search the page instead.
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
				moved, alreadyThere = self._focus(picked)
				if not moved:
					picked = None
		log.info(
			"%s %s found=%s cached=%s alreadyThere=%s visited=%d timedOut=%s %.2fs"
			% (
				_NAV_LOG_TAG,
				which,
				picked is not None,
				cached,
				alreadyThere,
				visited,
				timedOut,
				time.monotonic() - started,
			),
		)
		if picked is None:
			if targets is not None:
				# Only roles and counts, never names: enough to see what the page looked like.
				seen = sorted(targets.roleCounts.items(), key=lambda item: -item[1])
				log.info(
					"%s %s not found: chat list %s, roles seen: %s"
					% (
						_NAV_LOG_TAG,
						which,
						"found" if targets.chatTable is not None else "missing",
						", ".join("%s=%d" % (getattr(role, "name", role), count) for role, count in seen),
					),
				)
			ui.message(notFoundMessage)
		elif alreadyThere:
			# The focus does not move, so NVDA would say nothing: announce the target the way a
			# focus change would.
			speech.speakObject(picked, reason=controlTypes.OutputReason.FOCUS)

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
