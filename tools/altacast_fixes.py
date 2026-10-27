#!/usr/bin/env python3
"""
AltaCast stability fixes, applied at build time.

  1. fflush      - the recording archive file is flushed after every write, so a crash
                   or power loss does not lose the last buffered part of the recording.
  2. metadata    - updateSongTitle() fixes:
     / password    * URLize() works IN PLACE. Calling it on g->gPassword permanently
                     turned the password into its URL-encoded form after the first
                     song-title update. A copy is encoded instead, so g->gPassword stays
                     intact AND the request still carries the correctly encoded password.
                   * The song title had the same problem and, worse, URL-encoding makes
                     it up to 3 times longer, which could overflow the 1024 byte
                     buffers for long titles. The title is now encoded from a copy into
                     a 4096 byte buffer, and the request buffer is 16384 bytes
                     (same size as ShuiCast uses).
  3. recording   - when the recording device is lost (RDP / Terminal Server session
     recovery      change, USB device unplugged, driver reset) the BASS record channel
                   stops. The device is re-opened, but only every RETRY_INTERVAL_MS and
                   at most MAX_TRIES times in a row. When it gives up a status message is
                   shown once; re-selecting the device (or any successful restart)
                   re-arms the retries. Standalone build only.
  4. long-run    - the "3 hour bug": after about 4 GB of recorded data (BASS 2.4.1 is
     restart       bundled) the audio can distort. The recorded bytes are counted and the
                   recording is restarted shortly before the limit (every ~6h45m with the
                   16 bit / 44.1 kHz stereo input AltaCast uses). Same idea as ShuiCast's
                   ThreeHourBug option. Standalone build only. Set
                   ENABLE_LONG_RUN_RESTART = False to leave it out.
  5. AAC         - * AAC Quality 0 / empty (what the field holds after switching the
     settings       encoder type to AAC) gave a very distorted 24 kbps stream. It is now
                    100 in the dialog and, as a safety net, in the encoder.
                  * "Use bitrate" (Ogg Vorbis only) stayed ticked after switching to AAC
                    and then disabled Quality and enabled the unused Bitrate field. For
                    AAC only Quality is editable now.
                  * Ogg Vorbis Quality is 0..10 (100 would mean maximum quality, about
                    500 kbps). The AAC value no longer carries over to Ogg, and the initial
                    Ogg quality is 3 (oggenc's default, about 112 kbps) instead of 0.
                    Change OGG_DEFAULT_QUALITY in this script to use another value.
  6. quality     - The dialog has ONE Quality field for AAC (10..500) and for Ogg Vorbis /
     memory        MP3 / FLAC (0..10). Both values are remembered per encoder type: switching
                   the type restores that type's own value (AAC 100, Ogg 3 at the start),
                   and OK saves both.

Usage:  python altacast_fixes.py <path to trunk/AltaCast/src>

Idempotent (already applied -> skipped). If an anchor cannot be found the script stops
with an error, so a silent half-patch cannot happen. Can run before or after
ladio_patch.py.
"""
import re
import sys
from pathlib import Path

# ---- tuning ---------------------------------------------------------------
RETRY_INTERVAL_MS = 3000        # wait between two attempts to re-open the device
MAX_TRIES = 20                  # attempts in a row before giving up (20 x 3 s = about 1 minute)
ENABLE_LONG_RUN_RESTART = True  # restart recording before the 4 GB limit ("3 hour bug")
LONG_RUN_LIMIT_BYTES = "0xFF000000UL"   # same threshold as ShuiCast
# ---------------------------------------------------------------------------

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else ".")


def fail(msg):
    print("altacast_fixes: ERROR: " + msg, file=sys.stderr)
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


# ------------------------------------------------------------------ 1. fflush
def fix_fflush(t):
    if re.search(r"fwrite\(data, length, 1, g->gSaveFile\);\s*fflush\(g->gSaveFile\);", t):
        print("altacast_fixes: fflush already present, skipped")
        return t
    t = sub_once(
        t,
        r"([ \t]*)fwrite\(data, length, 1, g->gSaveFile\);\n",
        lambda m: m.group(0)
        + m.group(1)
        + "fflush(g->gSaveFile);\t/* altacast_fixes: keep the archive file current */\n",
        "libaltacast.cpp archive fwrite",
    )
    print("altacast_fixes: fflush added")
    return t


