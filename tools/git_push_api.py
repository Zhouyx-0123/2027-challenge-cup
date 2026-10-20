# -*- coding: utf-8 -*-
"""Git Data API 推送器：github.com 主站不通时，走 api.github.com 提交。

为什么需要这个：本机到 github.com:443 的链路时通时不通（DNS 解析到 IPv6 地址后连不上），
但 api.github.com 一直可用。git push 走的是 github.com，推不动；
这个脚本用 GitHub REST 的 Git Data API 完成同样的事——把本地某个 commit 的内容
原样推到远端分支。

它**不重新打包**：
  - 直接取本地 commit 的 tree SHA；
  - 若远端已有该 commit 及其祖先（tree 可达），则无需动作；
  - 否则用「ref → tree」的等价路径重建：用远端 base tree + 本地 commit 的 diff
    生成新 tree，再创建 commit，最后快进更新 ref。

**安全**：token 只从 git credential manager 取，不落盘、不打印。
用法：
    python -m tools.git_push_api --branch integration/da-zg-ui
    python -m tools.git_push_api --branch integration/da-zg-ui --dry-run
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"
REPO = "Zhouyx-0123/2027-challenge-cup"


def _git(*args: str, input_text: Optional[str] = None) -> str:
    proc = subprocess.run(["git", *args], cwd=ROOT, input=input_text,
                          capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError("git %s 失败：%s" % (" ".join(args), proc.stderr.strip()[:300]))
    return proc.stdout


def get_credentials() -> Tuple[str, str]:
    """从 git credential manager 取用户名与 token（不落盘、不打印）。"""
    out = _git("credential", "fill", input_text="protocol=https\nhost=github.com\n\n")
    username, password = "", ""
    for line in out.splitlines():
        if line.startswith("username="):
            username = line.split("=", 1)[1]
        elif line.startswith("password="):
            password = line.split("=", 1)[1]
    if not password:
        raise RuntimeError("取不到 GitHub 凭据")
    return username, password


class GitHubAPI:
    def __init__(self, token: str):
        self.token = token

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        url = API + path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.token)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "workbuddy-git-push")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError("HTTP %s %s %s → %s"
                               % (exc.code, method, path, detail[:600])) from exc

    def get_ref(self, branch: str) -> Dict[str, Any]:
        return self._request("GET", "/repos/%s/git/ref/heads/%s" % (REPO, branch))

    def get_commit(self, sha: str) -> Dict[str, Any]:
        return self._request("GET", "/repos/%s/git/commits/%s" % (REPO, sha))

    def get_tree(self, sha: str, recursive: str = "1") -> Dict[str, Any]:
        return self._request("GET", "/repos/%s/git/trees/%s?recursive=%s" % (REPO, sha, recursive))

    def create_blob(self, content: bytes) -> str:
        payload = {"content": base64.b64encode(content).decode("ascii"),
                   "encoding": "base64"}
        return self._request("POST", "/repos/%s/git/blobs" % REPO, payload)["sha"]

    def create_tree(self, base_tree: str, entries: List[Dict[str, Any]]) -> str:
        payload = {"base_tree": base_tree, "tree": entries}
        return self._request("POST", "/repos/%s/git/trees" % REPO, payload)["sha"]

    def create_commit(self, message: str, tree: str, parents: List[str],
                      author: Optional[Dict[str, str]] = None) -> str:
        payload: Dict[str, Any] = {
            "message": message, "tree": tree, "parents": parents,
        }
        if author:
            payload["author"] = author
        return self._request("POST", "/repos/%s/git/commits" % REPO, payload)["sha"]

    def update_ref(self, branch: str, sha: str, force: bool = False) -> Dict[str, Any]:
        payload = {"sha": sha, "force": force}
        return self._request("PATCH", "/repos/%s/git/refs/heads/%s" % (REPO, branch), payload)


def local_commit_info(ref: str) -> Tuple[str, str, str, str]:
    """返回 (commit_sha, tree_sha, parent_sha, message)。"""
    sha = _git("rev-parse", ref).strip()
    tree = _git("rev-parse", ref + "^{tree}").strip()
    parents = _git("rev-list", "--parents", "-n", "1", ref).strip().split()[1:]
    message = _git("log", "-1", "--pretty=%B", ref)
    return sha, tree, (parents[0] if parents else ""), message.strip()


def file_list(tree_sha: str) -> Dict[str, str]:
    """本地 tree → {path: blob_sha}。

    **必须加 -z**：git 默认把非 ASCII 路径转义成 "\\346\\241\\243..." 的八进制形式
    （core.quotepath），与远端的原始 UTF-8 路径对不上，会导致所有中文文件
    被误判成"远端有、本地无"→ 全被删掉。用 -z 拿 NUL 分隔的原始字节路径。
    """
    proc = subprocess.run(["git", "ls-tree", "-r", "-z", tree_sha], cwd=ROOT,
                          capture_output=True, timeout=120)
    mapping: Dict[str, str] = {}
    for record in proc.stdout.split(b"\x00"):
        if not record:
            continue
        meta, _, raw_path = record.partition(b"\t")
        parts = meta.split()
        if len(parts) >= 3 and parts[1] == b"blob":
            path = raw_path.decode("utf-8")
            mapping[path] = parts[2].decode("ascii")
    return mapping


def _file_mode(path: str) -> str:
    """取文件在 git tree 里的 mode：普通文件 100644 / 可执行 100755 / symlink 120000。"""
    proc = subprocess.run(["git", "ls-files", "-s", "-z", "--", path], cwd=ROOT,
                          capture_output=True, timeout=60)
    for record in proc.stdout.split(b"\x00"):
        if not record:
            continue
        meta, _, _ = record.partition(b"\t")
        parts = meta.split()
        if parts and parts[0].isdigit():
            return parts[0].decode("ascii")
    return "100644"


def _align_local_ref(branch: str, remote_sha: str) -> None:
    """把本地 origin/<branch> 指到远端实际的新 commit（尽力而为）。

    两个已知限制，如实说明，不粉饰：
      1. 走 API 建的 commit 与本地 commit 是**不同的 SHA 对象**（内容一致，但
         author/committer 时间戳不同），所以本地分支与远端分支永远不可能 0/0；
      2. 远端 commit 对象不在本地 git 对象库里，`git update-ref` 指向它会被 git 拒绝
         （exit 128），本地 ref 对齐大概率失败——这是预期内的，不影响远端内容。

    结论：**远端文件内容已与本地完全一致**，这才是"推送成功"的实质。
    下次 github.com 通了，用 `git push` 可把本地这条 commit 正式推上去并让 SHA 对齐。
    """
    ref = "refs/remotes/origin/%s" % branch
    proc = subprocess.run(["git", "update-ref", ref, remote_sha], cwd=ROOT,
                          capture_output=True, timeout=60)
    if proc.returncode == 0:
        print("  · 本地 origin/%s 已指向远端新 commit" % branch)
    else:
        print("  · 本地 ref 未对齐（预期内：远端 commit 对象不在本地库，git 拒绝指向它）")
    print("  · 注：走 API 建的 commit 与本地 commit SHA 不同（时间戳不同）但内容一致，")
    print("    故本地分支会显示领先 1 个等价提交。**远端文件内容已完全同步**；")
    print("    下次 github.com 通了跑一次 `git push` 即可让 SHA 也对齐。")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="用 Git Data API 推送（github.com 不通时的替代路径）")
    parser.add_argument("--branch", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    print("=" * 78)
    print("Git Data API 推送器 → %s @ %s" % (REPO, args.branch))
    print("=" * 78)

    username, token = get_credentials()
    api = GitHubAPI(token)

    try:
        ref = api.get_ref(args.branch)
    except urllib.error.HTTPError as exc:
        print("  ✗ 取远端分支失败：HTTP %s" % exc.code)
        return 1
    remote_sha = ref["object"]["sha"]
    print("  远端 HEAD ：%s" % remote_sha[:10])

    local_sha, local_tree, local_parent, message = local_commit_info("HEAD")
    print("  本地 HEAD ：%s" % local_sha[:10])
    print("  提交信息 ：%s" % (message.splitlines()[0] if message else "(空)"))

    if local_sha == remote_sha:
        print("  ✓ 已是最新，无需推送")
        return 0

    # --- 两边的文件清单（快进/重建两条路都要用）---
    remote_commit = api.get_commit(remote_sha)
    remote_tree = remote_commit["tree"]["sha"]
    remote_files = {e["path"]: e["sha"] for e in api.get_tree(remote_tree).get("tree", [])
                    if e.get("type") == "blob"}
    local_files = file_list(local_tree)
    print("  远端文件 %d 个 / 本地文件 %d 个" % (len(remote_files), len(local_files)))

    if local_parent and local_parent == remote_sha:
        print("  关系：本地是远端的直接子提交（快进）")
        if args.dry_run:
            print("  [dry-run] 快进推送将创建 commit（tree 与本地内容一致）")
            return 0
        # ---- 上传 blob → 在远端建 tree → 建 commit → 快进 ref ----
        # 注意：不能直接引用本地 tree SHA——GitHub 不认本地算出的 tree
        # （子模块/symlink 的 mode 处理不同），必须在远端用 create_tree 重建。
        print("  上传 blob……")
        entries: List[Dict[str, Any]] = []
        uploaded = 0
        for path, blob_sha in local_files.items():
            if remote_files.get(path) == blob_sha:
                continue
            raw = subprocess.run(["git", "cat-file", "blob", blob_sha], cwd=ROOT,
                                 capture_output=True, timeout=120).stdout
            new_blob = api.create_blob(raw)
            mode = _file_mode(path)
            entries.append({"path": path, "mode": mode, "type": "blob", "sha": new_blob})
            uploaded += 1
        print("  上传 %d 个 blob" % uploaded)

        for path in remote_files:
            if path not in local_files:
                entries.append({"path": path, "mode": "100644", "type": "blob",
                                "sha": "0000000000000000000000000000000000000000"})
                print("  删除 %s" % path)

        if not entries:
            print("  文件无差异——远端内容已是最新")
            return 0

        print("  在远端构建 tree……")
        new_tree = api.create_tree(remote_tree, entries)
        new_commit = api.create_commit(message or "sync", new_tree, [remote_sha],
                                       {"name": "WorkBuddy", "email": "workbuddy@local"})
        print("  新建 commit：%s" % new_commit[:10])
        api.update_ref(args.branch, new_commit, force=False)
        print("  ✓ 远端分支已快进 → %s" % new_commit[:10])
        _align_local_ref(args.branch, new_commit)
        print("=" * 78)
        return 0
    elif local_parent:
        print("  关系：本地父提交 %s ≠ 远端 %s" % (local_parent[:10], remote_sha[:10]))
        print("        → 走重建路径（以远端为基底叠加本地差异）")
    else:
        print("  关系：本地无父提交（首次提交）")

    if args.dry_run:
        print("  [dry-run] 到此为止")
        return 0

    # --- 重建：base = 远端 tree，把本地改动的文件叠加上去 ---
    changed = {p: s for p, s in local_files.items() if remote_files.get(p) != s}
    removed = [p for p in remote_files if p not in local_files]
    print("  需更新 %d 个 / 需删除 %d 个" % (len(changed), len(removed)))

    if not changed and not removed:
        print("  ✓ 文件内容与远端**完全一致**——远端已是最新，无需再推")
        _align_local_ref(args.branch, remote_sha)
        print("=" * 78)
        return 0

    entries: List[Dict[str, Any]] = []
    for path, blob_sha in changed.items():
        # 用 git cat-file 取出字节内容（保持二进制安全）
        raw = subprocess.run(["git", "cat-file", "blob", blob_sha], cwd=ROOT,
                             capture_output=True, timeout=120).stdout
        new_blob = api.create_blob(raw)
        entries.append({"path": path, "mode": _file_mode(path), "type": "blob",
                        "sha": new_blob})
    for path in removed:
        entries.append({"path": path, "mode": "100644", "type": "blob",
                        "sha": "0000000000000000000000000000000000000000"})

    print("  上传 %d 个 blob" % len(changed))
    new_tree = api.create_tree(remote_tree, entries)
    author = {"name": "WorkBuddy", "email": "workbuddy@local"}
    new_commit = api.create_commit(message or "sync", new_tree, [remote_sha], author)
    print("  新建 commit：%s" % new_commit[:10])

    api.update_ref(args.branch, new_commit, force=False)
    print("  ✓ 远端分支已更新 → %s" % new_commit[:10])
    _align_local_ref(args.branch, new_commit)
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
