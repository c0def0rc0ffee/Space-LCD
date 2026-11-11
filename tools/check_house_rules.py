#!/usr/bin/env python3
"""
<summary>
House rules gate, run by build-zip.sh against each staged tree before it is
zipped:

    python3 tools/check_house_rules.py <stage_dir>... [--allow <path>...] [--allow-ip <path>...] [--skip <dir>...]

Refuses the build when a stage carries a dash character or a dash entity in
any text file, an attribution trailer, a file whose name matches a secret
pattern, a placeholder secret in a config style file, a real IP address, a
home directory path, a term from the machine's publish denylist, a long run
of hex that looks like a key, or a real config beside its .example template.
</summary>
<remarks>
Merged on 7th November 2026 from the template checker, the widened
RoadClosures one and the extra checks Bailiwick.gg carried. Copy this file
verbatim into a project; never edit a copy.

Arguments: stage folders come first, any number of them. Each flag takes one
or more values up to the next flag, so "--allow a --allow b" and "--allow a b"
mean the same. An --allow or --allow-ip value with no slash matches that file
name anywhere in the stage; one with a slash matches that relative path.
--skip leaves out a whole reviewed subtree, such as vendored code.
--strict-code is accepted and ignored: every text file is now checked.

A long hex run (48 characters or more) is reported as a possible key, except
in checksum and lock files, where hashes belong.

A text file is any file under 5 MB with no NUL byte in its first 4 KB that
decodes as UTF-8, whatever its suffix, so code in any language is covered.

An address is real unless it is loopback, unspecified, 123.123.123.x or one of
the RFC 5737 documentation ranges; CGNAT and the private ranges are real on
purpose (gold rule 1). A dotted number straight after "version" or "v" is a
version string, not an address, and is skipped; anything else that looks like
an address and is not one (SVG path data, an RFC section number in vendored
code) is exempted per file with --allow-ip after review.

The denylist lives outside the tree at ~/.config/github/publish-denylist
(override with HOUSE_RULES_DENYLIST). The gate refuses to run without it, so a
missing list can never pass as a clean one. Denylist hits report the pattern
number, never the matched text, so the gate's output can be pasted anywhere.

A committed *.example.* template is exempt from the secret name check because
the convention requires one beside every real config. --allow exempts one
reviewed file from every check; --allow-ip exempts it from the address check
only, which is the safer choice whenever it is enough.
</remarks>
"""

import fnmatch
import os
import re
import sys

# The editor vendor's name, needed for the editor state folder below and the
# attribution trailer further down. Gold rules 4 and 19 keep that name out of
# every published file, this one included, so it is built from character codes
# rather than written out. The checks behave exactly as if it were spelled.
_VENDOR = '\x43\x6c\x61\x75\x64\x65'
SKIP_DIRS = {'.git', '.' + _VENDOR.lower(), 'node_modules', 'coverage', '__pycache__'}
MAX_TEXT_BYTES = 5_000_000
SECRET_PATTERNS = ('.env', '.env.*', '*credential*', '*Credential*', '*CREDENTIAL*',
                   '*password*', '*Password*', '*PASSWORD*', '*secret*', '*Secret*',
                   '*SECRET*', '*.pem', '*.key', '*.pfx', 'id_rsa*', '*sftp*', '*SFTP*',
                   '*ftp*', '*FTP*', '*cred*', '*Cred*', '*token*', '*Token*',
                   'connection.txt', '*config.local*')
PLACEHOLDER = re.compile(r'changeme|CHANGEME|change_me|CHANGE_ME|REPLACE_ME|replace_me'
                         r'|password123|secret123'
                         r'|your[-_ ]?(password|secret|token)[-_ ]?here', re.IGNORECASE)
HEX_KEY = re.compile(r'(?<![0-9a-fA-F])[0-9a-fA-F]{48,}(?![0-9a-fA-F])')
HASH_FILES = re.compile(r'(SHA\d*SUMS|\.sha\d+|\.md5|\.lock|-lock\.json|lock\.ya?ml|\.sum)$', re.IGNORECASE)
CONFIG_SUFFIXES = ('.conf', '.service', '.ini', '.yml', '.yaml', '.env')
# Escaped so this file passes its own check.
DASHES = (chr(0x2014), chr(0x2013))
DASH_ENTITIES = tuple('&' + e + ';' for e in ('mdash', 'ndash', '#8212', '#8211', '#x2014', '#x2013'))
# A trailer written as a quoted or backticked literal is a checker's own pattern
# or a doc naming the rule, not an attribution, so it is not counted.
ATTRIBUTION = re.compile(r'(?<![\'"`])(Co-Authored-By|[Gg]enerated with ' + _VENDOR + ')')
IP = re.compile(r'(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w.])')
VERSION_BEFORE = re.compile(r'(?:version\W{0,3}|\bv)$', re.IGNORECASE)
# Kodi's special://home/ is a virtual path inside Kodi, not a machine's home
# folder, so it is not counted.
HOME_PATH = re.compile(r'((?<!special:/)/home/[A-Za-z0-9_.-]+|/Users/[A-Za-z0-9_.-]+|[A-Za-z]:\\\\?Users\\\\?[A-Za-z0-9_.-]+)')
DENYLIST_DEFAULT = os.path.expanduser('~/.config/github/publish-denylist')