# --------------------------------------------------- 2. password / song title
PW_BLOCK = """{i}/*
{i} * altacast_fixes: URLize() works in place. Encode a copy, otherwise g->gPassword
{i} * itself is overwritten with its URL-encoded form.
{i} */
{i}{{
{i}	char_t	pwCopy[1024] = "";

{i}	strncpy(pwCopy, g->gPassword, sizeof(pwCopy) - 1);
{i}	memset(URLPassword, '\\000', sizeof(URLPassword));
{i}	URLize(pwCopy, URLPassword, sizeof(pwCopy), sizeof(URLPassword));
{i}}}
"""

SONG_BLOCK = """{i}/*
{i} * altacast_fixes: same for the song title. Encoding can make it 3 times longer, so
{i} * work on a 4096 byte copy (the title itself is at most 1023 bytes).
{i} */
{i}{{
{i}	char_t	songCopy[4096] = "";

{i}	strncpy(songCopy, g->gCurrentSong, sizeof(songCopy) - 1);
{i}	memset(URLSong, '\\000', sizeof(URLSong));
{i}	URLize(songCopy, URLSong, sizeof(songCopy), sizeof(URLSong));
{i}}}
"""


def fix_password(t):
    if "altacast_fixes: URLize() works in place" in t:
        print("altacast_fixes: password/song fix already present, skipped")
        return t
    marker = "int updateSongTitle(altacastGlobals *g, int forceURL) {"
    pos = t.find(marker)
    if pos < 0:
        fail("libaltacast.cpp: updateSongTitle() not found")
    head, tail = t[:pos], t[pos:]
    tail = sub_once(
        tail,
        r"([ \t]*)URLize\(g->gPassword, URLPassword, sizeof\(g->gPassword\), sizeof\(URLPassword\)\);\n",
        lambda m: PW_BLOCK.format(i=m.group(1)),
        "libaltacast.cpp URLize(g->gPassword ...)",
    )
    tail = sub_once(
        tail,
        r"([ \t]*)URLize\(g->gCurrentSong, URLSong, sizeof\(g->gSongTitle\), sizeof\(URLSong\)\);\n",
        lambda m: SONG_BLOCK.format(i=m.group(1)),
        "libaltacast.cpp URLize(g->gCurrentSong ...)",
    )
    tail = sub_once(
        tail, r"URLPassword\[255\]", "URLPassword[1024]", "libaltacast.cpp URLPassword size"
    )
    tail = sub_once(
        tail, r"URLSong\[1024\]", "URLSong[4096]", "libaltacast.cpp URLSong size"
    )
    tail = sub_once(
        tail, r"contentString\[2056\]", "contentString[16384]", "libaltacast.cpp contentString size"
    )
    print("altacast_fixes: password/song fix added (buffers: request 16384, song 4096)")
    return head + tail


# ---------------------------------------------------------------- 3. recording
LONG_RUN_RESTART_CODE = """
#ifdef ALTACAST_LONG_RUN_RESTART
			if(altacastRecRestart) {
				generalStatusCallback((void *) "Restarting recording (long-run workaround for the 4 GB limit)");
				stopRecording();
				startRecording(m_CurrentInputCard);
			}
#endif"""

