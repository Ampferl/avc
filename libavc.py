import argparse
import collections
import difflib
import enum
import hashlib
import operator
import os
import stat
import struct
import sys
import time
import urllib.request
import zlib

def main():
    parser = argparse.ArgumentParser()
    sub_parsers = parser.add_subparsers(dest='command', metavar='command')
    sub_parsers.required = True

    sub_parser = sub_parsers.add_parser('add', help='add file(s) to index')
    sub_parser.add_argument('paths', nargs='+', metavar='path', help='path(s) of files to add')

    sub_parser = sub_parsers.add_parser('cat-file', help='display contents of object')
    valid_modes = ['commit', 'tree', 'blob', 'size', 'type', 'pretty']
    sub_parser.add_argument('mode', choices=valid_modes, help='object type (commit, tree, blob) or display mode (size, type, pretty)')
    sub_parser.add_argument('hash_prefix', help='SHA-1 hash (or hash prefix) of object to display')

    sub_parser = sub_parsers.add_parser('commit', help='commit current state of index to master branch')
    sub_parser.add_argument('-a', '--author', help='commit author in format "A U Thor <author@example.com>" (uses GIT_AUTHOR_NAME and GIT_AUTHOR_EMAIL environment variables by default)')
    sub_parser.add_argument('-m', '--message', required=True, help='text of commit message')

    sub_parser = sub_parsers.add_parser('diff', help='show diff of files changed (between index and working copy)')

    sub_parser = sub_parsers.add_parser('hash-object', help='hash contents of given path (and optionally write to object store)')
    sub_parser.add_argument('path', help='path of file to hash')
    sub_parser.add_argument('-t', choices=['commit', 'tree', 'blob'], default='blob', dest='type', help='type of object (default %(default)r)')
    sub_parser.add_argument('-w', action='store_true', dest='write', help='write object to object store (as well as printing hash)')

    sub_parser = sub_parsers.add_parser('init', help='initialize a new repo')
    sub_parser.add_argument('repo', help='directory name for new repo')

    sub_parser = sub_parsers.add_parser('ls-files', help='list files in index')
    sub_parser.add_argument('-s', '--stage', action='store_true', help='show object details (mode, hash, and stage number) in addition to path')

    sub_parser = sub_parsers.add_parser('push', help='push master branch to given git server URL')
    sub_parser.add_argument('git_url', help='URL of git repo, eg: https://github.com/:user:/:repository:.git')
    sub_parser.add_argument('-p', '--password', help='password to use for authentication (uses GIT_PASSWORD environment variable by default)')
    sub_parser.add_argument('-u', '--username', help='username to use for authentication (uses GIT_USERNAME environment variable by default)')

    sub_parser = sub_parsers.add_parser('status', help='show status of working copy')

    args = parser.parse_args()
    if args.command == 'add':
        add(args.paths)
    elif args.command == 'cat-file':
        try:
            cat_file(args.mode, args.hash_prefix)
        except ValueError as error:
            print(error, file=sys.stderr)
            sys.exit(1)
    elif args.command == 'commit':
        commit(args.message, author=args.author)
    elif args.command == 'diff':
        diff()
    elif args.command == 'hash-object':
        sha1 = hash_object(read_file(args.path), args.type, write=args.write)
        print(sha1)
    elif args.command == 'init':
        init(args.repo)
    elif args.command == 'ls-files':
        ls_files(details=args.stage)
    elif args.command == 'push':
        push(args.git_url, username=args.username, password=args.password)
    elif args.command == 'status':
        status()
    else:
        assert False, 'unexpected command {!r}'.format(args.command)


def diff():
    changed, _, _ = get_status()
    entries_by_path = {e.path: e for e in read_index()}
    for i, path in enumerate(changed):
        sha1 = entries_by_path[path].sha1.hex()
        obj_type, data = read_object(sha1)
        assert obj_type == 'blob'
        index_lines = data.decode().splitlines()
        working_lines = read_file(path).decode().splitlines()
        diff_lines = difflib.unified_diff(
                index_lines, working_lines,
                '{} (index)'.format(path),
                '{} (working copy)'.format(path),
                lineterm='')
        for line in diff_lines:
            print(line)
        if i < len(changed) - 1:
            print('-' * 70)


