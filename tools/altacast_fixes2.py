#!/usr/bin/env python3
"""
altacast_fixes2.py - second batch of AltaCast fixes (applied at build time).

  1. Encoder lock while connected
       Changing Encoder Type / Bitrate / Quality / Samplerate / Channels in the settings
       dialog while an encoder is connected re-initialised the running encoder and crashed.
       While the encoder is connected these fields are now greyed out.
  2. Multiple instances
       A warning popup (Yes/No, default No) is shown when another instance that uses the
       same folder (= the same .cfg files) is already running.
  3. Ogg FLAC archive file had no extension  ->  ".flac" is added.
  4. AAC Plus
       - altacast_aacp.ini is written next to the program (full path) instead of a
         relative name (which Windows resolves to the Windows folder).
       - channel mode is always set (it was empty for 48..64 kbps and 12..16 kbps stereo)
       - speech / pns / signallingmode keys written like ShuiCast does
       - the real reason is logged, and the status line says "AAC Plus init failed
         (see log)" instead of the generic "Encoder init failed".

Usage:  python altacast_fixes2.py <path to trunk/AltaCast/src>

Idempotent. Stops with an error if an anchor is missing. Can run before or after
altacast_fixes.py / ladio_patch.py.
"""
import re
import sys
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
TAG = "altacast_fixes2"


def fail(msg):
    print(TAG + ": ERROR: " + msg, file=sys.stderr)
    sys.exit(1)


def sub_once(text, pattern, repl, label, flags=0):
    new, n = re.subn(pattern, repl, text, flags=flags)
    if n != 1:
        fail("%s: expected exactly 1 match, found %d" % (label, n))
    return new


def patch_file(rel, func):
    path = SRC / rel
    if not path.exists():
        fail("%s not found under %s" % (rel, SRC))
    raw = path.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("latin-1").replace("\r\n", "\n")
    new = func(text)
    if new == text:
        return
    if crlf:
        new = new.replace("\n", "\r\n")
    path.write_bytes(new.encode("latin-1"))
    print(TAG + ": patched " + rel)


def c_wide(s):
    """Python str -> body of a C++ wide string literal, ASCII only (\\uXXXX escapes)."""
    out = []
    for ch in s:
        if ch == "\n":
            out.append("\\n")
        elif ch in '"\\':
            out.append("\\" + ch)
        elif ord(ch) < 128:
            out.append(ch)
        else:
            out.append("\\u%04x" % ord(ch))
    return "".join(out)


# ------------------------------------------------------------ 1. encoder lock
MARK_LOCK = TAG + ": lock"


def fix_lock_header(t):
    if MARK_LOCK in t:
        return t
    return sub_once(
        t,
        r"([ \t]*)void UpdateFields\(\);\n",
        lambda m: m.group(0)
        + m.group(1) + "void UpdateFieldsCore();   /* %s */\n" % MARK_LOCK
        + m.group(1) + "void ApplyEncoderLock();\n"
        + m.group(1) + "BOOL m_LockEncoder = FALSE;   /* TRUE while the encoder is connected */\n",
        "BasicSettings.h UpdateFields declaration",
    )


LOCK_CODE = """void CBasicSettings::UpdateFields() {
	UpdateFieldsCore();
	ApplyEncoderLock();
}

/* %(m)s: while connected, the fields that change the running encoder are greyed out */
void CBasicSettings::ApplyEncoderLock() {
	CWnd *w;

	if (!m_LockEncoder) {
		m_EncoderTypeCtrl.EnableWindow(TRUE);
		w = GetDlgItem(IDC_SAMPLERATE);
		if (w != NULL) w->EnableWindow(TRUE);
		w = GetDlgItem(IDC_CHANNELS);
		if (w != NULL) w->EnableWindow(TRUE);
		return;
	}
	m_EncoderTypeCtrl.EnableWindow(FALSE);
	m_BitrateCtrl.EnableWindow(FALSE);
	m_QualityCtrl.EnableWindow(FALSE);
	m_UseBitrateCtrl.EnableWindow(FALSE);
	w = GetDlgItem(IDC_SAMPLERATE);
	if (w != NULL) w->EnableWindow(FALSE);
	w = GetDlgItem(IDC_CHANNELS);
	if (w != NULL) w->EnableWindow(FALSE);
}

void CBasicSettings::UpdateFieldsCore() {"""