REC_BLOCK = """#ifdef ALTACASTSTANDALONE
	/*
	 * altacast_fixes: recording device recovery.
	 * When the recording device is lost the BASS record channel stops. Re-open it, but
	 * only every ALTACAST_REC_RETRY_MS and at most ALTACAST_REC_MAX_TRIES times in a
	 * row, so a device that stays away does not cause a flood of init attempts and
	 * status messages. The counter is re-armed as soon as recording works again.
	 * Also restarts the recording before the 4 GB limit (see BASSwaveInputProc).
	 */
#define ALTACAST_REC_RETRY_MS	%(retry)d
#define ALTACAST_REC_MAX_TRIES	%(tries)d
	if(nIDEvent == 73) {
		static int		recTries = 0;
		static DWORD	recLastTry = 0;

		if(BASS_ChannelIsActive(inRecHandle) != BASS_ACTIVE_STOPPED) {
			recTries = 0;%(longrun)s
		}
		else if(recTries < ALTACAST_REC_MAX_TRIES &&
				(recTries == 0 || GetTickCount() - recLastTry >= ALTACAST_REC_RETRY_MS)) {
			recTries++;
			recLastTry = GetTickCount();
			stopRecording();
			startRecording(m_CurrentInputCard);

			if(recTries >= ALTACAST_REC_MAX_TRIES && BASS_ChannelIsActive(inRecHandle) == BASS_ACTIVE_STOPPED) {
				generalStatusCallback((void *) "Recording device lost - gave up retrying. Select the device again to resume.");
			}
		}
	}
#undef ALTACAST_REC_RETRY_MS
#undef ALTACAST_REC_MAX_TRIES
#endif

""" % {
    "retry": RETRY_INTERVAL_MS,
    "tries": MAX_TRIES,
    "longrun": (LONG_RUN_RESTART_CODE if ENABLE_LONG_RUN_RESTART else ""),
}

COUNTER_DECL = """/*
 * altacast_fixes: long-run restart. Counts the recorded bytes; the timer restarts the
 * recording before BASS reaches 4 GB (the "3 hour bug").
 */
#define ALTACAST_LONG_RUN_RESTART
static DWORD			altacastRecBytes = 0;
static volatile LONG	altacastRecRestart = 0;

"""

COUNTER_CODE = """	altacastRecBytes += length;	/* altacast_fixes: long-run restart */
	if(altacastRecBytes > %s) {
		altacastRecRestart = 1;
	}

""" % LONG_RUN_LIMIT_BYTES

COUNTER_RESET = "\taltacastRecBytes = 0;\t/* altacast_fixes: long-run restart */\n\taltacastRecRestart = 0;\n"


def fix_recording(t):
    if "altacast_fixes: recording device recovery" in t:
        print("altacast_fixes: recording recovery already present, skipped")
        return t
    if re.search(r"if\s*\(\s*!\s*BASS_ChannelIsActive\(\s*inRecHandle\s*\)\s*\)", t):
        fail("MainWindow.cpp already contains an unconditional BASS_ChannelIsActive() restart "
             "(ImagoTrigger fork?). Remove it first, otherwise the retry limit is bypassed.")
    t = sub_once(
        t,
        r"(void CMainWindow::OnTimer\(UINT nIDEvent\)\s*\{\n)(\n?)",
        lambda m: m.group(1) + REC_BLOCK,
        "MainWindow.cpp OnTimer",
    )
    if ENABLE_LONG_RUN_RESTART:
        t = sub_once(
            t,
            r"(BOOL CALLBACK BASSwaveInputProc\(HRECORD handle, const void \*buffer, DWORD length, void \*user\) \{\n)",
            lambda m: COUNTER_DECL + m.group(1) + COUNTER_CODE,
            "MainWindow.cpp BASSwaveInputProc",
        )
        t = sub_once(
            t,
            r"([ \t]*inRecHandle = BASS_RecordStart\(44100, 2, 0, &BASSwaveInputProc, NULL\);\n)",
            lambda m: m.group(1) + COUNTER_RESET,
            "MainWindow.cpp BASS_RecordStart",
        )
    print("altacast_fixes: recording recovery added (retry every %d ms, max %d tries)"
          % (RETRY_INTERVAL_MS, MAX_TRIES))
    if ENABLE_LONG_RUN_RESTART:
        print("altacast_fixes: long-run restart added (limit %s bytes)" % LONG_RUN_LIMIT_BYTES)
    return t


