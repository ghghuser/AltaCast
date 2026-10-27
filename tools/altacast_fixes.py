#!/usr/bin/env python3
"""
AltaCast stability fixes, applied at build time.

  1. fflush      - the recording archive file is flushed after every write, so a crash
                   or power loss does not lose the last buffered part of the recording.
  2. password    - URLize() works IN PLACE. Calling it on g->gPassword permanently turned
                   the password into its URL-encoded form after the first song-title
                   update (and the encoded form was then used for the next connection).
                   A copy is encoded instead, so g->gPassword stays intact AND the
                   request still carries the correctly encoded password.
                   (The encode buffers are also enlarged: the old 255 byte buffer could
                   overflow, because every special character grows to 3 bytes.)
  3. recording   - when the recording device is lost (RDP / Terminal Server session
     recovery      change, USB device unplugged, driver reset) the BASS record channel
                   stops. The device is re-opened, but only every RETRY_INTERVAL_MS and
                   at most MAX_TRIES times in a row. When it gives up a status message is
                   shown once; re-selecting the device (or any successful restart)
                   re-arms the retries. Standalone build only.

Usage:  python altacast_fixes.py <path to trunk/AltaCast/src>

Idempotent (already applied -> skipped). If an anchor cannot be found the script stops
with an error, so a silent half-patch cannot happen. Can run before or after
ladio_patch.py.
"""
import re
import sys
from pathlib import Path

# ---- tuning ---------------------------------------------------------------
RETRY_INTERVAL_MS = 3000   # wait between two attempts to re-open the device
MAX_TRIES = 20             # attempts in a row before giving up (20 x 3 s = about 1 minute)
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


# ----------------------------------------------------------------- 2. password
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


def fix_password(t):
    if "altacast_fixes: URLize() works in place" in t:
        print("altacast_fixes: password fix already present, skipped")
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
        tail, r"URLPassword\[255\]", "URLPassword[1024]", "libaltacast.cpp URLPassword size"
    )
    tail = sub_once(
        tail, r"contentString\[2056\]", "contentString[4096]", "libaltacast.cpp contentString size"
    )
    print("altacast_fixes: password fix added")
    return head + tail


# ---------------------------------------------------------------- 3. recording
REC_BLOCK = """#ifdef ALTACASTSTANDALONE
	/*
	 * altacast_fixes: recording device recovery.
	 * When the recording device is lost the BASS record channel stops. Re-open it, but
	 * only every ALTACAST_REC_RETRY_MS and at most ALTACAST_REC_MAX_TRIES times in a
	 * row, so a device that stays away does not cause a flood of init attempts and
	 * status messages. The counter is re-armed as soon as recording works again.
	 */
#define ALTACAST_REC_RETRY_MS	%(retry)d
#define ALTACAST_REC_MAX_TRIES	%(tries)d
	if(nIDEvent == 73) {
		static int		recTries = 0;
		static DWORD	recLastTry = 0;

		if(BASS_ChannelIsActive(inRecHandle) != BASS_ACTIVE_STOPPED) {
			recTries = 0;
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

""" % {"retry": RETRY_INTERVAL_MS, "tries": MAX_TRIES}


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
    print("altacast_fixes: recording recovery added (retry every %d ms, max %d tries)"
          % (RETRY_INTERVAL_MS, MAX_TRIES))
    return t


if not REC_BLOCK.isascii() or not PW_BLOCK.isascii():
    fail("internal: inserted C++ must be ASCII")

patch_file("libaltacast/libaltacast.cpp", lambda t: fix_password(fix_fflush(t)))
patch_file("MainWindow.cpp", fix_recording)
print("altacast_fixes: done")
