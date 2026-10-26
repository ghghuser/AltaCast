#!/usr/bin/env python3
"""
AltaCast: add Netladio (ねとらじ) support.

  * YP Settings gets two new fields: "DJ Name" and "Charset" (none / sjis / utf8)
  * Both are stored in the encoder config file
    (ServerLadioDJ / ServerLadioCharset)
  * On the Icecast2 (ICE/1.0) SOURCE connection an extra header is sent:
        x-ladio-info: charset=sjis;dj=<URL-encoded DJ name>
    When charset is sjis/utf8, the stream name / URL / genre / description
    headers are converted to that character set as well, so they match what
    x-ladio-info announces.

Also rewrites the recording file name to "[YYYYMMDD] <Stream Name>_hhmmss" in a
multibyte-safe way (Shift_JIS characters whose 2nd byte is 0x5C are kept).

Usage:  python ladio_patch.py <path to trunk/AltaCast/src>

The script is idempotent (a file that already contains "Ladio" is skipped) and
fails loudly if an anchor cannot be found, so a silent half-patch cannot happen.
"""
import re
import sys
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else ".")


def fail(msg):
    print("ladio_patch: ERROR: " + msg, file=sys.stderr)
    sys.exit(1)


def edit(text, pattern, insert, where, label, flags=0):
    found = list(re.finditer(pattern, text, flags))
    if len(found) != 1:
        fail("%s: expected exactly 1 match, found %d" % (label, len(found)))
    m = found[0]
    if where == "after":
        return text[: m.end()] + insert + text[m.end():]
    if where == "before":
        return text[: m.start()] + insert + text[m.start():]
    if where == "replace":
        return text[: m.start()] + insert + text[m.end():]
    fail("bad mode " + where)


def patch_file(name, func):
    path = SRC / name
    if not path.exists():
        fail("%s not found under %s" % (name, SRC))
    raw = path.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("latin-1").replace("\r\n", "\n")
    if "ladio" in text.lower():
        print("ladio_patch: %s already patched, skipped" % name)
        return
    text = func(text)
    if crlf:
        text = text.replace("\n", "\r\n")
    path.write_bytes(text.encode("latin-1"))
    print("ladio_patch: patched %s" % name)


# ---------------------------------------------------------------- resource.h
def p_resource_h(t):
    return edit(
        t,
        r"#define IDC_CONFIGPATH[^\n]*\n",
        "#define IDC_LADIODJ\t\t\t\t\t\t1104\n"
        "#define IDC_LADIOCHARSET\t\t\t\t1105\n",
        "after",
        "resource.h IDC_CONFIGPATH",
    )


# ----------------------------------------------------------------- altacast.rc
def p_rc(t):
    # "DJ Name" and "Charset" go below IRC, one per row. The page is only 211 units
    # high (the tab control ends right below it), so the group box grows by 3 units
    # and the two new rows are packed tightly.
    t = edit(
        t,
        r'GROUPBOX\s+"YP Settings",IDC_STATIC,7,7,314,197',
        'GROUPBOX        "YP Settings",IDC_STATIC,7,7,314,200',
        "replace",
        "altacast.rc YP Settings group box",
    )
    return edit(
        t,
        r"    EDITTEXT\s+IDC_IRC,[^\n]*\n",
        '    RTEXT           "DJ Name",IDC_STATIC,43,179,44,8\n'
        "    EDITTEXT        IDC_LADIODJ,93,176,117,14,ES_AUTOHSCROLL\n"
        '    RTEXT           "Charset",IDC_STATIC,43,196,44,8\n'
        "    COMBOBOX        IDC_LADIOCHARSET,93,193,70,60,"
        "CBS_DROPDOWNLIST | WS_VSCROLL | WS_TABSTOP\n",
        "after",
        "altacast.rc IDC_IRC",
    )


# ---------------------------------------------------------------- YPSettings.h
def p_yp_h(t):
    return edit(
        t,
        r"\tCString\tm_StreamAIM;\n",
        "\tCString\tm_LadioDJ;\n"
        "\tint\t\tm_LadioCharset;\t// 0 = none, 1 = sjis, 2 = utf8\n"
        "\tCComboBox\tm_LadioCharsetCtrl;\n",
        "after",
        "YPSettings.h m_StreamAIM",
    )


