"""Inspect title parsing and live index/Bangumi matching for one saved RSS resource.

Usage: python3 scripts/debug/rss_title.py [--id ID | --random | --all | --retry-failed] [--save]

By default it runs five fixed edge-case resources without saving. ``--save``
persists completed recognition results; ``--all --save`` backfills all resources.
It never opens a .torrent file.
"""

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.db.connection import DB_PATH
from backend.db import resource_recognitions
from backend.services.resource_monitor.recognize import recognize_resource
from backend.services.resource_monitor.titles import parse_title


EDGE_CASES = (
    (23, "KTNBytes 第二季 01-13+SPx3：正片范围与 SP 混合"),
    (52, "VCB [Fin]：没有季号或集数，需默认第一季全季"),
    (51, "VCB S1+S2+MOVIE：多季与独立电影候选"),
    (76, "VCB S2-S4 + S1：季范围后又追加离散季"),
    (63, "VCB MOVIE：纯电影不应当作第一季电视剧"),
)


def pick_resource(database: Path, resource_id: int | None = None,
                  source: str = "") -> sqlite3.Row | None:
    if not database.is_file():
        raise FileNotFoundError(f"资源数据库不存在: {database}")
    # Open read-only so a test run cannot change resources or recognition data.
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        filters = ["title != ''"]
        parameters: list[int | str] = []
        if resource_id is not None:
            filters.append("id = ?")
            parameters.append(resource_id)
        if source:
            filters.append("source = ?")
            parameters.append(source)
        query = ("SELECT id, source, title, index_type FROM resources WHERE "
                 + " AND ".join(filters) + " ORDER BY RANDOM() LIMIT 1")
        return connection.execute(query, parameters).fetchone()


def list_resource_ids(database: Path, source: str = "") -> list[int]:
    """List IDs through a read-only connection for explicit batch backfills."""
    if not database.is_file():
        raise FileNotFoundError(f"资源数据库不存在: {database}")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        query = "SELECT id FROM resources WHERE title != ''"
        params: list[str] = []
        if source:
            query += " AND source = ?"
            params.append(source)
        return [row[0] for row in connection.execute(query + " ORDER BY id", params)]


def list_retry_ids(database: Path, source: str = "") -> list[int]:
    """Select only prior failed or waiting_config recognitions."""
    if not database.is_file():
        raise FileNotFoundError(f"资源数据库不存在: {database}")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        query = ("SELECT r.id FROM resources AS r "
                 "JOIN resource_recognitions AS x ON x.resource_id = r.id "
                 "WHERE r.title != '' AND x.status IN ('failed', 'waiting_config')")
        params: list[str] = []
        if source:
            query += " AND r.source = ?"
            params.append(source)
        return [row[0] for row in connection.execute(query + " ORDER BY r.id", params)]


STEP_LABELS = {
    "title": "标题线索", "missing_config": "缺少索引配置",
    "search": "索引作品搜索", "mapping": "Bangumi 映射表",
    "index_episodes": "索引季剧集", "bangumi_comparison": "Bangumi 集名比较",
    "candidates": "最终候选", "unresolved": "未识别", "error": "识别错误",
}


def print_step(step: str, details: dict, *, verbose: bool = False) -> None:
    if verbose:
        print(f"\n===== {STEP_LABELS.get(step, step)} =====", flush=True)
        print(json.dumps(details, ensure_ascii=False, indent=2, default=str), flush=True)
        return
    if step == "title":
        return  # The compact title summary is printed before the network call.
    if step == "search":
        top = ", ".join(f"{item['id']}:{item['name']}" for item in details["top_results"][:3])
        print(f"  搜索 {details['index_type'].upper()} {details['query']!r}: "
              f"{details['result_count']} 条" + (f"；{top}" if top else ""), flush=True)
    elif step == "mapping":
        entries = details["entries"]
        label = ", ".join(f"{item['bangumi_id']}:{item.get('name', '')}"
                          for item in entries[:8])
        print(f"  映射表: {len(entries)} 条" + (f"；{label}" if label else ""), flush=True)
    elif step == "index_episodes":
        count = details["count"]
        print(f"  索引第 {details['season']} 季: " +
              (f"{count} 集" if details["available"] else "剧集不可用"), flush=True)
    elif step == "bangumi_comparison":
        print(f"    Bangumi {details['bangumi_id']}: {details['episode_count']} 集，"
              f"匹配 {details['match_count']}，{details['decision']}（{details['reason']}）", flush=True)
    elif step == "candidates":
        for item in details["candidates"]:
            label = "电影" if item["media_type"] == "MOVIE" else f"第 {item['index_season']} 季"
            print(f"  候选 Bangumi {item['bangumi_id']} {label}: {item['decision']}，"
                  f"匹配 {item['match_count']}（{item['reason']}）", flush=True)
    elif step in {"unresolved", "missing_config", "error"}:
        print(f"  {STEP_LABELS[step]}: {details.get('reason') or details.get('message') or details.get('error')}",
              flush=True)