failures = []


def fail(message):
    """<summary>Records one gate failure; the run reports them all at the end.</summary>"""
    failures.append(message)


def read_text(path):
    """
    <summary>Returns a file's text, or None when it is binary or too large to be source.</summary>
    <param name="path">Absolute path of the file.</param>
    <remarks>
    Binary is judged by a NUL byte in the first 4 KB or a failed UTF-8
    decode. A text file in another encoding is therefore skipped, which is
    the one way a dash could slip past; keep source in UTF-8.
    </remarks>
    """
    try:
        if os.path.getsize(path) > MAX_TEXT_BYTES:
            return None
        with open(path, 'rb') as handle:
            data = handle.read()
    except OSError:
        return None
    if b'\0' in data[:4096]:
        return None
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return None


def is_example(name):
    """<summary>True for a committed template such as db_secrets.example.php.</summary>"""
    return '.example' in name


def is_config(name):
    """<summary>True for files that hold configuration and therefore must not hold a placeholder secret.</summary>"""
    suffix = os.path.splitext(name)[1]
    return name.startswith('.env') or 'config' in name.lower() or suffix in CONFIG_SUFFIXES


def is_placeholder_address(octets):
    """
    <summary>
    True for an address the rules allow in published text: loopback,
    123.123.123.x, the three RFC 5737 documentation ranges and the unspecified
    address. Everything else, CGNAT and private ranges included, is real.
    </summary>
    <param name="octets">The four octets as integers.</param>
    """
    a, b, c, d = octets
    if a == 127 or (a, b, c, d) == (0, 0, 0, 0):
        return True
    if (a, b, c) == (123, 123, 123):
        return True
    return (a, b, c) in ((192, 0, 2), (198, 51, 100), (203, 0, 113))


def load_denylist():
    """
    <summary>
    Compiles the machine's publish denylist into regular expressions. Comment
    and blank lines are skipped.
    </summary>
    <returns>A list of compiled patterns, matched case insensitively.</returns>
    <remarks>Exits the gate when the file is missing, so a missing list can never pass as clean.</remarks>
    """
    path = os.environ.get('HOUSE_RULES_DENYLIST', DENYLIST_DEFAULT)
    if not os.path.isfile(path):
        print('FAILED: publish denylist not found at {}; refusing to run without it'.format(path))
        sys.exit(1)
    patterns = []
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.rstrip('\n')
            if not line.strip() or line.startswith('#'):
                continue
            patterns.append(re.compile(line, re.IGNORECASE))
    return patterns


def check_line(relative, line_number, line, name, denylist, check_ips):
    """
    <summary>
    Runs every per line check on one line of a text file and records each
    failure with the file and line it was found on.
    </summary>
    <param name="relative">Path of the file inside the stage, for the message.</param>
    <param name="line_number">One based line number, for the message.</param>
    <param name="line">The line itself.</param>
    <param name="name">The file's base name, which decides the config and self checks.</param>
    <param name="denylist">Compiled denylist patterns.</param>
    <param name="check_ips">False when the file was exempted with --allow-ip.</param>
    """
    if any(dash in line for dash in DASHES) or any(entity in line for entity in DASH_ENTITIES):
        fail('{}:{} contains a dash character or entity that the house rules disallow'.format(relative, line_number))
    if ATTRIBUTION.search(line) and name != 'check_house_rules.py':
        fail('{}:{} carries an attribution trailer'.format(relative, line_number))
    if is_config(name) and not is_example(name) and PLACEHOLDER.search(line):
        fail('{}:{} holds a placeholder secret'.format(relative, line_number))
    if check_ips:
        for match in IP.finditer(line):
            octets = tuple(int(part) for part in match.groups())
            if not all(part <= 255 for part in octets) or is_placeholder_address(octets):
                continue
            if VERSION_BEFORE.search(line[max(0, match.start() - 12):match.start()]):
                continue
            fail('{}:{} contains a real IP address'.format(relative, line_number))
    if HOME_PATH.search(line):
        fail('{}:{} contains a home directory path'.format(relative, line_number))
    if not HASH_FILES.search(name) and HEX_KEY.search(line):
        fail('{}:{} holds a long run of hex that looks like a key'.format(relative, line_number))
    for index, pattern in enumerate(denylist, 1):
        if pattern.search(line):
            fail('{}:{} matches publish denylist pattern {}'.format(relative, line_number, index))