# -------------------------------------------------------------- YPSettings.cpp
def p_yp_cpp(t):
    t = edit(
        t,
        r"\tm_StreamAIM = _T\(\"\"\);\n",
        '\tm_LadioDJ = _T("");\n\tm_LadioCharset = 0;\n',
        "after",
        "YPSettings.cpp ctor",
    )
    t = edit(
        t,
        r"\tDDX_Text\(pDX, IDC_AIM, m_StreamAIM\);\n",
        "\tDDX_Control(pDX, IDC_LADIOCHARSET, m_LadioCharsetCtrl);\n"
        "\tDDX_Text(pDX, IDC_LADIODJ, m_LadioDJ);\n"
        "\tDDX_CBIndex(pDX, IDC_LADIOCHARSET, m_LadioCharset);\n",
        "after",
        "YPSettings.cpp DDX",
    )
    t = edit(
        t,
        r"BOOL CYPSettings::OnInitDialog\(\)\s*\{\s*CDialog::OnInitDialog\(\);\n",
        "\n"
        "\t// Netladio (x-ladio-info) fields\n"
        "\tm_LadioCharsetCtrl.ResetContent();\n"
        '\tm_LadioCharsetCtrl.AddString("(none)");\n'
        '\tm_LadioCharsetCtrl.AddString("sjis");\n'
        '\tm_LadioCharsetCtrl.AddString("utf8");\n'
        "\tif (m_LadioCharset < 0 || m_LadioCharset > 2) {\n"
        "\t\tm_LadioCharset = 0;\n"
        "\t}\n"
        "\tm_LadioCharsetCtrl.SetCurSel(m_LadioCharset);\n"
        "\tCWnd *pDJ = GetDlgItem(IDC_LADIODJ);\n"
        "\tif (pDJ) {\n"
        "\t\tpDJ->SendMessage(EM_LIMITTEXT, 250, 0);\n"
        "\t}\n",
        "after",
        "YPSettings.cpp OnInitDialog",
    )
    return t


# ------------------------------------------------------------------- Config.cpp
def p_config_cpp(t):
    t = edit(
        t,
        r"    ypSettings->m_StreamIRC = g->gServIRC;\n",
        "    ypSettings->m_LadioDJ = g->gLadioDJ;\n"
        '    if (!strcmp(g->gLadioCharset, "sjis")) {\n'
        "        ypSettings->m_LadioCharset = 1;\n"
        "    }\n"
        '    else if (!strcmp(g->gLadioCharset, "utf8")) {\n'
        "        ypSettings->m_LadioCharset = 2;\n"
        "    }\n"
        "    else {\n"
        "        ypSettings->m_LadioCharset = 0;\n"
        "    }\n",
        "after",
        "Config.cpp dialog <- globals",
    )
    t = edit(
        t,
        r"    strcpy\(g->gServIRC, LPCSTR\(ypSettings->m_StreamIRC\)\);\n",
        "    strncpy(g->gLadioDJ, LPCSTR(ypSettings->m_LadioDJ), sizeof(g->gLadioDJ) - 1);\n"
        "    g->gLadioDJ[sizeof(g->gLadioDJ) - 1] = '\\000';\n"
        "    if (ypSettings->m_LadioCharset == 1) {\n"
        '        strcpy(g->gLadioCharset, "sjis");\n'
        "    }\n"
        "    else if (ypSettings->m_LadioCharset == 2) {\n"
        '        strcpy(g->gLadioCharset, "utf8");\n'
        "    }\n"
        "    else {\n"
        "        g->gLadioCharset[0] = '\\000';\n"
        "    }\n",
        "after",
        "Config.cpp globals <- dialog",
    )
    return t


# ------------------------------------------------------------------ libaltacast.h
def p_lib_h(t):
    return edit(
        t,
        r"\tchar_t\t\tgServGenre\[100\];\n",
        "\tchar_t\t\tgLadioDJ[256];\t\t/* Netladio DJ name (x-ladio-info) */\n"
        "\tchar_t\t\tgLadioCharset[10];\t/* \"\", \"sjis\" or \"utf8\" */\n",
        "after",
        "libaltacast.h gServGenre",
    )


