import argparse
import collections
import configparser
import hashlib
import os
import re
import sys
import zlib

argparser = argparse.ArgumentParser(description="Another Version Control")

argsubparser = argparser.add_subparsers(title="Commands", dest="command")
argsubparser.required = True


def main(argv=sys.argv[1:]):
    args = argparser.parse_args(argv)

    # if   args.command == "add"         : cmd_add(args)
    # elif args.command == "cat-file"    : cmd_cat_file(args)
    # elif args.command == "checkout"    : cmd_checkout(args)
    # elif args.command == "commit"      : cmd_commit(args)
    # elif args.command == "hash-object" : cmd_hash_object(args)
    # elif args.command == "init"        : cmd_init(args)
    # elif args.command == "log"         : cmd_log(args)
    # elif args.command == "ls-tree"     : cmd_ls_tree(args)
    # elif args.command == "merge"       : cmd_merge(args)
    # elif args.command == "rebase"      : cmd_rebase(args)
    # elif args.command == "rev-parse"   : cmd_rev_parse(args)
    # elif args.command == "rm"          : cmd_rm(args)
    # elif args.command == "show-ref"    : cmd_show_ref(args)
    # elif args.command == "tag"         : cmd_tag(args)


class GitRepository(object):
    worktree = None
    gitdir = None
    conf = None

    def __init__(self, path, force=False):
        self.worktree = path
        self.gitdir = os.path.join(path, ".git")

        if not (force or os.path.isdir(self.gitdir)):
            raise Exception("Not a Git repository %s" % path)

        self.conf = configparser.ConfigParser()
        cf = repo_file(self, "config")

        if cf and os.path.exists(cf):
            self.conf.read([cf])
        elif not force:
            raise Exception("Configuration file missing")

        if not force:
            vers = int(self.conf.get("core", "repositoryformatversion"))
            if vers != 0:
                raise Exception("Unsupported repositoryformatversion %s" % vers)


def repo_path(repo, *path):
    return os.path.join(repo.gitdir, *path)


def repo_file(repo, *path, mkdir=False):
    if repo_dir(repo, *path[:-1], mkdir=mkdir):
        return repo_path(repo, *path)


def repo_dir(repo, *path, mkdir=False):
    path = repo_path(repo, *path)

    if os.path.exists(path):
        if os.path.isdir(path):
            return path
        else:
            raise Exception("Not a directory %s" % path)

    if mkdir:
        os.makedirs(path)
        return path
    else:
        return None


def repo_create(path):
    repo = GitRepository(path, True)

    if os.path.exists(repo.worktree):
        if not os.path.isdir(repo.worktree):
            raise Exception("%s is not a directory!" % path)
        if os.listdir(repo.worktree):
            raise Exception("%s is not empty!" % path)
    else:
        os.makedirs(repo.worktree)

    assert(repo_dir(repo, "branches", mkdir=True))
    assert(repo_dir(repo, "objects", mkdir=True))
    assert(repo_dir(repo, "refs", "tags", mkdir=True))
    assert(repo_dir(repo, "refs", "heads", mkdir=True))

    # .git/description
    with open(repo_file(repo, "description"), "w") as f:
        f.write("Unnamed repository; edit this file 'description' to name the repository.\n")

    # .git/HEAD
    with open(repo_file(repo, "HEAD"), "w") as f:
        f.write("ref: refs/heads/master\n")

    # .git/config
    with open(repo_file(repo, "config"), "w") as f:
        config = repo_default_config()
        config.write(f)

    return repo
