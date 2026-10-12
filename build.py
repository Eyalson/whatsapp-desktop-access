#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# WhatsApp Desktop Access - build script.
# Copyright (C) 2026 Sontech - Eyal Sonsino
# This file is covered by the GNU General Public License, version 2.
# See the file LICENSE for details.
"""
WhatsApp Desktop Access - Build Script
Compiles .po translation files to .mo binary format and packages the add-on.

Usage:
    python build.py              # builds the add-on in ./addon
    python build.py diag-addon   # builds the focus diagnostics add-on

LEARNING POINT:
.po (Portable Object) = human-readable translation file
.mo (Machine Object)  = compiled binary that NVDA loads at runtime

The .mo format is a standard GNU gettext binary format:
- Starts with a magic number (0x950412de)
- Contains two sorted tables: original strings and translations
- NVDA loads the matching language .mo at startup
"""

import os
import sys
import struct
import codecs
import zipfile
import configparser


_PO_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def _unescape(text):
    """Turn .po escape sequences (\\n, \\", \\\\ ...) into the real characters."""
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(_PO_ESCAPES.get(text[i + 1], text[i + 1]))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def parse_po(po_path):
    """
    Parse a .po file and return a dict of {msgid: msgstr}.

    LEARNING POINT:
    A .po file looks like:
        msgid "Hello"
        msgstr "שלום"

    Multi-line strings use continuation lines starting with ":
        msgid ""
        "Hello "
        "World"
    We handle both cases.
    """
    messages = {}
    msgid = None
    msgstr = None
    in_msgid = False
    in_msgstr = False

    with codecs.open(po_path, "r", "utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()

            # Skip comments and blank lines
            if not line or line.startswith("#"):
                continue

            if line.startswith("msgid "):
                # Save previous pair if complete
                if msgid is not None and msgstr is not None:
                    # The header entry (empty msgid) is kept on purpose: it declares
                    # charset=UTF-8. Without it gettext assumes ASCII and NVDA fails
                    # to load the add-on as soon as it meets a Hebrew letter.
                    messages[_unescape(msgid)] = _unescape(msgstr)
                # Start new msgid
                msgid = line[7:-1]   # strip: msgid "..."
                msgstr = None
                in_msgid = True
                in_msgstr = False

            elif line.startswith("msgstr "):
                msgstr = line[8:-1]  # strip: msgstr "..."
                in_msgid = False
                in_msgstr = True

            elif line.startswith('"'):
                # Continuation line
                content = line[1:-1]
                if in_msgid:
                    msgid = (msgid or "") + content
                elif in_msgstr:
                    msgstr = (msgstr or "") + content

    # Don't forget the last entry
    if msgid is not None and msgstr is not None:
        messages[_unescape(msgid)] = _unescape(msgstr)

    return messages


def write_mo(messages, mo_path):
    """
    Write a dict of {msgid: msgstr} to a binary .mo file.

    LEARNING POINT:
    The MO binary format:
    - 4 bytes: magic number (little-endian)
    - 4 bytes: revision (0)
    - 4 bytes: number of strings (N)
    - 4 bytes: offset of original strings table
    - 4 bytes: offset of translation strings table
    - 4 bytes: hash table size (0 = no hash)
    - 4 bytes: hash table offset
    Then N * (length, offset) pairs for originals
    Then N * (length, offset) pairs for translations
    Then the actual string data
    """
    # Filter: skip entries where msgstr is empty
    filtered = {k: v for k, v in messages.items() if v}

    keys = sorted(filtered.keys())
    values = [filtered[k] for k in keys]
    n = len(keys)

    def enc(s):
        return s.encode("utf-8")

    # Build string data blocks and offset tables
    key_data = b""
    val_data = b""
    key_offsets = []
    val_offsets = []

    for k, v in zip(keys, values):
        kb = enc(k)
        vb = enc(v)
        key_offsets.append((len(kb), len(key_data)))
        key_data += kb + b"\x00"
        val_offsets.append((len(vb), len(val_data)))
        val_data += vb + b"\x00"

    # Calculate where string data starts in the file
    # Header = 7 * 4 bytes
    # Two offset tables = 2 * N * 8 bytes (each entry = length + offset)
    header_size = 7 * 4
    tables_size = 2 * n * 8
    key_data_start = header_size + tables_size
    val_data_start = key_data_start + len(key_data)

    # Build header
    output = struct.pack(
        "<Iiiiiii",
        0x950412DE,     # magic number (little-endian)
        0,              # file format revision
        n,              # number of strings
        header_size,                    # offset of original strings table
        header_size + n * 8,            # offset of translation strings table
        0,              # hash table size (unused)
        0,              # hash table offset (unused)
    )

    # Append offset tables
    for klen, koff in key_offsets:
        output += struct.pack("<ii", klen, key_data_start + koff)
    for vlen, voff in val_offsets:
        output += struct.pack("<ii", vlen, val_data_start + voff)

    # Append string data
    output += key_data + val_data

    os.makedirs(os.path.dirname(mo_path), exist_ok=True)
    with open(mo_path, "wb") as f:
        f.write(output)


def find_addon_dir():
    """
    Auto-detect the add-on folder by looking for the directory
    that contains manifest.ini. Works regardless of folder name.

    An explicit folder can be given on the command line:
        python build.py diag-addon
    """
    if len(sys.argv) > 1:
        explicit = sys.argv[1]
        if os.path.isdir(explicit) and os.path.exists(os.path.join(explicit, "manifest.ini")):
            return explicit
        print(f"ERROR: {explicit} is not an add-on folder (no manifest.ini)")
        return None
    if os.path.exists(os.path.join("addon", "manifest.ini")):
        return "addon"
    for entry in os.listdir("."):
        if os.path.isdir(entry) and os.path.exists(os.path.join(entry, "manifest.ini")):
            return entry
    return None


def build_all():
    """
    Find all .po files under {addon_dir}/locale/ and compile each to .mo.
    Prints a summary of how many strings were compiled per language.
    """
    addon_dir = find_addon_dir()
    if not addon_dir:
        print("ERROR: Could not find add-on folder (no manifest.ini found)")
        return
    locale_dir = os.path.join(addon_dir, "locale")

    if not os.path.isdir(locale_dir):
        print(f"ERROR: locale directory not found: {locale_dir}")
        return

    for lang in sorted(os.listdir(locale_dir)):
        po_path = os.path.join(locale_dir, lang, "LC_MESSAGES", "nvda.po")
        mo_path = os.path.join(locale_dir, lang, "LC_MESSAGES", "nvda.mo")

        if not os.path.exists(po_path):
            continue

        messages = parse_po(po_path)
        write_mo(messages, mo_path)
        print(f"  [{lang}] compiled {len(messages)} strings → {mo_path}")

    print("Build complete.")


def package():
    """
    Package the addon/ folder into a .nvda-addon file (ZIP archive).

    LEARNING POINT:
    .nvda-addon is just a ZIP file with a different extension.
    The ZIP must contain the *contents* of addon/ at the root level
    (not the addon/ folder itself), so NVDA finds manifest.ini at the top.

    The output filename is built from the add-on name + version
    read directly from addon/manifest.ini.
    """
    # Read name and version from manifest.ini
    addon_dir_for_manifest = find_addon_dir()
    manifest_path = os.path.join(addon_dir_for_manifest, "manifest.ini")

    # LEARNING POINT:
    # NVDA's manifest.ini has no [section] header, so configparser fails.
    # We parse it manually as simple "key = value" lines.
    manifest = {}
    with open(manifest_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                manifest[key.strip()] = value.strip()

    name    = manifest.get("name", "addon").replace(" ", "-")
    version = manifest.get("version", "0.1")

    output_filename = f"{name}-{version}.nvda-addon"

    addon_dir = find_addon_dir()
    if not addon_dir:
        print("ERROR: Could not find add-on folder (no manifest.ini found)")
        return None
    files_added = 0

    # LEARNING POINT:
    # zipfile.ZIP_DEFLATED = compressed ZIP (smaller file)
    # We walk every file inside addon/ and add it to the ZIP,
    # using a path relative to addon/ so manifest.ini is at the ZIP root.
    with zipfile.ZipFile(output_filename, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(addon_dir):
            for filename in filenames:
                full_path    = os.path.join(dirpath, filename)
                archive_path = os.path.relpath(full_path, addon_dir)
                zf.write(full_path, archive_path)
                files_added += 1

    print(f"  Packaged {files_added} files → {output_filename}")
    return output_filename


if __name__ == "__main__":
    # Windows consoles with a legacy code page cannot print the arrow used below.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("=== Step 1: Compiling translations ===")
    build_all()
    print()
    print("=== Step 2: Packaging add-on ===")
    package()
    print()
    print("Done! Install the .nvda-addon file by opening it, or via:")
    print("  NVDA menu → Tools → Add-on Store → Install from external source")