def fix_lock_cpp(t):
    if MARK_LOCK in t:
        return t
    return sub_once(
        t,
        r"void CBasicSettings::UpdateFields\(\) \{",
        lambda m: LOCK_CODE % {"m": MARK_LOCK},
        "BasicSettings.cpp UpdateFields",
    )


def fix_lock_config(t):
    if MARK_LOCK in t:
        return t
    return sub_once(
        t,
        r"([ \t]*)basicSettings->UpdateFields\(\);\n",
        lambda m: m.group(1)
        + "basicSettings->m_LockEncoder = (g->weareconnected != 0);   /* %s */\n" % MARK_LOCK
        + m.group(0),
        "Config.cpp GlobalsToDialog UpdateFields",
    )


# ------------------------------------------------------- 2. multiple instances
MARK_INST = TAG + ": instance"
INST_TITLE = "多重起動の確認"
INST_MSG = (
    "同じフォルダ(設定ファイルの場所)を使うインスタンスが、すでに起動しています。\n\n"
    "設定ファイル(.cfg)を互いに上書きし合い、設定が壊れたり、"
    "意図しない内容で保存されたりするおそれがあります。\n\n"
    "それでも起動しますか?\n\n"
    "(別々に使いたい場合は、アプリのフォルダごとコピーして、別のフォルダから起動してください。)"
)


def instance_block(indent, dir_expr, prefix):
    lines = [
        "/* %s: warn when another instance uses the same folder (= the same .cfg files) */" % MARK_INST,
        "{",
        "\tchar instDir[MAX_PATH] = \"\";",
        "\tchar *instFilePart = NULL;",
        "\tchar instMutexName[MAX_PATH + 64];",
        "\tif (GetFullPathNameA(%s, MAX_PATH, instDir, &instFilePart) == 0) {" % dir_expr,
        "\t\tstrcpy(instDir, \".\");",
        "\t}",
        "\tfor (char *ip = instDir; *ip; ++ip) {",
        "\t\tif (*ip == '\\\\' || *ip == ':') *ip = '/';",
        "\t\telse if (*ip >= 'A' && *ip <= 'Z') *ip = (char)(*ip - 'A' + 'a');",
        "\t}",
        "\tsprintf(instMutexName, \"Local\\\\%s_inst_%%s\", instDir);" % prefix,
        "\tHANDLE instMutex = CreateMutexA(NULL, FALSE, instMutexName);",
        "\tif (instMutex != NULL && GetLastError() == ERROR_ALREADY_EXISTS) {",
        "\t\tif (MessageBoxW(NULL, L\"%s\", L\"%s\", MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2) != IDYES) {"
        % (c_wide(INST_MSG), c_wide(INST_TITLE)),
        "\t\t\treturn FALSE;",
        "\t\t}",
        "\t}",
        "}",
    ]
    return "".join(indent + ln + "\n" for ln in lines)


def fix_instance(t):
    if MARK_INST in t:
        return t
    return sub_once(
        t,
        r"([ \t]*)LoadConfigs\(currentDir, \"altacaststandalone\"\);\n",
        lambda m: instance_block(m.group(1), "currentDir", "altacast") + "\n" + m.group(0),
        "altacastStandalone.cpp LoadConfigs",
    )


# --------------------------------------------------- 3 + 4. libaltacast.cpp
def fix_flac_ext(t):
    if 'strcat(outputFile, ".flac")' in t:
        return t
    return sub_once(
        t,
        r'([ \t]*)if\(g->gAACPFlag\) \{\n[ \t]*strcat\(outputFile, "\.aac"\);\n[ \t]*\}\n',
        lambda m: m.group(0)
        + m.group(1) + "if(g->gFLACFlag) {\n"
        + m.group(1) + '\tstrcat(outputFile, ".flac");\t/* %s: Ogg FLAC archive extension */\n' % TAG
        + m.group(1) + "}\n",
        "libaltacast.cpp archive extension",
    )


MARK_AACP = TAG + ": aacp"


