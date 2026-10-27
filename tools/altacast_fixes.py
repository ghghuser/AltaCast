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


patch_file("libaltacast/libaltacast.cpp", lambda t: fix_aac_quality(fix_password(fix_fflush(t))))
patch_file("MainWindow.cpp", fix_recording)
patch_file("BasicSettings.cpp", fix_aac_ui)
print("altacast_fixes: done")
