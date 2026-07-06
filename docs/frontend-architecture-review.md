# 前端架构审查报告

> 审查日期: 2026-07-03 | 审查范围: `frontend/src/` 全部 51 个文件

---

## 目录

- [整体评价](#整体评价)
- [项目结构](#项目结构)
- [高危问题 (P0)](#高危问题-p0)
- [中危问题 (P1-P2)](#中危问题-p1-p2)
- [低危问题 (P3)](#低危问题-p3)
- [组件详细审查](#组件详细审查)
- [类型安全](#类型安全)
- [错误处理模式](#错误处理模式)
- [代码重复清单](#代码重复清单)
- [硬编码值清单](#硬编码值清单)
- [TODO/FIXME/HACK](#todofixmehack)

---

## 整体评价

| 维度 | 评分 | 说明 |
|------|------|------|
| 项目结构 | ★★★★☆ | API → hooks → components → pages 分层清晰 |
| 类型安全 | ★★★☆☆ | 核心类型定义完善，但有多处 `any` |
| 错误处理 | ★★☆☆☆ | 20+ 处静默吞没错误 |
| 代码复用 | ★★☆☆☆ | 多处重复代码未提取 |
| 可访问性 | ★★☆☆☆ | 自定义对话框缺少 ARIA 支持 |
| 性能 | ★★★☆☆ | 有优化空间但总体可接受 |
| 可维护性 | ★★★☆☆ | 单个文件过大，状态管理可改进 |

---

## 项目结构

```
frontend/src/
├── main.tsx                    # 入口 + 路由
├── App.tsx                     # (死代码 — 仅重新导出)
├── App.css                     # Tailwind v4 主题 tokens
├── api/
│   ├── client.ts               # 通用 fetch 包装器
│   ├── rssApi.ts               # RSS API (28 个函数, 367 行)
│   └── torrentApi.ts           # Torrent API (8 个函数, 203 行)
├── types/
│   └── preview.ts              # 共享 TS 接口 (182 行)
├── lib/
│   ├── utils.ts                # cn() 工具
│   └── toast.ts                # toast 辅助
├── hooks/
│   ├── useTheme.ts
│   ├── useConfig.ts
│   ├── useSubscriptions.ts
│   ├── useDownloadHistory.ts
│   └── useRssSearch.ts
├── pages/
│   ├── RssPage.tsx             # 最复杂页面 (208 行, 14 个 useState)
│   ├── SettingsPage.tsx
│   └── TorrentPage.tsx
├── components/
│   ├── rss/                    # RSS 相关 (12 个组件)
│   ├── settings/               # 设置表单 (4 个组件)
│   ├── Cards/                  # 卡片组件
│   ├── layout/                 # AppLayout
│   ├── icons/                  # SVG 图标集
│   ├── shared/                 # FieldGroup (未充分使用)
│   └── ui/                     # shadcn/ui 组件 (12 个)
```

**状态管理**: 纯 useState + props 传递，无全局 Context。对于当前规模可接受。

**Props 最深嵌套**: RssPage → SubtitleGroupDialog → SubtitleGroupTable → TagFilterPanel (3 层)

---

## 高危问题 (P0)

### 1. 静默错误吞没 — 20+ 处

**文件**: `useSubscriptions.ts`, `DownloadHistoryDialog.tsx`, `SubtitleGroupTable.tsx`, `RssPage.tsx` 等多处

```typescript
// 反模式 — 用户永远不知道操作失败
catch { /* */ }
```

**影响**: 用户操作失败后无任何反馈，无法排查问题。

**建议**: 每个空 catch 至少 `console.error(e)`，关键操作应 toast 提示用户。

---

### 2. 防抖内存泄漏

**文件**: `RssSearchBar.tsx:42-53`, `InfoCards.tsx:120-130`

```typescript
// 组件卸载时 setTimeout 未清理
debounceRef.current = setTimeout(async () => {
  setCandidates(results);  // 可能在 unmounted component 上调用
}, 300);
```

**影响**: 用户在 300ms 内切换页面 → React 警告 "setState on unmounted component"。

**建议**: 
```typescript
useEffect(() => {
  return () => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
  };
}, []);
```

---

### 3. MatchTable.tsx 1045 行 — 巨型组件

**文件**: `components/MatchTable.tsx`

| 内容 | 行号 | 应放在 |
|------|------|--------|
| 接口定义 | 69-98 | `types/preview.ts` |
| fuzzyMatchTmdb | 105-191 | `lib/match-utils.ts` |
| computeMatches | 193-338 | `lib/match-utils.ts` |
| 批量字幕上传 | 719-795 | `hooks/useSubtitleUploader.ts` |
| UI 渲染 | 340-1044 | `MatchTable.tsx` + `MatchRow.tsx` |

**影响**: 单文件过大，难以维护和测试。

---

### 4. DownloadHistoryDialog 绕过 API 层

**文件**: `DownloadHistoryDialog.tsx:144`

```typescript
// 直接 fetch，绕过了 rssApi 和 client.ts 的统一错误处理
const res = await fetch(`/api/rss/download-history/${sub.bangumi_id}/${editSort}`, {
  method: 'PATCH',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ tmdb_ep: tmdbEp, tmdb_season: tmdbSeason }),
});
```

**影响**: 唯一绕过 `apiFetch` 超时 + HTML 检测的地方，错误处理不一致。

**建议**: 使用 `rssApi.updateEpisodeHistory()`。

---

## 中危问题 (P1-P2)

### 5. API 错误解析重复 18 次

**文件**: `api/rssApi.ts` (18 个函数中重复)

```typescript
const text = await res.text();
let msg = text;
try { const j = JSON.parse(text); msg = j.detail || text; } catch { /* */ }
throw new Error(msg || `HTTP ${res.status}`);
```

**建议**: 提取为工具函数
```typescript
// lib/api-utils.ts
export async function parseApiError(res: Response): Promise<never> {
  const text = await res.text();
  let msg = text;
  try { const j = JSON.parse(text); msg = j.detail || text; } catch { /* */ }
  throw new Error(msg || `HTTP ${res.status}`);
}
```

---

### 6. 自定义对话框无无障碍支持

**文件**: `SubtitleGroupDialog.tsx`, `MikanSearchDialog.tsx`, `DownloadHistoryDialog.tsx`, `UnsubscribeDialog.tsx`

4 个模态框全部用 `fixed inset-0` 手写，缺少：

- 焦点陷阱 (focus trap)
- Escape 键关闭
- `role="dialog"` / `aria-modal`
- 屏幕阅读器支持

**影响**: 键盘用户和屏幕阅读器用户无法正常使用。

**建议**: 使用项目已有的 `@base-ui/react` Dialog 组件，或至少添加 ARIA 属性。

---

### 7. 死代码清理

| 文件 | 行数 | 说明 |
|------|------|------|
| `components/ui/sidebar.tsx` | 435 | shadcn/ui Sidebar，完全未使用 |
| `App.tsx` | 7 | 仅含注释和重新导出 |
| `AppLayout.tsx` 搜索框 | 3 | 装饰性输入框，无 onChange |
| `AppLayout.tsx` 通知按钮 | 4 | 装饰性红点，无 onClick |
| `AppLayout.tsx` Dashboard 按钮 | 4 | 无 onClick |
| `SubscriptionList.tsx` AddCard | 1 | `onClick={() => {}}` |

---

### 8. 表单组件重复定义 3 次

**文件**: `GeneralConfigForm.tsx`, `QbitConfigForm.tsx`, `TorrentConfigForm.tsx`

三个组件各自定义了 `FieldRow`、`fieldClass`、`val` 辅助函数，逻辑完全相同。

**影响**: 修改表单样式需要改 3 个文件。

**建议**: 项目已有 `components/shared/FieldGroup.tsx`，应统一使用。

---

### 9. useConfig 的 saved 定时器泄漏

**文件**: `hooks/useConfig.ts:54`

```typescript
// handleSave 被调用第二次时，第一个 setTimeout 未被清除
setTimeout(() => setSaved(false), 2000);
```

**建议**: 用 `useRef` 保存 timeout ID，在下次调用前清理。

---

### 10. useSubscriptions 乐观更新缺陷

**文件**: `hooks/useSubscriptions.ts`

`subscribe` 函数等待完整 API 往返后才更新本地列表。订阅操作无乐观 UI，慢连接用户看到 500ms+ 延迟。

---

## 低危问题 (P3)

### 11. 硬编码颜色 `#f09199`

**文件**: `TorrentUpload.tsx`, `Cards/MappingCard.tsx`

多处直接使用 `#f09199` 而非 CSS 变量，破坏主题一致性。

**建议**: 定义 `--color-sakura` 或类似变量。

---

### 12. 文件大小格式化不统一

| 位置 | 约定 | 示例 |
|------|------|------|
| `FeedPreview.tsx` `formatSize()` | 1000 进制 | 1.5 MB |
| `EpisodeTable.tsx` `formatBytes()` | 1024 进制 | 1.5 MiB |

**建议**: 统一为一个工具函数到 `lib/utils.ts`。

---

### 13. RssPage 性能优化

```typescript
// 每次渲染都重新计算 — 应用 useMemo
const takenRoles = (() => { ... })();        // RssPage:113
const getSubMode = (subgroupId) => { ... };  // RssPage:102

// 每次渲染重新创建 — 应提到模块作用域
const CloseIcon = (...) => { ... };          // MikanSearchDialog:126
const Spinner = (...) => { ... };            // MikanSearchDialog:131
```

---

### 14. 硬编码 SVG 图标未使用图标集

**文件**: `TorrentPreview.tsx:201-241`

内联了多个 SVG 图标，但项目已有 `components/icons/index.tsx` 图标集。

---

## 组件详细审查

### RssPage.tsx (208 行, 14 个 useState)

**组件树**:
```
RssPage
 ├── RssSearchBar
 ├── SubtitleGroupDialog (条件)
 │    └── SubtitleGroupTable
 │         ├── TagFilterPanel
 │         └── FeedPreview
 ├── MikanSearchDialog (条件)
 ├── SubscriptionList
 │    └── SubscriptionCard
 ├── DownloadHistoryDialog
 │    ├── LeftSidebar
 │    └── EpisodeTable
 └── UnsubscribeDialog
```

**问题**:
- 14 个 useState 过多，状态逻辑分散
- `handleSearch` 直接操作 MikanSearchDialog 状态（关注点混淆）
- 订阅操作无乐观 UI
- `handleDeleteGroupRss` 吞没错误

---

### MikanSearchDialog.tsx (315 行)

**优点**: 状态机模式 (`DialogState` 联合类型) 用得好，比散落的 boolean 清晰。

**问题**:
- SVG 组件在 render 中定义，每次渲染重新创建
- `switchToManual` 的结果传递逻辑脆弱

---

### FeedPreview.tsx (152 行)

**问题**:
- v1/v2 去重逻辑在组件体内运行（非 useMemo）
- 与 EpisodeTable 的文件大小格式化不一致

---

### EpisodeTable.tsx (300 行)

**问题**:
- Fragment 缺少 key（React 可容忍但非最佳实践）
- TMDB 编辑表单逻辑复杂

---

### 设置表单组件

**GeneralConfigForm, QbitConfigForm, TorrentConfigForm**:
- `FieldRow` + `fieldClass` + `val` 各复制一份
- `GeneralConfigForm` 的 `proxyEnabled` 是本地状态，刷新后丢失

**RssToolsPanel**:
- 初始加载无 loading 状态
- `excludePatterns` 从后端初始化但后续不同步

---

## 类型安全

### any 使用清单

| 文件 | 位置 | 说明 |
|------|------|------|
| `TorrentPage.tsx` | useState | `useState<any>` |
| `TorrentPreview.tsx` | props | 多个 `any` 类型 props |
| `MatchTable.tsx` | computeMatches | 参数类型为 `any` |
| `InfoCards.tsx` | props | 多个 `any` 类型 props |
| `torrentApi.ts` | parseAndSearchTorrent | 返回 `any` |

### TypeScript 改进建议

- `MatchTable.tsx:869` 的 `@ts-ignore webkitdirectory` 应添加类型声明文件
- `rssApi.updateSubscription` 的 `Record<string, unknown>` 应使用具体类型

---

## 错误处理模式

当前存在 5 种不一致的模式：

| 模式 | 使用场景 | 问题 |
|------|---------|------|
| `catch { /* */ }` | 20+ 处 | 完全无反馈 |
| `showError(err)` | 页面级操作 | 仅部分覆盖 |
| `useState<string>` error | TorrentPage | 需手动管理 |
| 忽略回调 | useDownloadHistory | 流错误被丢弃 |
| 异常传播 | 少数情况 | 不统一 |

**统一建议**: 所有 catch 至少 `console.error`，面向用户的操作应 toast 提示。

---

## 代码重复清单

| 重复内容 | 位置 | 次数 | 建议 |
|---------|------|------|------|
| API 错误解析 7 行 | `rssApi.ts` | 18 | 提取 `parseApiError()` |
| `FieldRow` 组件 | 3 个设置表单 | 3 | 统一用 `FieldGroup` |
| `fieldClass` 函数 | 3 个设置表单 | 3 | 移到 `lib/utils.ts` |
| `val` 函数 | 3 个设置表单 | 3 | 移到 `lib/utils.ts` |
| 防抖搜索 + 下拉 | `RssSearchBar`, `InfoCards` | 2 | 提取 `useAutocomplete` hook |
| 点击外部监听 | `RssSearchBar`, `InfoCards` | 2 | 提取 `useClickOutside` hook |
| 文件大小格式化 | `FeedPreview`, `EpisodeTable` | 2 (不同约定) | 统一到 `lib/utils.ts` |

---

## 硬编码值清单

| 值 | 位置 | 说明 |
|---|------|------|
| `15_000` | `api/client.ts` | fetch 超时 |
| `300` | `RssSearchBar.tsx`, `InfoCards.tsx` | 防抖延迟 |
| `0.55` | `MatchTable.tsx` | 模糊匹配阈值 |
| `#f09199` | `TorrentUpload.tsx`, `MappingCard.tsx` | 粉色硬编码 |
| `2000` | `SubtitleGroupTable.tsx` | 复制反馈闪烁 |
| `6000` / `3000` | `lib/toast.ts` | toast 持续时间 |
| `5` | `FeedPreview.tsx` | 初始可见条目数 |

---

## TODO/FIXME/HACK

| 位置 | 内容 |
|------|------|
| `TorrentUpload.tsx:112` | Magnet link 解析未实现 |
| `MatchTable.tsx:869` | `@ts-ignore webkitdirectory` |
| `GeneralConfigForm.tsx:170` | "Reset Defaults" 按钮占位 |
| `AppLayout.tsx:45` | Dashboard 按钮无 onClick |
| `SubscriptionList.tsx:117` | AddCard onClick 为空 |

---

## 优化路线图

| 优先级 | 改动 | 预计工作量 | 影响 |
|--------|------|-----------|------|
| **P0** | 修静默错误吞没 (+`console.error`) | 1h | 可调试性 |
| **P0** | 修复防抖内存泄漏 (RssSearchBar, InfoCards) | 0.5h | 稳定性 |
| **P1** | 提取 `parseApiError` 工具函数 | 1h | DRY, 一致性 |
| **P1** | 修复 DownloadHistoryDialog 绕过 API 层 | 0.5h | 一致性 |
| **P2** | 拆分 MatchTable.tsx | 3h | 可维护性 |
| **P2** | 清理死代码 (sidebar.tsx, App.tsx 等) | 0.5h | 代码清洁 |
| **P2** | 统一表单组件 (FieldGroup) | 1h | DRY |
| **P3** | 对话框添加 ARIA / 使用 @base-ui Dialog | 3h | 无障碍 |
| **P3** | 统一文件大小格式化 | 0.5h | 一致性 |
| **P3** | 硬编码颜色 → CSS 变量 | 0.5h | 主题一致性 |
| **P3** | RssPage 性能优化 (useMemo) | 1h | 性能 |