def matches(relative, entries):
    """
    <summary>True when a file is named by an --allow or --allow-ip entry.</summary>
    <param name="relative">The file's path inside the stage.</param>
    <param name="entries">The entries given on the command line.</param>
    <remarks>An entry with no slash matches that file name anywhere; one with a slash matches that path only.</remarks>
    """
    base = os.path.basename(relative)
    return any(relative == e or ('/' not in e and base == e) for e in entries)


def check_stage(stage, label, allowed, allowed_ip, skips, denylist):
    """
    <summary>
    Walks one staged tree, applying the file name checks to every file, the
    line checks to every text file, and the example template check to every
    folder.
    </summary>
    <param name="stage">Root of the staged tree.</param>
    <param name="label">Prefix for messages when more than one stage is checked; empty otherwise.</param>
    <param name="allowed">Entries exempted from every check with --allow.</param>
    <param name="allowed_ip">Entries exempted from the address check with --allow-ip.</param>
    <param name="skips">Subtrees left out with --skip, relative to the stage.</param>
    <param name="denylist">Compiled denylist patterns.</param>
    """
    for folder, dirs, files in os.walk(stage):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                   and not any(os.path.relpath(os.path.join(folder, d), stage) == s for s in skips)]
        for name in files:
            path = os.path.join(folder, name)
            relative = label + os.path.relpath(path, stage)
            if matches(os.path.relpath(path, stage), allowed):
                continue
            if not is_example(name) and any(fnmatch.fnmatchcase(name, p) for p in SECRET_PATTERNS):
                fail('{} matches a secret file pattern; it must not ship'.format(relative))
            if is_example(name):
                real = name.replace('.example', '', 1)
                if real in files and not matches(os.path.relpath(os.path.join(folder, real), stage), allowed):
                    fail('{}: the real config beside its .example template has reached the stage'.format(
                        label + os.path.relpath(os.path.join(folder, real), stage)))
            text = read_text(path)
            if text is None:
                continue
            check_ips = not matches(os.path.relpath(path, stage), allowed_ip)
            for line_number, line in enumerate(text.splitlines(), 1):
                check_line(relative, line_number, line, name, denylist, check_ips)


def parse_args(args):
    """
    <summary>Splits the command line into stage folders and the flag values.</summary>
    <param name="args">The arguments after the script name.</param>
    <returns>(stages, allowed, allowed_ip, skips), or None after printing why the line is bad.</returns>
    """
    stages, allowed, allowed_ip, skips = [], set(), set(), []
    targets = {'--allow': allowed, '--allow-ip': allowed_ip, '--skip': skips}
    target = None
    for arg in args:
        if arg in targets:
            target = targets[arg]
        elif arg == '--strict-code':
            target = None
        elif arg.startswith('--'):
            print('unknown argument: {}'.format(arg))
            return None
        elif target is None:
            stages.append(arg)
        elif target is skips:
            skips.append(arg.strip('/'))
        else:
            target.add(arg)
    if not stages:
        print('no stage folder given')
        return None
    return stages, allowed, allowed_ip, skips


def main(argv):
    """<summary>Command line entry point; exit code 0 on clean stages, 1 on failures, 2 on bad arguments.</summary>"""
    if len(argv) < 2:
        print(__doc__)
        return 2
    parsed = parse_args(argv[1:])
    if parsed is None:
        return 2
    stages, allowed, allowed_ip, skips = parsed
    for stage in stages:
        if not os.path.isdir(stage):
            print('FAILED: {} is not a directory'.format(stage))
            return 1
    denylist = load_denylist()
    for index, stage in enumerate(stages, 1):
        label = 'stage {}: '.format(index) if len(stages) > 1 else ''
        check_stage(stage, label, allowed, allowed_ip, skips, denylist)
    where = ', '.join(stages)
    if failures:
        print('FAILED: {} house rule problem(s) in {}'.format(len(failures), where))
        for message in failures:
            print('  * ' + message)
        return 1
    print('OK: house rules pass for {}'.format(where))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