def fix_aacp(t):
    if MARK_AACP in t:
        return t
    # ini file: full path, so it is written where we expect it
    t = sub_once(
        t,
        r'([ \t]*)char_t\s+\*conf_file = "altacast_aacp\.ini";[^\n]*\n',
        lambda m: (
            m.group(1) + "char_t\t\t\tconf_file_buf[MAX_PATH] = \"\";\t/* %s */\n" % MARK_AACP
            + m.group(1) + "char_t\t\t\t*conf_file = conf_file_buf;\n"
            + m.group(1) + "{\n"
            + m.group(1) + "\tchar_t *confFilePart = NULL;\n"
            + m.group(1) + "\tif(GetFullPathName(\"altacast_aacp.ini\", MAX_PATH, conf_file_buf, &confFilePart) == 0) {\n"
            + m.group(1) + "\t\tstrcpy(conf_file_buf, \"altacast_aacp.ini\");\n"
            + m.group(1) + "\t}\n"
            + m.group(1) + "}\n"
        ),
        "libaltacast.cpp aacp ini path",
    )
    # channel mode: always set a value
    t = sub_once(
        t,
        r'([ \t]*)sprintf\(bitrateValue, "%d", bitrateLong\);\n',
        lambda m: m.group(0)
        + m.group(1) + 'strcpy(channelMode, (g->currentChannels == 2) ? "2" : "1");\t/* %s: default, no empty value */\n' % TAG,
        "libaltacast.cpp aacp channel mode default",
    )
    # extra keys
    t = sub_once(
        t,
        r'([ \t]*)WritePrivateProfileString\("audio_aacplus", "nsignallingmode", "0", conf_file\);\n',
        lambda m: m.group(0)
        + m.group(1) + 'WritePrivateProfileString("audio_aacplus", "signallingmode", "0", conf_file);\n'
        + m.group(1) + 'WritePrivateProfileString("audio_aacplus", "speech", "0", conf_file);\n'
        + m.group(1) + 'WritePrivateProfileString("audio_aacplus", "pns", "0", conf_file);\n',
        "libaltacast.cpp aacp extra keys",
    )
    # log the real reason
    t = sub_once(
        t,
        r'([ \t]*)LogMessage\(g,LOG_ERROR, "Invalid AAC\+ settings"\);\n',
        lambda m: (
            m.group(1) + "{\n"
            + m.group(1) + "\tchar_t aacpDiag[1024];\n"
            + m.group(1) + "\tsprintf(aacpDiag, \"Invalid AAC+ settings (channels=%d samplerate=%d bitrate=%ld channelmode=%s ini=%s)\",\n"
            + m.group(1) + "\t\t(int) g->currentChannels, (int) g->currentSamplerate, bitrateLong, channelMode, conf_file);\n"
            + m.group(1) + "\tLogMessage(g,LOG_ERROR, aacpDiag);\n"
            + m.group(1) + "}\n"
        ),
        "libaltacast.cpp aacp log",
    )
    # status line
    t = sub_once(
        t,
        r'(g->serverStatusCallback\(g, \(void \*\) "cannot find libfaac\.dll"\);\n[ \t]*\}\n[ \t]*else \{\n)([ \t]*)g->serverStatusCallback\(g, \(void \*\) "Encoder init failed"\);\n',
        lambda m: m.group(1)
        + m.group(2) + "if(g->gAACPFlag) {\n"
        + m.group(2) + '\tg->serverStatusCallback(g, (void *) "AAC Plus init failed (see log)");\n'
        + m.group(2) + "}\n"
        + m.group(2) + "else {\n"
        + m.group(2) + '\tg->serverStatusCallback(g, (void *) "Encoder init failed");\n'
        + m.group(2) + "}\n",
        "libaltacast.cpp init failed status",
    )
    return t


# Config.cpp first (anchor), then the rest
patch_file("Config.cpp", fix_lock_config)
patch_file("BasicSettings.h", fix_lock_header)
patch_file("BasicSettings.cpp", fix_lock_cpp)
patch_file("altacastStandalone.cpp", fix_instance)
patch_file("libaltacast/libaltacast.cpp", lambda t: fix_aacp(fix_flac_ext(t)))
print(TAG + ": done")
