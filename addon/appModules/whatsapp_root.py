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
"""

import ctypes
from ctypes import wintypes
import time

import api
import appModuleHandler
import braille
import controlTypes
import vision
import winUser
from logHandler import log
from NVDAObjects import NVDAObject
from NVDAObjects.IAccessible import IAccessible, getNVDAObjectFromEvent

try:
	# Extend NVDA's own WhatsApp support (it turns browse mode off by default) instead of replacing it.
	from nvdaBuiltin.appModules.whatsapp_root import AppModule as _BaseAppModule
except ImportError:
	# NVDA versions released before the built-in WhatsApp app module.
	class _BaseAppModule(appModuleHandler.AppModule):
		disableBrowseModeByDefault = True


_LOG_TAG = "WA-GUARD"
#: Window class of the WinUI pane that hosts the WebView2.
_HOST_PANE_CLASS = "Microsoft.UI.Content.DesktopChildSiteBridge"
#: Window class of the Chromium window that holds the WhatsApp page.
_PAGE_WINDOW_CLASS = "Chrome_RenderWidgetHostHWND"
#: Top-level Chromium window class whose title ends with "WhatsApp".
_PAGE_TOP_WINDOW_CLASS = "Chrome_WidgetWin_1"
#: How long a pending silent return stays valid, in seconds.
_SILENT_RETURN_SECONDS = 1.0

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


def _pageWindow() -> int:
	if _GuardState.pageWindow and _user32.IsWindow(_GuardState.pageWindow):
		return _GuardState.pageWindow
	_GuardState.pageWindow = _searchPageWindow()
	return _GuardState.pageWindow


def _realPageFocus():
	"""The element that really has the keyboard focus inside the WhatsApp page, or None."""
	hwnd = _pageWindow()
	if not hwnd:
		return None
	document = getNVDAObjectFromEvent(hwnd, winUser.OBJID_CLIENT, 0)
	if not document:
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


class AppModule(_BaseAppModule):
	def chooseNVDAObjectOverlayClasses(self, obj, clsList):
		try:
			windowClass = obj.windowClassName
		except Exception:
			windowClass = ""
		if windowClass == _PAGE_WINDOW_CLASS and obj.role == controlTypes.Role.POPUPMENU:
			clsList.insert(0, SelectionPopup)
		elif windowClass == _HOST_PANE_CLASS:
			clsList.insert(0, HostPane)
		super().chooseNVDAObjectOverlayClasses(obj, clsList)

	def event_gainFocus(self, obj, nextHandler):
		try:
			if obj.windowClassName == _PAGE_WINDOW_CLASS:
				_GuardState.pageWindow = obj.windowHandle
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
