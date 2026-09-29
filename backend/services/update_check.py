"""Read remote update status without changing the working tree."""

import os
import subprocess


def inspect_remote(source_dir: str, branch: str) -> dict:
    def git(*args: str, timeout: int = 10) -> str:
        result = subprocess.run(
            ["git", *args], cwd=source_dir, capture_output=True, text=True,
            timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if result.returncode:
            raise RuntimeError(f"git {args[0]} 失败: {result.stderr.strip()[:200]}")
        return result.stdout.strip()

    try:
        remote_ref = f"refs/remotes/origin/{branch}"
        # An explicit destination also works with single-branch clones and
        # refreshes the tracking ref after a force push.
        git("fetch", "origin", f"+refs/heads/{branch}:{remote_ref}", timeout=30)
        local_sha = git("rev-parse", "HEAD")
        remote_sha = git("rev-parse", remote_ref)
        commits_behind = int(git("rev-list", "--count", f"{local_sha}..{remote_sha}"))
        return {
            "update_available": commits_behind > 0,
            "commits_behind": commits_behind,
            "local_hash": local_sha[:8],
            "remote_hash": remote_sha[:8],
            "current_sha": local_sha[:8],
            "latest_sha": remote_sha[:8],
        }
    except subprocess.TimeoutExpired:
        return {"update_available": False, "error": "Git 操作超时"}
    except Exception as exc:
        return {"update_available": False, "error": str(exc)}