for _name, _blk in (("REC_BLOCK", REC_BLOCK), ("PW_BLOCK", PW_BLOCK), ("SONG_BLOCK", SONG_BLOCK),
                    ("COUNTER_DECL", COUNTER_DECL), ("COUNTER_CODE", COUNTER_CODE)):
    if not _blk.isascii():
        fail("internal: inserted C++ (%s) must be ASCII" % _name)

# ----------------------------------------------------------- 5. AAC settings
MARK_AAC = "altacast_fixes: aac"

AAC_UI_BLOCK = """if (m_EncoderType == "AAC") {
\t\t/* %(m)s: faac is quality based. The bitrate and "Use bitrate" (Ogg only)
\t\t * are ignored, so only Quality is editable, and an empty/0 Quality
\t\t * (what the field holds after switching from another type) becomes 100. */
\t\tm_BitrateCtrl.EnableWindow(FALSE);
\t\tm_QualityCtrl.EnableWindow(TRUE);
\t\tif (m_QualityCtrl.GetSafeHwnd()) {
\t\t\tCString	aacQuality;

\t\t\tm_QualityCtrl.GetWindowText(aacQuality);
\t\t\tif (atoi(LPCSTR(aacQuality)) <= 0) {
\t\t\t\tm_QualityCtrl.SetWindowText(_T("100"));
\t\t\t\tm_Quality = _T("100");
\t\t\t}
\t\t}
""" % {"m": MARK_AAC}


def fix_aac_ui(t):
    if MARK_AAC in t:
        print("altacast_fixes: AAC settings (UI) already patched, skipped")
        return t
    t = sub_once(
        t,
        r'if \(m_EncoderType == "AAC"\) \{\n'
        r"\s*if \(m_UseBitrate\) \{\n\s*m_BitrateCtrl\.EnableWindow\(TRUE\);\n\s*m_QualityCtrl\.EnableWindow\(FALSE\);\n\s*\}\n"
        r"\s*else \{\n\s*m_QualityCtrl\.EnableWindow\(TRUE\);\n\s*m_BitrateCtrl\.EnableWindow\(FALSE\);\n\s*\}\n",
        lambda m: AAC_UI_BLOCK,
        "BasicSettings.cpp AAC branch of UpdateFields",
    )
    print("altacast_fixes: AAC settings (UI) fixed: Quality only, empty/0 -> 100")
    return t


def fix_aac_quality(t):
    if MARK_AAC in t:
        print("altacast_fixes: AAC quality default already patched, skipped")
        return t
    t = sub_once(
        t,
        r"([ \t]*)m_pConfig->quantqual = atoi\(g->gAACQuality\);\n",
        lambda m: m.group(1) + "m_pConfig->quantqual = (atoi(g->gAACQuality) > 0) ? atoi(g->gAACQuality) : 100;\t/* "
        + MARK_AAC + ": 0/empty -> 100 */\n",
        "libaltacast.cpp quantqual",
    )
    print("altacast_fixes: AAC quality 0/empty -> 100 in the encoder")
    return t


# Ogg Vorbis Quality is 0..10 (x0.1 -> libvorbis 0.0..1.0; anything >= 10 is clamped to the
# maximum, about 500 kbps). The Quality field is shared with AAC (10..500), so the value 100
# set for AAC must not carry over to Ogg. Ogg default: 3 = what oggenc uses (about 112 kbps).
OGG_DEFAULT_QUALITY = "3"
MARK_OGG = "altacast_fixes: ogg"

OGG_UI_BLOCK = """\t\t/* %(m)s: Quality is 0..10 here. An empty value or the AAC value (100)
\t\t * must not carry over - 10 or more means maximum quality (about 500 kbps). */
\t\tif (m_QualityCtrl.GetSafeHwnd()) {
\t\t\tCString	oggQuality;

\t\t\tm_QualityCtrl.GetWindowText(oggQuality);
\t\t\tif (oggQuality.IsEmpty() || atof(LPCSTR(oggQuality)) < 0 || atof(LPCSTR(oggQuality)) > 10) {
\t\t\t\tm_QualityCtrl.SetWindowText(_T("%(q)s"));
\t\t\t\tm_Quality = _T("%(q)s");
\t\t\t}
\t\t}
""" % {"m": MARK_OGG, "q": OGG_DEFAULT_QUALITY}