def get_status():
    paths = set()
    for root, dirs, files in os.walk('.'):
        dirs[:] = [d for d in dirs if d != '.git']
        for file in files:
            path = os.path.join(root, file)
            path = path.replace('\\', '/')
            if path.startswith('./'):
                path = path[2:]
            paths.add(path)
    entries_by_path = {e.path: e for e in read_index()}
    entry_paths = set(entries_by_path)
    changed = {p for p in (paths & entry_paths)
               if hash_object(read_file(p), 'blob', write=False) !=
                  entries_by_path[p].sha1.hex()}
    new = paths - entry_paths
    deleted = entry_paths - paths
    return (sorted(changed), sorted(new), sorted(deleted))




IndexEntry = collections.namedtuple('IndexEntry', [
    'ctime_s', 'ctime_n', 'mtime_s', 'mtime_n', 'dev', 'ino', 'mode', 'uid',
    'gid', 'size', 'sha1', 'flags', 'path',
])


class ObjectType(enum.Enum):
    commit = 1
    tree = 2
    blob = 3


def commit(message, author=None):
    tree = write_tree()
    parent = get_local_master_hash()
    if author is None:
        author = '{} <{}>'.format(
                os.environ['GIT_AUTHOR_NAME'], os.environ['GIT_AUTHOR_EMAIL'])
    timestamp = int(time.mktime(time.localtime()))
    utc_offset = -time.timezone
    author_time = '{} {}{:02}{:02}'.format(
            timestamp,
            '+' if utc_offset > 0 else '-',
            abs(utc_offset) // 3600,
            (abs(utc_offset) // 60) % 60)
    lines = ['tree ' + tree]
    if parent:
        lines.append('parent ' + parent)
    lines.append('author {} {}'.format(author, author_time))
    lines.append('committer {} {}'.format(author, author_time))
    lines.append('')
    lines.append(message)
    lines.append('')
    data = '\n'.join(lines).encode()
    sha1 = hash_object(data, 'commit')
    master_path = os.path.join('.git', 'refs', 'heads', 'master')
    write_file(master_path, (sha1 + '\n').encode())
    print('committed to master: {:7}'.format(sha1))
    return sha1


def write_tree():
    tree_entries = []
    for entry in read_index():
        assert '/' not in entry.path, \
                'currently only supports a single, top-level directory'
        mode_path = '{:o} {}'.format(entry.mode, entry.path).encode()
        tree_entry = mode_path + b'\x00' + entry.sha1
        tree_entries.append(tree_entry)
    return hash_object(b''.join(tree_entries), 'tree')


def get_local_master_hash():
    master_path = os.path.join('.git', 'refs', 'heads', 'master')
    try:
        return read_file(master_path).decode().strip()
    except FileNotFoundError:
        return None


def cat_file(mode, sha1_prefix):
    obj_type, data = read_object(sha1_prefix)
    if mode in ['commit', 'tree', 'blob']:
        if obj_type != mode:
            raise ValueError('expected object type {}, got {}'.format(
                    mode, obj_type))
        sys.stdout.buffer.write(data)
    elif mode == 'size':
        print(len(data))
    elif mode == 'type':
        print(obj_type)
    elif mode == 'pretty':
        if obj_type in ['commit', 'blob']:
            sys.stdout.buffer.write(data)
        elif obj_type == 'tree':
            for mode, path, sha1 in read_tree(data=data):
                type_str = 'tree' if stat.S_ISDIR(mode) else 'blob'
                print('{:06o} {} {}\t{}'.format(mode, type_str, sha1, path))
        else:
            assert False, 'unhandled object type {!r}'.format(obj_type)
    else:
        raise ValueError('unexpected mode {!r}'.format(mode))


def read_tree(sha1=None, data=None):
    if sha1 is not None:
        obj_type, data = read_object(sha1)
        assert obj_type == 'tree'
    elif data is None:
        raise TypeError('must specify "sha1" or "data"')
    i = 0
    entries = []
    for _ in range(1000):
        end = data.find(b'\x00', i)
        if end == -1:
            break
        mode_str, path = data[i:end].decode().split()
        mode = int(mode_str, 8)
        digest = data[end + 1:end + 21]
        entries.append((mode, path, digest.hex()))
        i = end + 1 + 20
    return entries


def read_object(sha1_prefix):
    path = find_object(sha1_prefix)
    full_data = zlib.decompress(read_file(path))
    nul_index = full_data.index(b'\x00')
    header = full_data[:nul_index]
    obj_type, size_str = header.decode().split()
    size = int(size_str)
    data = full_data[nul_index + 1:]
    assert size == len(data), 'expected size {}, got {} bytes'.format(
            size, len(data))
    return (obj_type, data)


def find_object(sha1_prefix):
    if len(sha1_prefix) < 2:
        raise ValueError('hash prefix must be 2 or more characters')
    obj_dir = os.path.join('.git', 'objects', sha1_prefix[:2])
    rest = sha1_prefix[2:]
    objects = [name for name in os.listdir(obj_dir) if name.startswith(rest)]
    if not objects:
        raise ValueError('object {!r} not found'.format(sha1_prefix))
    if len(objects) >= 2:
        raise ValueError('multiple objects ({}) with prefix {!r}'.format(
                len(objects), sha1_prefix))
    return os.path.join(obj_dir, objects[0])


def add(paths):
    paths = [p.replace('\\', '/') for p in paths]
    all_entries = read_index()
    entries = [e for e in all_entries if e.path not in paths]
    for path in paths:
        sha1 = hash_object(read_file(path), 'blob')
        st = os.stat(path)
        flags = len(path.encode())
        assert flags < (1 << 12)
        entry = IndexEntry(
                int(st.st_ctime), 0, int(st.st_mtime), 0, st.st_dev,
                st.st_ino, st.st_mode, st.st_uid, st.st_gid, st.st_size,
                bytes.fromhex(sha1), flags, path)
        entries.append(entry)
    entries.sort(key=operator.attrgetter('path'))
    write_index(entries)


def read_index():
    try:
        data = read_file(os.path.join('.git', 'index'))
    except FileNotFoundError:
        return []
    digest = hashlib.sha1(data[:-20]).digest()
    assert digest == data[-20:], 'invalid index checksum'
    signature, version, num_entries = struct.unpack('!4sLL', data[:12])
    assert signature == b'DIRC', \
            'invalid index signature {}'.format(signature)
    assert version == 2, 'unknown index version {}'.format(version)
    entry_data = data[12:-20]
    entries = []
    i = 0
    while i + 62 < len(entry_data):
        fields_end = i + 62
        fields = struct.unpack('!LLLLLLLLLL20sH', entry_data[i:fields_end])
        path_end = entry_data.index(b'\x00', fields_end)
        path = entry_data[fields_end:path_end]
        entry = IndexEntry(*(fields + (path.decode(),)))
        entries.append(entry)
        entry_len = ((62 + len(path) + 8) // 8) * 8
        i += entry_len
    assert len(entries) == num_entries
    return entries


def hash_object(data, obj_type, write=True):
    header = '{} {}'.format(obj_type, len(data)).encode()
    full_data = header + b'\x00' + data
    sha1 = hashlib.sha1(full_data).hexdigest()
    if write:
        path = os.path.join('.git', 'objects', sha1[:2], sha1[2:])
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            write_file(path, zlib.compress(full_data))
    return sha1


def read_file(path):
    with open(path, 'rb') as f:
        return f.read()


def write_file(path, data):
    with open(path, 'wb') as f:
        f.write(data)


def write_index(entries):
    packed_entries = []
    for entry in entries:
        entry_head = struct.pack('!LLLLLLLLLL20sH',
                entry.ctime_s, entry.ctime_n, entry.mtime_s, entry.mtime_n,
                entry.dev, entry.ino, entry.mode, entry.uid, entry.gid,
                entry.size, entry.sha1, entry.flags)
        path = entry.path.encode()
        length = ((62 + len(path) + 8) // 8) * 8
        packed_entry = entry_head + path + b'\x00' * (length - 62 - len(path))
        packed_entries.append(packed_entry)
    header = struct.pack('!4sLL', b'DIRC', 2, len(entries))
    all_data = header + b''.join(packed_entries)
    digest = hashlib.sha1(all_data).digest()
    write_file(os.path.join('.git', 'index'), all_data + digest)


def init(repo):
    os.mkdir(repo)
    os.mkdir(os.path.join(repo, '.git'))
    for name in ['objects', 'refs', 'refs/heads']:
        os.mkdir(os.path.join(repo, '.git', name))
    write_file(os.path.join(repo, '.git', 'HEAD'), b'ref: refs/heads/master')
    print('initialized empty repository: {}'.format(repo))