LADIO_HELPERS = r'''/*
 * Netladio (x-ladio-info) support
 *
 * x-ladio-info: charset=<sjis|utf8>;dj=<URL-encoded DJ name>
 */

/* Code page for the configured charset; 0 = leave strings untouched */
static int ladioCodePage(const char *charset) {
#ifdef WIN32
	if(!strcmp(charset, "utf8")) {
		return 65001;	/* CP_UTF8 */
	}

	if(!strcmp(charset, "sjis")) {
		return 932;	/* Shift_JIS */
	}
#endif
	return 0;
}

/* Convert from the local (ANSI) code page to codePage. Falls back to a plain copy. */
static void ladioConvert(const char *src, int codePage, char *dst, int dstSize) {
	dst[0] = '\000';
#ifdef WIN32
	if(codePage != 0 && src[0] != '\000') {
		wchar_t	wbuf[2048];
		int		wlen = MultiByteToWideChar(CP_ACP, 0, src, -1, wbuf, 2048);

		if(wlen > 0) {
			int	blen = WideCharToMultiByte((UINT) codePage, 0, wbuf, -1, dst, dstSize, NULL, NULL);

			if(blen > 0) {
				return;
			}
		}
	}
#endif
	strncpy(dst, src, dstSize - 1);
	dst[dstSize - 1] = '\000';
}

static void ladioUrlEncode(const char *src, char *dst, int dstSize) {
	static const char	hex[] = "0123456789ABCDEF";
	int					o = 0;

	for(; *src && o + 4 < dstSize; src++) {
		unsigned char	c = (unsigned char) *src;

		if((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
		   c == '-' || c == '_' || c == '.' || c == '~') {
			dst[o++] = (char) c;
		}
		else {
			dst[o++] = '%';
			dst[o++] = hex[c >> 4];
			dst[o++] = hex[c & 15];
		}
	}

	dst[o] = '\000';
}

/*
 * Builds the complete "x-ladio-info: ...\n" header line (empty string when
 * nothing is configured). Returns the code page the other YP headers have to
 * be converted to (0 = send them unchanged).
 */
static int ladioMakeHeader(altacastGlobals *g, char *header, int headerSize) {
	char	djConv[1024] = "";
	char	djEnc[3200] = "";
	char	info[3400] = "";
	int		codePage = ladioCodePage(g->gLadioCharset);

	header[0] = '\000';

	if(codePage != 0) {
		sprintf(info, "charset=%s", g->gLadioCharset);
	}

	if(g->gLadioDJ[0] != '\000') {
		ladioConvert(g->gLadioDJ, codePage, djConv, sizeof(djConv));
		ladioUrlEncode(djConv, djEnc, sizeof(djEnc));
		const char	*sep = info[0] ? ";" : "";

		sprintf(info + strlen(info), "%sdj=%s", sep, djEnc);
	}

	if(info[0] != '\000' && (int) strlen(info) + 20 < headerSize) {
		sprintf(header, "x-ladio-info: %s\n", info);
	}

	return codePage;
}

'''

ICE2_BLOCK = '''char	ladioInfo[4096] = "";
				char	ladioName[1024] = "";
				char	ladioURL[1024] = "";
				char	ladioGenre[1024] = "";
				char	ladioDesc[1024] = "";
				int		ladioCP = ladioMakeHeader(g, ladioInfo, sizeof(ladioInfo));

				ladioConvert(g->gServName, ladioCP, ladioName, sizeof(ladioName));
				ladioConvert(g->gServURL, ladioCP, ladioURL, sizeof(ladioURL));
				ladioConvert(g->gServGenre, ladioCP, ladioGenre, sizeof(ladioGenre));
				ladioConvert(g->gServDesc, ladioCP, ladioDesc, sizeof(ladioDesc));

				sprintf(contentString,
						"SOURCE %s ICE/1.0\\ncontent-type: %s\\nAuthorization: Basic %s\\nice-name: %s\\nice-url: %s\\nice-genre: %s\\nice-bitrate: %s\\nice-private: %d\\nice-public: %d\\nice-description: %s\\nice-audio-info: %s\\n%s\\n",
					g->gMountpoint,
						contentType,
						puserAuthbase64,
						ladioName,
						ladioURL,
						ladioGenre,
						ypbrate,
						!g->gPubServ,
						g->gPubServ,
						ladioDesc,
						audioInfo,
						ladioInfo);'''