def fix_ogg_ui(t):
    if MARK_OGG in t:
        print("altacast_fixes: Ogg quality (UI) already patched, skipped")
        return t
    t = sub_once(
        t,
        r'(if \(m_EncoderType == "OggVorbis"\) \{\n\s*m_UseBitrateCtrl\.EnableWindow\(TRUE\);\n)',
        lambda m: m.group(1) + OGG_UI_BLOCK,
        "BasicSettings.cpp OggVorbis branch of UpdateFields",
    )
    print("altacast_fixes: Ogg quality kept within 0..10 (fallback %s)" % OGG_DEFAULT_QUALITY)
    return t


def fix_ogg_default(t):
    """Initial Ogg quality (no config file yet): 0 (about 64 kbps) -> OGG_DEFAULT_QUALITY."""
    if re.search(r'"OggQuality", "%s"' % re.escape(OGG_DEFAULT_QUALITY), t):
        print("altacast_fixes: Ogg default quality already %s, skipped" % OGG_DEFAULT_QUALITY)
        return t
    t = sub_once(
        t,
        r'("OggQuality", )"0"',
        lambda m: m.group(1) + '"%s"' % OGG_DEFAULT_QUALITY,
        "libaltacast.cpp OggQuality default",
    )
    print("altacast_fixes: Ogg default quality 0 -> %s" % OGG_DEFAULT_QUALITY)
    return t


# ----------------------------------------- 6. Quality remembered per encoder type
# The dialog has ONE Quality field for AAC (10..500) and for Ogg Vorbis / MP3 / FLAC (the
# 0..10 "OggQuality"). Both values are kept in the dialog, switched together with the
# encoder type, and both are saved with OK.
MARK_QMEM = "altacast_fixes: qmem"

QMEM_VALID_AAC = "atoi(LPCSTR(%s)) > 0"
QMEM_VALID_OGG = "!%(s)s.IsEmpty() && atof(LPCSTR(%(s)s)) >= 0 && atof(LPCSTR(%(s)s)) <= 10"


def fix_qmem_header(t):
    if MARK_QMEM in t:
        print("altacast_fixes: quality memory (header) already patched, skipped")
        return t
    return sub_once(
        t,
        r"([ \t]*CString[ \t]+m_Quality;\n)",
        lambda m: m.group(1)
        + "\tCString\tm_QualityAAC;\t// " + MARK_QMEM + ": Quality of the AAC type\n"
        + "\tCString\tm_QualityOgg;\t// Quality of Ogg Vorbis / MP3 / FLAC (0..10)\n",
        "BasicSettings.h m_Quality",
    )


QMEM_LOAD = (
    "    /* " + MARK_QMEM + ": both Quality values are known to the dialog */\n"
    "    basicSettings->m_QualityAAC = g->gAACQuality;\n"
    "    basicSettings->m_QualityOgg = g->gOggQuality;\n"
)

QMEM_SAVE = """    /* %(m)s: also store the Quality of the type that is not selected */
    if (g->gAACFlag) {
        if (%(ogg)s) {
            strcpy(g->gOggQuality, LPCSTR(basicSettings->m_QualityOgg));
        }
    }
    if (g->gLAMEFlag || g->gOggFlag || g->gFLACFlag) {
        if (%(aac)s) {
            strcpy(g->gAACQuality, LPCSTR(basicSettings->m_QualityAAC));
        }
    }
""" % {"m": MARK_QMEM,
       "ogg": QMEM_VALID_OGG % {"s": "basicSettings->m_QualityOgg"},
       "aac": QMEM_VALID_AAC % "basicSettings->m_QualityAAC"}


