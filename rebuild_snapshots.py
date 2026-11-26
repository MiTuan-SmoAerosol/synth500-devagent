#!/usr/bin/env python3
"""Rebuild the repository snapshots (snapshots/<instance_id>.tgz) from the git bundles in ./bundles.

Examples
  python3 rebuild_snapshots.py                         # all tasks, 4 in parallel
  python3 rebuild_snapshots.py --repo rich --jobs 8    # only the rich tasks
  python3 rebuild_snapshots.py --only fastapi_100 rich_1849

Needs git >= 2.32 and Python >= 3.8 on Linux or macOS; no network access. Each snapshot is built the same way
as the original (git fast-export of the history up to base_commit | git fast-import into a fresh repository,
branches main and export_ref, repack, working tree checked out, packed as ./ relative .tgz), then checked against
snapshot_manifest.jsonl: the tree hash and commit count must match; the HEAD commit id is expected to match too.
Existing .tgz files are skipped unless --force is given. A report is written to rebuild_report.jsonl.
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK = threading.Lock()
# Isolate from the user's own git configuration (aliases, templates, autocrlf, hooks ...) and fix the identity
# used for reflog entries, so every machine produces the same repository content.
GIT_ENV = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
               LC_ALL="C", GIT_AUTHOR_NAME="Agent", GIT_AUTHOR_EMAIL="agent@eval",
               GIT_COMMITTER_NAME="Agent", GIT_COMMITTER_EMAIL="agent@eval")
GIT_ENV.pop("GIT_DIR", None)
GIT_ENV.pop("GIT_WORK_TREE", None)


def git(*args, cwd=None, capture=True):
    p = subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                       stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args[:3]), p.stderr.decode(errors="replace")[-400:]))
    return p.stdout.decode().strip() if capture else ""


def check_git():
    out = subprocess.run(["git", "--version"], stdout=subprocess.PIPE, check=True).stdout.decode()
    m = re.search(r"(\d+)\.(\d+)", out)
    if not m or (int(m.group(1)), int(m.group(2))) < (2, 32):
        sys.exit("git >= 2.32 is required (found: %s)" % out.strip())
    usage = subprocess.run(["git", "fast-export", "-h"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=GIT_ENV)
    flags = ["--signed-tags=strip", "--reencode=yes"]
    if b"signed-commits" in usage.stdout:  # git >= 2.50: keep the historical behaviour (signatures dropped)
        flags.append("--signed-commits=strip")
    return out.strip(), flags


def short_name(iid):
    return iid.rsplit("_", 1)[0]


def prepare_cache(short, bundle, cache_root, tasks):
    dst = cache_root / ("%s.git" % short)
    if not (dst / "READY").exists():
        if not bundle.exists():
            sys.exit("missing bundle %s (download it into %s)" % (bundle, bundle.parent))
        shutil.rmtree(dst, ignore_errors=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        git("init", "-q", "--bare", str(dst))
        git("-C", str(dst), "fetch", "-q", "--no-tags", str(bundle), "+refs/tasks/*:refs/tasks/*")
        (dst / "READY").write_text("ok\n")
    refs = dict(l.split(" ", 1)[::-1] for l in
                git("-C", str(dst), "for-each-ref", "--format=%(objectname) %(refname)", "refs/tasks/").splitlines())
    for t in tasks:
        got = refs.get("refs/tasks/%s" % t["instance_id"])
        if got != t["base_commit"]:
            sys.exit("bundle %s: ref for %s is %s, expected base_commit %s" % (bundle.name, t["instance_id"], got,
                                                                              t["base_commit"]))
    return dst


def tar_filter(ti):
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    return ti


def build(t, cache, out_dir, work_root, fe_flags, discard):
    iid, t0 = t["instance_id"], time.time()
    tmp = Path(tempfile.mkdtemp(prefix=iid + ".", dir=str(work_root)))
    snap = tmp / "snap"
    try:
        git("init", "-q", "-b", "main", str(snap))
        fe = subprocess.Popen(["git", "-C", str(cache), "fast-export", *fe_flags,
                               "--refspec", "refs/tasks/%s:refs/heads/export_ref" % iid, "refs/tasks/%s" % iid],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=GIT_ENV)
        fi = subprocess.run(["git", "-C", str(snap), "fast-import", "--quiet", "--date-format=raw-permissive"],
                            stdin=fe.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=GIT_ENV)
        fe.stdout.close()
        fe_err = fe.stderr.read()
        if fe.wait() != 0 or fi.returncode != 0:
            raise RuntimeError("fast-export/import failed: %s %s" % (fe_err.decode(errors="replace")[-300:],
                                                                     fi.stderr.decode(errors="replace")[-300:]))
        s = str(snap)
        git("-C", s, "branch", "-f", "main", "export_ref")
        git("-C", s, "symbolic-ref", "HEAD", "refs/heads/main")
        git("-C", s, "config", "user.email", "agent@eval")
        git("-C", s, "config", "user.name", "Agent")
        git("-C", s, "-c", "core.autocrlf=false", "reset", "-q", "--hard", "main")
        git("-C", s, "repack", "-a", "-d", "-q")
        git("-C", s, "prune-packed")
        tree = git("-C", s, "rev-parse", "main^{tree}")
        commits = int(git("-C", s, "rev-list", "--count", "main"))
        head = git("-C", s, "rev-parse", "main")
        dirty = git("-C", s, "-c", "core.autocrlf=false", "status", "--porcelain")
        rec = {"instance_id": iid, "tree_ok": tree == t["snapshot_tree"], "commits_ok": commits == t["snapshot_commits"],
               "head_identical": head == t["snapshot_head"], "clean": not dirty}
        if not (rec["tree_ok"] and rec["commits_ok"] and rec["clean"]):
            rec.update(status="mismatch", tree=tree, commits=commits, dirty=dirty[:300])
            return rec
        dst = out_dir / ("%s.tgz" % iid)
        part = out_dir / ("%s.tgz.part" % iid)
        with tarfile.open(str(part), "w:gz", compresslevel=6) as tf:
            tf.add(s, arcname=".", filter=tar_filter)
        rec["bytes"] = part.stat().st_size
        if discard:
            part.unlink()
        else:
            part.replace(dst)
        rec["status"] = "ok"
        return rec
    except Exception as e:  # noqa: BLE001
        return {"instance_id": iid, "status": "error", "error": str(e)[-600:]}
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)
        t["_seconds"] = round(time.time() - t0, 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundles", default=str(HERE / "bundles"), help="directory with <repo>.bundle files")
    ap.add_argument("--out", default=str(HERE / "snapshots"), help="output directory (default: ./snapshots)")
    ap.add_argument("--repo", action="append", help="only tasks of this repository (fastapi, rich, httpx, requests)")
    ap.add_argument("--only", nargs="+", help="only these instance_ids")
    ap.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    ap.add_argument("--force", action="store_true", help="rebuild even if the .tgz already exists")
    ap.add_argument("--discard", action="store_true", help="build and check, but do not keep the .tgz (testing)")
    a = ap.parse_args()

    git_version, fe_flags = check_git()
    manifest = {}
    for line in open(HERE / "snapshot_manifest.jsonl"):
        r = json.loads(line)
        manifest[r["instance_id"]] = r
    tasks = [manifest[json.loads(l)["instance_id"]] for l in open(HERE / "task-index.jsonl")]
    if a.repo:
        tasks = [t for t in tasks if short_name(t["instance_id"]) in a.repo]
    if a.only:
        unknown = set(a.only) - set(manifest)
        if unknown:
            sys.exit("unknown instance_id: %s" % " ".join(sorted(unknown)))
        tasks = [t for t in tasks if t["instance_id"] in a.only]
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [t for t in tasks if a.force or a.discard or not (out_dir / ("%s.tgz" % t["instance_id"])).exists()]
    print("%s; %d tasks selected, %d to build, %d jobs" % (git_version, len(tasks), len(todo), a.jobs), flush=True)
    if not todo:
        return 0

    cache_root, work_root = out_dir / ".cache", out_dir / ".work"
    work_root.mkdir(parents=True, exist_ok=True)
    caches = {}
    for short in sorted({short_name(t["instance_id"]) for t in todo}):
        sub = [t for t in todo if short_name(t["instance_id"]) == short]
        caches[short] = prepare_cache(short, Path(a.bundles) / ("%s.bundle" % short), cache_root, sub)

    report_path = HERE / "rebuild_report.jsonl"
    n_ok = n_head = n_bad = 0
    with open(report_path, "a") as rep, cf.ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(build, t, caches[short_name(t["instance_id"])], out_dir, work_root, fe_flags, a.discard): t
                for t in todo}
        for i, f in enumerate(cf.as_completed(futs), 1):
            t, rec = futs[f], f.result()
            rec["seconds"] = t.get("_seconds")
            rep.write(json.dumps(rec) + "\n")
            rep.flush()
            if rec["status"] == "ok":
                n_ok += 1
                n_head += rec["head_identical"]
            else:
                n_bad += 1
            note = "" if rec["status"] != "ok" or rec["head_identical"] else " (HEAD commit id differs)"
            print("[%d/%d] %s %s%s %ss" % (i, len(todo), rec["instance_id"], rec["status"], note, rec["seconds"]),
                  flush=True)
    shutil.rmtree(str(work_root), ignore_errors=True)
    print("done: %d ok (%d with identical HEAD commit id), %d failed; report: %s"
          % (n_ok, n_head, n_bad, report_path), flush=True)
    if n_ok > n_head:
        print("note: tree and commit count match for all ok tasks; a differing HEAD id usually means a different git"
              " version rewrote commit metadata. The code and history content are still the same.", flush=True)
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
