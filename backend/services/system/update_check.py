"""Read remote update status without changing the working tree."""

import logging
import os
import subprocess
import time


logger = logging.getLogger(__name__)

_CONNECTION_ERRORS = (
    "unexpected eof while reading",
    "tls connect error",
    "gnutls_handshake() failed",
    "ssl_connect",
    "connection reset",
    "connection was reset",
    "couldn't connect to server",
    "could not resolve host",
    "could not resolve proxy",
    "http/2 stream",
    "http/2 framing",
)


def inspect_remote(source_dir: str, branch: str) -> dict:
    def git(*args: str, timeout: int = 10) -> str:
        deadline = time.monotonic() + timeout
        attempts = 3 if args[0] == "fetch" else 1
        for attempt in range(attempts):
            # 只对连接故障重试；兼容选项仅用于本次命令，不修改 Git 全局配置。
            options = ["-c", "http.version=HTTP/1.1"] if attempt else []
            result = subprocess.run(
                ["git", *options, *args], cwd=source_dir, capture_output=True, text=True,
                timeout=max(0.001, deadline - time.monotonic()),
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if not result.returncode:
                return result.stdout.strip()
            detail = result.stderr.strip()
            connection_error = any(marker in detail.lower() for marker in _CONNECTION_ERRORS)
            delay = attempt + 1
            if connection_error and attempt + 1 < attempts and deadline - time.monotonic() > delay:
                logger.warning("Git %s connection interrupted; retrying (%s/%s)", args[0], attempt + 2, attempts)
                time.sleep(delay)
                continue
            hint = (
                "；连接在传输过程中中断，请稍后重试，并检查容器到 GitHub 的网络及代理配置"
                if connection_error else ""
            )
            raise RuntimeError(f"git {args[0]} 失败: {detail[:200]}{hint}")

    try:
        remote_ref = f"refs/remotes/origin/{branch}"
        # 显式指定目标引用，兼容单分支克隆，并在强制推送后刷新远程跟踪引用。
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