def fix_qmem_config(t):
    if MARK_QMEM in t:
        print("altacast_fixes: quality memory (Config.cpp) already patched, skipped")
        return t
    t = sub_once(
        t,
        r'([ \t]*currentEnc = g->encoderNumber;\n)(?=\s*sprintf\(buf, "%d", getCurrentBitrate\(g\)\);)',
        lambda m: m.group(1) + QMEM_LOAD,
        "Config.cpp currentEnc",
    )
    t = sub_once(
        t,
        r"([ \t]*if \(g->gOggFlag\) \{\n\s*strcpy\(g->gOggQuality, LPCSTR\(basicSettings->m_Quality\)\);\n\s*\}\n)",
        lambda m: m.group(1) + QMEM_SAVE,
        "Config.cpp gOggQuality save",
    )
    return t


QMEM_DIALOG = """m_QualityCtrl.EnableWindow(TRUE);
\t/* %(m)s: remember the Quality per encoder type. AAC has its own value,
\t * Ogg Vorbis / MP3 / FLAC share the Ogg one (0..10). */
\t{
\t\tCString\tleavingQuality;

\t\tm_QualityCtrl.GetWindowText(leavingQuality);
\t\tif (m_EncoderType == "AAC") {
\t\t\tm_QualityAAC = leavingQuality;
\t\t}
\t\telse if (m_EncoderType == "OggVorbis" || m_EncoderType == "MP3 Lame" || m_EncoderType == "Ogg FLAC") {
\t\t\tm_QualityOgg = leavingQuality;
\t\t}
\t}
\tm_EncoderType = selectedString;
\t{
\t\tCString\tenteringQuality;
\t\tbool\tsetQuality = false;

\t\tif (m_EncoderType == "AAC") {
\t\t\tenteringQuality = (%(aac)s) ? m_QualityAAC : CString(_T("%(aacdef)s"));
\t\t\tsetQuality = true;
\t\t}
\t\telse if (m_EncoderType == "OggVorbis" || m_EncoderType == "MP3 Lame" || m_EncoderType == "Ogg FLAC") {
\t\t\tenteringQuality = (%(ogg)s) ? m_QualityOgg : CString(_T("%(oggdef)s"));
\t\t\tsetQuality = true;
\t\t}
\t\tif (setQuality && m_QualityCtrl.GetSafeHwnd()) {
\t\t\tm_QualityCtrl.SetWindowText(enteringQuality);
\t\t\tm_Quality = enteringQuality;
\t\t}
\t}
\t"""


def fix_qmem_dialog(t):
    if MARK_QMEM in t:
        print("altacast_fixes: quality memory (dialog) already patched, skipped")
        return t
    block = QMEM_DIALOG % {
        "m": MARK_QMEM,
        "aac": QMEM_VALID_AAC % "m_QualityAAC",
        "aacdef": "100",
        "ogg": QMEM_VALID_OGG % {"s": "m_QualityOgg"},
        "oggdef": OGG_DEFAULT_QUALITY,
    }
    t = sub_once(
        t,
        r"m_QualityCtrl\.EnableWindow\(TRUE\);\n\s*m_EncoderType = selectedString;\n\s*",
        lambda m: block,
        "BasicSettings.cpp OnSelendokEncoderType",
    )
    print("altacast_fixes: Quality remembered per encoder type")
    return t


for _name, _blk in (("QMEM_LOAD", QMEM_LOAD), ("QMEM_SAVE", QMEM_SAVE), ("QMEM_DIALOG", QMEM_DIALOG)):
    if not _blk.isascii():
        fail("internal: inserted C++ (%s) must be ASCII" % _name)

patch_file("Config.cpp", fix_qmem_config)
patch_file("BasicSettings.h", fix_qmem_header)
patch_file("BasicSettings.cpp", lambda t: fix_qmem_dialog(fix_ogg_ui(fix_aac_ui(t))))
patch_file("libaltacast/libaltacast.cpp",
           lambda t: fix_ogg_default(fix_aac_quality(fix_password(fix_fflush(t)))))
patch_file("MainWindow.cpp", fix_recording)
print("altacast_fixes: done")