# Recording file name: "[YYYYMMDD] <Stream Name>_hhmmss"  (multibyte safe)
FILENAME_BLOCK = r'''/* Recording file name: "[YYYYMMDD] <Stream Name>_hhmmss" */
	{
		char_t	rawName[1024] = "";

		snprintf(rawName, sizeof(rawName), "[%04d%02d%02d] %s_%02d%02d%02d",
				 newtime->tm_year + 1900, newtime->tm_mon + 1, newtime->tm_mday, g->gServName,
				 newtime->tm_hour, newtime->tm_min, newtime->tm_sec);

		/*
		 * Remove characters that are not allowed in file names. Only whole
		 * multibyte characters are looked at, so a Shift_JIS trail byte of 0x5C
		 * is kept intact.
		 */
		memset(outputFile, '\000', sizeof(outputFile));
		{
			const unsigned char	*p = (const unsigned char *) rawName;
			int					o = 0;

			while(*p && o < (int) sizeof(outputFile) - 16) {	/* leave room for the extension */
#ifdef WIN32
				if(IsDBCSLeadByte(*p) && p[1]) {
					outputFile[o++] = (char) *p++;
					outputFile[o++] = (char) *p++;
					continue;
				}
#endif
				char	c = (char) *p++;

				if(c == '"') {
					c = '\'';
				}
				else if(strchr("\\/:*?<>|\r\n", c)) {
					continue;
				}

				outputFile[o++] = c;
			}
		}
	}
	memset(outFilename, '\000', sizeof(outFilename));
	strcpy(outFilename, outputFile);'''


# --------------------------------------------------------------- libaltacast.cpp
def p_lib_cpp(t):
    # recording file name: replaces the sprintf (original or an earlier PowerShell patch)
    # and the whole ReplaceString() chain, inside openArchiveFile() only
    marker = "int openArchiveFile(altacastGlobals *g) {"
    pos = t.find(marker)
    if pos < 0:
        fail("libaltacast.cpp: openArchiveFile() not found")
    head, tail = t[:pos], t[pos:]
    tail = edit(
        tail,
        r"sprintf\(\s*outFilename\s*,[^;]*;.*?strcpy\(outputFile, outFilename\);",
        FILENAME_BLOCK,
        "replace",
        "libaltacast.cpp openArchiveFile",
        re.S,
    )
    t = head + tail
    # helpers, right before connectToServer()
    t = edit(
        t,
        r"int connectToServer\(altacastGlobals \*g\) \{\n",
        LADIO_HELPERS,
        "before",
        "libaltacast.cpp connectToServer",
    )
    # contentString only had 1024 bytes
    t = edit(
        t,
        r"contentString\[1024\]",
        "contentString[8192]",
        "replace",
        "libaltacast.cpp contentString size",
    )
    # the ICE/1.0 SOURCE request
    t = edit(
        t,
        r'sprintf\(contentString,\s*"SOURCE %s ICE/1\.0.*?audioInfo\);',
        ICE2_BLOCK,
        "replace",
        "libaltacast.cpp ICE/1.0 request",
        re.S,
    )
    # init
    t = edit(
        t,
        r"\tmemset\(g->gServDesc, '\\000', sizeof\(g->gServDesc\)\);\n",
        "\tmemset(g->gLadioDJ, '\\000', sizeof(g->gLadioDJ));\n"
        "\tmemset(g->gLadioCharset, '\\000', sizeof(g->gLadioCharset));\n",
        "after",
        "libaltacast.cpp init",
    )
    # read config
    t = edit(
        t,
        r"\tGetConfigVariable\(g, g->gAppName, \"ServerGenre\",[^\n]*\n",
        '\tGetConfigVariable(g, g->gAppName, "ServerLadioDJ", "", g->gLadioDJ, sizeof(g->gLadioDJ), NULL);\n'
        '\tGetConfigVariable(g, g->gAppName, "ServerLadioCharset", "", g->gLadioCharset, sizeof(g->gLadioCharset), NULL);\n',
        "after",
        "libaltacast.cpp GetConfigVariable",
    )
    # write config
    t = edit(
        t,
        r"\tPutConfigVariable\(g, g->gAppName, \"ServerGenre\", g->gServGenre\);\n",
        '\tPutConfigVariable(g, g->gAppName, "ServerLadioDJ", g->gLadioDJ);\n'
        '\tPutConfigVariable(g, g->gAppName, "ServerLadioCharset", g->gLadioCharset);\n',
        "after",
        "libaltacast.cpp PutConfigVariable",
    )
    # config variable allow-list
    t = edit(
        t,
        r"    addConfigVariable\(g, \"ServerGenre\"\);\n",
        '    addConfigVariable(g, "ServerLadioDJ");\n'
        '    addConfigVariable(g, "ServerLadioCharset");\n',
        "after",
        "libaltacast.cpp addConfigVariable",
    )
    return t


patch_file("resource.h", p_resource_h)
patch_file("altacast.rc", p_rc)
patch_file("YPSettings.h", p_yp_h)
patch_file("YPSettings.cpp", p_yp_cpp)
patch_file("Config.cpp", p_config_cpp)
patch_file("libaltacast/libaltacast.h", p_lib_h)
patch_file("libaltacast/libaltacast.cpp", p_lib_cpp)
print("ladio_patch: done")