def run_resource(resource: sqlite3.Row, *, verbose: bool = False,
                 save: bool = False, cache: dict | None = None) -> str:
    """Run one case; persist only a completed derived result when requested."""
    try:
        parsed = parse_title(resource["title"], resource["index_type"])
    except (TypeError, ValueError) as exc:
        print(f"标题测试失败: {exc}", file=sys.stderr)
        return "failed"

    print(f"资源 ID: {resource['id']}")
    print(f"来源: {resource['source']}；索引: {resource['index_type']}")
    print(f"标题: {resource['title']}")
    range_label = "不适用" if parsed["media_types"] == ["MOVIE"] else parsed["episode_range"] or "全季"
    special_label = f"；SP {parsed['special_count']} 集" if parsed["special_count"] else ""
    print(f"标题线索: 季 {parsed['seasons']}；范围 {range_label}；"
          f"类型 {parsed['media_types']}{special_label}；别名 {parsed['name_candidates']}")
    if verbose:
        print("anitopy 原始结果:")
        print(json.dumps(parsed["raw_anitopy"], ensure_ascii=False, indent=2, default=str))
        print("规范化标题线索:")
        print(json.dumps({key: value for key, value in parsed.items() if key != "raw_anitopy"},
                         ensure_ascii=False, indent=2, default=str))
    result = asyncio.run(recognize_resource(
        dict(resource), cache=cache, persist=False,
        on_step=lambda step, details: print_step(step, details, verbose=verbose)))
    print(f"  识别状态: {result['status']}")
    if save:
        if result["status"] in {"complete", "unresolved"}:
            resource_recognitions.save(resource["id"], result["title_snapshot"],
                                       result["status"], result["candidates"],
                                       result.get("reason", ""))
            stored = resource_recognitions.get(resource["id"])
            if stored is None or stored["status"] != result["status"] or len(stored["candidates"]) != len(result["candidates"]):
                raise RuntimeError(f"资源 {resource['id']} 的识别结果写入后校验失败")
            print(f"  已保存: {result['status']}，规则 v{result['title_snapshot']['rule_version']}，"
                  f"Bangumi 候选 {len(stored['candidates'])} 条")
        else:
            print("  未写入: 识别未完成，保留数据库中原有结果")
    if result["status"] == "failed" and "401" in result.get("error", ""):
        print(f"提示: {resource['index_type'].upper()} 返回 401，请核对设置页面中的 API Key。")
    return result["status"]


def main() -> int:
    parser = argparse.ArgumentParser(description="默认依次测试五条易出错的 RSS 资源")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--id", type=int, dest="resource_id", help="只测试指定资源 ID")
    selection.add_argument("--random", action="store_true", help="随机测试一条资源")
    selection.add_argument("--all", action="store_true", help="处理全部已保存的 RSS 资源")
    selection.add_argument("--retry-failed", action="store_true", help="只重试 failed 和 waiting_config 记录")
    parser.add_argument("--source", default="", help="与 --random、--all 或 --retry-failed 合用")
    parser.add_argument("--verbose", action="store_true", help="打印 anitopy 原文和完整逐步 JSON")
    parser.add_argument("--save", action="store_true", help="将完成识别的快照和候选写入数据库")
    args = parser.parse_args()
    if args.source and not (args.random or args.all or args.retry_failed):
        parser.error("--source 需要与 --random、--all 或 --retry-failed 一起使用")

    if args.resource_id is not None:
        cases = [(args.resource_id, "指定资源")]
    elif args.random:
        cases = [(None, "随机资源")]
    elif args.all:
        try:
            cases = [(resource_id, "批量回填") for resource_id in list_resource_ids(Path(DB_PATH), args.source)]
        except (OSError, sqlite3.Error) as exc:
            print(f"读取资源列表失败: {exc}", file=sys.stderr)
            return 1
    elif args.retry_failed:
        try:
            cases = [(resource_id, "重试未成功识别")
                     for resource_id in list_retry_ids(Path(DB_PATH), args.source)]
        except (OSError, sqlite3.Error) as exc:
            print(f"读取待重试资源失败: {exc}", file=sys.stderr)
            return 1
    else:
        cases = list(EDGE_CASES)

    results: list[tuple[int | None, str]] = []
    cache: dict = {}
    print(f"本次选中 {len(cases)} 条资源；{'写入数据库' if args.save else '只读验证'}。")
    for position, (resource_id, reason) in enumerate(cases, start=1):
        print(f"\n{'=' * 24} 案例 {position}/{len(cases)} {'=' * 24}")
        print(f"测试理由: {reason}")
        try:
            resource = pick_resource(Path(DB_PATH), resource_id, args.source)
        except (OSError, sqlite3.Error) as exc:
            print(f"读取资源失败: {exc}", file=sys.stderr)
            results.append((resource_id, "failed"))
            continue
        if resource is None:
            print(f"资源不存在或没有标题: {resource_id}", file=sys.stderr)
            results.append((resource_id, "missing"))
            continue
        try:
            status = run_resource(resource, verbose=args.verbose, save=args.save, cache=cache)
        except Exception as exc:
            print(f"保存资源 {resource['id']} 失败: {exc}", file=sys.stderr)
            status = "save_failed"
        results.append((resource["id"], status))

    print("\n===== 五例测试汇总 =====" if len(cases) == 5 else "\n===== 测试汇总 =====")
    for resource_id, status in results:
        print(f"资源 {resource_id}: {status}")
    counts = {status: sum(result == status for _, result in results)
              for status in sorted({result for _, result in results})}
    print(f"汇总: {counts}")
    return 0 if all(status in {"complete", "unresolved"} for _, status in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
