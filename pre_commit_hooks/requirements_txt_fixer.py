from __future__ import annotations

import argparse
import re
from typing import IO
from typing import Sequence


PASS = 0
FAIL = 1


class Requirement:
    UNTIL_COMPARISON = re.compile(b'={2,3}|!=|~=|>=?|<=?')
    VERSION_MATCHER = re.compile(br'(?:={2,3}|!=|~=|>=?|<=?|@)\s*(?P<version>[A-Za-z0-9./:]+)$')  # noqa: E501
    UNTIL_SEP = re.compile(rb'[^;\s]+')

    def __init__(self) -> None:
        self.value: bytes | None = None
        self.version: str | None = None
        self.comments: list[bytes] = []

    @property
    def name(self) -> bytes:
        assert self.value is not None, self.value
        name = self.value.lower()
        for egg in (b'#egg=', b'&egg='):
            if egg in self.value:
                return name.partition(egg)[-1]

        m = self.UNTIL_SEP.match(name)
        assert m is not None

        name = m.group()
        m = self.UNTIL_COMPARISON.search(name)
        if not m:
            return name

        return name[:m.start()]

    def has_version(self) -> bool:
        return self.extract_version() is not None

    def is_include(self) -> bool:
        """Ignore version requirement if the line is -r <some_req_file.txt>"""
        if not self.value:
            return False
        return self.value.decode().startswith('-r')

    def extract_version(self) -> str | None:
        if not self.value:
            return None
        matches = self.VERSION_MATCHER.search(self.value)
        if matches:
            self.version = matches.groups()[0].decode()
        else:
            self.version = None
        return self.version

    def __lt__(self, requirement: Requirement) -> bool:
        # \n means top of file comment, so always return True,
        # otherwise just do a string comparison with value.
        assert self.value is not None, self.value
        if self.value == b'\n':
            return True
        elif requirement.value == b'\n':
            return False
        else:
            return self.name < requirement.name

    def is_complete(self, require_version: bool = False) -> bool:
        return (
            self.value is not None and
            not self.value.rstrip(b'\r\n').endswith(b'\\')
        )

    def append_value(self, value: bytes) -> None:
        if self.value is not None:
            self.value += value
        else:
            self.value = value


def fix_requirements(f: IO[bytes], require_version: bool = False) -> int:
    requirements: list[Requirement] = []
    before = list(f)
    after: list[bytes] = []

    before_string = b''.join(before)

    # adds new line in case one is missing
    # AND a change to the requirements file is needed regardless:
    if before and not before[-1].endswith(b'\n'):
        before[-1] += b'\n'

    # If the file is empty (i.e. only whitespace/newlines) exit early
    if before_string.strip() == b'':
        return PASS

    for line in before:
        # If the most recent requirement object has a value, then it's
        # time to start building the next requirement object.

        if not len(requirements) or requirements[-1].is_complete():
            requirements.append(Requirement())

        requirement = requirements[-1]

        # If we see a newline before any requirements, then this is a
        # top of file comment.
        if len(requirements) == 1 and line.strip() == b'':
            if (
                    len(requirement.comments) and
                    requirement.comments[0].startswith(b'#')
            ):
                requirement.value = b'\n'
            else:
                requirement.comments.append(line)
        elif line.lstrip().startswith(b'#') or line.strip() == b'':
            requirement.comments.append(line)
        else:
            requirement.append_value(line)

    # if a file ends in a comment, preserve it at the end
    if requirements[-1].value is None:
        rest = requirements.pop().comments
    else:
        rest = []

    # find and remove pkg-resources==0.0.0
    # which is automatically added by broken pip package under Debian
    requirements = [
        req for req in requirements
        if req.value != b'pkg-resources==0.0.0\n'
    ]

    missing_versions = []
    for requirement in sorted(requirements):
        after.extend(requirement.comments)
        assert requirement.value, requirement.value
        after.append(requirement.value)
        if require_version and \
                not requirement.is_include() and \
                not requirement.has_version():
            missing_versions.append(requirement.value.decode().strip())
    after.extend(rest)

    after_string = b''.join(after)

    # If the version is required but missing, we return FAIL,
    # but still write the fixes to the file, because the pip install
    # will work even if the req file is missing versions.
    # We could block the write if versions are missing, too. This is
    # something we should discuss in the PR review.
    outcome = PASS

    if len(missing_versions) > 0:
        print('Missing versions in:', ', '.join(missing_versions))
        outcome = FAIL

    if before_string != after_string:
        f.seek(0)
        f.write(after_string)
        f.truncate()
        outcome = FAIL

    return outcome


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('filenames', nargs='*', help='Filenames to fix')
    parser.add_argument(
        '-r', '--require_version',
        required=False,
        help='Each requirement must include a version number',
        action='store_true',
        default=False,
    )
    args = parser.parse_args(argv)

    retv = PASS

    for arg in args.filenames:
        with open(arg, 'rb+') as file_obj:
            ret_for_file = fix_requirements(file_obj, args.require_version)

            if ret_for_file:
                print(f'Sorting {arg}')

            retv |= ret_for_file

    return retv


if __name__ == '__main__':
    raise SystemExit(main())
