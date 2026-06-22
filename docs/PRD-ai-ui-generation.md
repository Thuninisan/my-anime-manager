# PRD: AI 辅助 UI 生成工具链

> **版本**: v1.0  
> **日期**: 2026-06-22  
> **状态**: Draft  
> **作者**: Thunini  
> **前置依赖**: [前端 UI 重构 PRD](./PRD-frontend-refactor.md) 实施完成

---

## 目录

1. [产品概述](#1-产品概述)
2. [核心能力](#2-核心能力)
3. [生成规范体系](#3-生成规范体系)
4. [组件模板库](#4-组件模板库)
5. [Prompt 工程](#5-prompt-工程)
6. [AI 上下文协议](#6-ai-上下文协议)
7. [生成工作流](#7-生成工作流)
8. [质量保障](#8-质量保障)
9. [文件产出物清单](#9-文件产出物清单)
10. [验收标准](#10-验收标准)

---

## 1. 产品概述

### 1.1 定位

AI 辅助 UI 生成工具链 是一套 **规范 + 模板 + Prompt** 体系，让开发者能用自然语言描述需求，由 AI 自动生成符合项目规范的前端代码。生成结果与手写代码风格一致、类型安全、可直接合并。

### 1.2 目标用户

| 用户 | 场景 | 频率 |
|---|---|---|
| 本项目开发者 (Thunini) | "在设置页新增一个日志级别配置项" | 经常 |
| 本项目开发者 | "为 RSS 模块新增一个定时任务管理页面" | 偶尔 |
| AI (Claude Code) | 读取项目规范后，按指令生成代码 | 每次生成时 |

### 1.3 核心价值

```
开发者说：               AI 需要知道：            AI 产出：
──────────              ──────────────           ──────────
"加一个新页面"    +     项目规范 + 模板库   =    完整的页面组件
                                                  + API 层函数
                                                  + TypeScript 类型
                                                  + 路由注册
                                                  + 骨架屏 + 空状态 + 错误状态
```

### 1.4 非目标

- **不是** 可视化拖拽搭建工具
- **不是** 从 OpenAPI 全自动生成（那是另一个方向，可作为输入源之一）
- **不是** 无代码/低代码平台
- **不替代** 开发者对业务逻辑的判断和 review

---

## 2. 核心能力

### 2.1 生成能力矩阵

| 能力 | 输入 | 输出 | 优先级 |
|---|---|---|---|
| **新增页面** | 页面名称 + 功能描述 + 数据源 | page组件 + 路由注册 + 子组件骨架 | P0 |
| **新增表单字段** | 字段名 + 类型 + 验证规则 + 所属组件 | 表单行 + FieldGroup + API类型补丁 | P0 |
| **新增表格列** | 列名 + 数据路径 + 格式化规则 | 表格列定义 + 类型补丁 | P1 |
| **新增 API 端点** | 端点路径 + method + 请求/响应结构 | api层函数 + 类型 + hook | P0 |
| **新增 UI 组件** | 组件描述 + 变体 | ui/ 组件 + Story(可选) | P1 |
| **修改现有组件** | 目标组件 + 变更描述 | diff 式修改，保持现有风格 | P0 |
| **错误/空/加载状态补齐** | 目标组件 | 骨架屏 + 空状态 + 错误边界 | P1 |
| **响应式适配** | 目标组件 | Tailwind 响应式 class | P2 |

### 2.2 生成示例

```
输入:
  "在设置页 RSS 工具区新增一个'最大并发下载数'配置项，
   类型为整数，范围 1-10，默认值 3"

AI 自动完成:
  1. types/preview.ts          → AppConfig 接口 +max_concurrent_downloads: number
  2. api/rssApi.ts             → updateRssSettings 参数类型自动包含新字段
  3. components/settings/      → 在 RssToolsPanel 中新增 FieldGroup 行
  4. config.py (提示)          → 后端 _DEFAULTS + "MAX_CONCURRENT_DOWNLOADS": 3
  5. 验证                      → TypeScript 编译检查 + ESLint
```

---

## 3. 生成规范体系

### 3.1 规范文档架构

```
docs/
├── conventions/
│   ├── README.md                    # 规范索引
│   ├── 01-project-structure.md      # 目录结构约定
│   ├── 02-naming-conventions.md     # 命名规范
│   ├── 03-component-patterns.md     # 组件编写模式
│   ├── 04-styling-guide.md          # 样式规范 (Tailwind)
│   ├── 05-api-integration.md        # API 调用规范
│   ├── 06-state-management.md       # 状态管理模式
│   ├── 07-error-handling.md         # 错误处理规范
│   ├── 08-typescript-guide.md       # TypeScript 使用规范
│   └── 09-accessibility.md          # 无障碍规范
│
├── templates/
│   ├── page.template.tsx            # 页面组件模板
│   ├── component.template.tsx       # 通用组件模板
│   ├── hook.template.ts             # 自定义 Hook 模板
│   ├── api-function.template.ts     # API 函数模板
│   ├── form-field.template.tsx      # 表单字段模板
│   ├── table-page.template.tsx      # 表格页面模板
│   └── dialog.template.tsx          # 弹窗模板
│
└── prompts/
    ├── add-page.md                  # "新增页面" prompt
    ├── add-field.md                 # "新增表单字段" prompt
    ├── add-api.md                   # "新增 API 端点" prompt
    ├── add-table-column.md          # "新增表格列" prompt
    ├── add-component.md             # "新增 UI 组件" prompt
    ├── fix-error-states.md          # "补齐错误/加载/空状态" prompt
    └── refactor-component.md        # "重构组件" prompt
```

### 3.2 关键规范摘要

#### 命名规范

```typescript
// ✅ 组件: PascalCase, 文件名同名
export default function SeasonMappingTable() {}

// ✅ Hook: use + PascalCase
export function useRssSearch() {}

// ✅ API 函数: camelCase, 动词开头
export async function lookupBangumiRss() {}

// ✅ 类型/接口: PascalCase, 名词
export interface SubscriptionOut {}

// ✅ 事件处理: handle + 动作
const handleSeasonChange = () => {};

// ✅ 常量: UPPER_SNAKE_CASE
const EXTRA_KEY_BASE = 900;
```

#### 组件结构

```typescript
// 必须按此顺序组织
// 1. imports (React → 第三方 → @/ → 相对路径)
// 2. types (如有本地 interface)
// 3. constants
// 4. helper functions (纯函数，不依赖组件状态)
// 5. export default function Component() { ... }
//    5a. hooks (useState, useEffect, custom hooks)
//    5b. derived state (useMemo, useCallback)
//    5c. event handlers
//    5d. conditional renders (loading → empty → error → data)
//    5e. main JSX return
```

#### 样式规范

```typescript
// ✅ 使用 Tailwind class + cn() 合并
// ✅ 颜色用语义 token: bg-primary, text-muted-foreground, border-border
// ✅ 间距遵循 4px 基准: p-4 (16px), gap-3 (12px), space-y-2 (8px)
// ❌ 禁止内联 style (除动态渐变/百分比宽度)
// ❌ 禁止硬编码颜色: #fff, rgb(...)
// ❌ 禁止自定义 CSS (除动画/复杂布局)
```

#### 状态覆盖规则

```typescript
// 每个数据展示组件必须有四种状态:
type ComponentState<T> = 
  | { status: 'loading' }           // 骨架屏
  | { status: 'empty' }             // 空状态插画 + 引导文案
  | { status: 'error'; message: string; retry: () => void }  // 错误 + 重试
  | { status: 'data'; data: T };    // 正常渲染
```

---

## 4. 组件模板库

### 4.1 页面模板 (`templates/page.template.tsx`)

```typescript
import { useState, useEffect, useCallback } from 'react';
import type { Something } from '@/types/preview';
import { useSomething } from '@/hooks/useSomething';
import { SomethingSkeleton } from '@/components/shared/SomethingSkeleton';
import { EmptyState } from '@/components/ui/empty-state';
import { ErrorCard } from '@/components/shared/ErrorCard';

export default function SomethingPage() {
  const { data, loading, error, refetch } = useSomething();

  // ── Loading ──
  if (loading) {
    return <SomethingSkeleton />;
  }

  // ── Error ──
  if (error) {
    return (
      <ErrorCard
        title="加载失败"
        message={error}
        onRetry={refetch}
      />
    );
  }

  // ── Empty ──
  if (!data || data.items.length === 0) {
    return (
      <EmptyState
        icon="📭"
        title="暂无内容"
        description="还没有任何数据，点击下方按钮开始添加"
        action={{ label: '添加', onClick: () => {} }}
      />
    );
  }

  // ── Data ──
  return (
    <div className="max-w-[1200px] mx-auto space-y-6">
      {/* header */}
      {/* content */}
    </div>
  );
}
```

### 4.2 Hook 模板 (`templates/hook.template.ts`)

```typescript
import { useState, useCallback } from 'react';

interface UseSomethingOptions {
  // 可选配置项
}

interface UseSomethingReturn {
  data: Something | null;
  loading: boolean;
  error: string | null;
  execute: (params: Params) => Promise<void>;
  reset: () => void;
}

export function useSomething(opts?: UseSomethingOptions): UseSomethingReturn {
  const [data, setData] = useState<Something | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const execute = useCallback(async (params: Params) => {
    setLoading(true);
    setError(null);
    try {
      const result = await apiCall(params);
      setData(result);
    } catch (e) {
      const message = e instanceof Error ? e.message : '请求失败';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  const reset = useCallback(() => {
    setData(null);
    setError(null);
  }, []);

  return { data, loading, error, execute, reset };
}
```

### 4.3 API 函数模板 (`templates/api-function.template.ts`)

```typescript
// === GET: 查询列表 ===
export async function getSomethingList(params?: {
  page?: number;
  size?: number;
}): Promise<SomethingListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set('page', String(params.page));
  if (params?.size) searchParams.set('size', String(params.size));

  const url = `${API_BASE}/something${searchParams.size ? '?' + searchParams.toString() : ''}`;

  const res = await fetch(url, {
    headers: { 'Accept': 'application/json' },
    signal: AbortSignal.timeout(15_000),  // 15s timeout
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }

  return res.json();
}

// === POST: 创建 ===
export async function createSomething(
  input: SomethingCreateInput
): Promise<SomethingOut> {
  const res = await fetch(`${API_BASE}/something`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }

  return res.json();
}
```

### 4.4 表单字段模板 (`templates/form-field.template.tsx`)

```typescript
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

interface FieldGroupProps {
  id: string;
  label: string;
  hint?: string;
  type?: 'text' | 'number' | 'password';
  value: string;
  placeholder?: string;
  dirty?: boolean;
  error?: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}

export function FieldGroup({
  id,
  label,
  hint,
  type = 'text',
  value,
  placeholder,
  dirty,
  error,
  disabled,
  onChange,
}: FieldGroupProps) {
  return (
    <div className={cn(dirty && 'ring-1 ring-yellow-500/30 rounded-lg p-2 -mx-2')}>
      <label htmlFor={id} className="text-sm font-medium flex items-center gap-1.5">
        {label}
        {dirty && <span className="w-2 h-2 rounded-full bg-yellow-400" />}
      </label>
      <Input
        id={id}
        type={type}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        onChange={e => onChange(e.target.value)}
        className={cn('mt-1', error && 'border-destructive')}
      />
      {error && (
        <p className="text-xs text-destructive mt-1">{error}</p>
      )}
      {hint && !error && (
        <p className="text-xs text-muted-foreground mt-1">{hint}</p>
      )}
    </div>
  );
}
```

---

## 5. Prompt 工程

### 5.1 Prompt 设计原则

| 原则 | 说明 |
|---|---|
| **上下文先行** | 每次生成前注入规范文档 + 相关现有代码 |
| **示例驱动** | 提供 1-2 个手写标杆作为风格参考 |
| **约束显式** | 明确禁止项（如 `any`, 内联 style, 硬编码颜色） |
| **输出结构化** | 要求 AI 按文件列表输出，每个文件标明路径 |
| **自检验证** | prompt 结尾要求 AI 运行 `tsc --noEmit` 自检 |

### 5.2 Prompt 模板示例: 新增页面

文件: `prompts/add-page.md`

```markdown
## 任务: 新增页面

你需要创建一组新的前端文件，实现以下页面功能。

### 输入信息
- **页面名称**: {{pageName}} (PascalCase)
- **路由路径**: {{routePath}}
- **功能描述**: {{description}}
- **数据来源**: {{apiEndpoint}} (method: {{method}})
- **页面类型**: {{pageType}}  (table | form | detail | dashboard)

### 前置参考 (请先阅读以下文件)
1. `docs/conventions/03-component-patterns.md` — 组件编写模式
2. `docs/conventions/04-styling-guide.md` — 样式规范
3. `docs/conventions/05-api-integration.md` — API 调用规范
4. `src/pages/{{referencePage}}.tsx` — 参考页面 (风格标杆)

### 生成要求
你必须创建以下文件:

#### 文件 1: `src/pages/{{pageName}}Page.tsx`
- 遵循页面模板结构 (loading → empty → error → data)
- 导入 `{{pageName}}Skeleton`, `EmptyState`, `ErrorCard`
- 使用 `use{{pageName}}` hook 获取数据
- 如果是 table 型: 使用 `@/components/ui/table` 组件
- 如果是 form 型: 使用 `FieldGroup` 组件

#### 文件 2: `src/hooks/use{{pageName}}.ts`
- 遵循 Hook 模板结构
- 导出 `{ data, loading, error, execute, reset }`

#### 文件 3: 类型补充到 `src/types/preview.ts`
- 新增必要的 interface (命名遵循 `{{feature}}Request` / `{{feature}}Response` 约定)

#### 文件 4: 路由注册
- 在 `src/App.tsx` 中添加新路由
- 在 `src/components/layout/Sidebar.tsx` 中添加导航项（如需要）

### 编码规范 (必须遵守)
- ❌ 禁止使用 `any` 类型
- ❌ 禁止内联 `style={{}}` (动态渐变和百分比宽度除外)
- ❌ 禁止硬编码颜色 (#xxx, rgb())
- ✅ 所有 Tailwind class 通过 `cn()` 合并
- ✅ 所有文本内容使用中文
- ✅ 每个组件文件只导出一个默认组件
- ✅ 组件行数 < 200 行

### 验证步骤
生成完成后，请运行:
```bash
cd frontend && npx tsc --noEmit && npx eslint src/
```
如有错误，请修复后重新输出。

### 示例参考

以下是项目中一个标杆表格页面的代码结构:

```typescript
// src/pages/RssPage.tsx (摘录结构示意)
export default function RssPage() {
  const { data, loading, error, search } = useRssSearch();

  if (loading) return <SearchSkeleton />;
  if (error) return <ErrorCard message={error} onRetry={...} />;

  return (
    <div className="max-w-3xl mx-auto flex flex-col gap-4">
      <RssSearchBar onSearch={search} />
      {data && <RssSearchResult result={data} />}
      <SubscriptionList />
    </div>
  );
}
```

请严格遵循以上结构生成 {{pageName}}Page.tsx。
```

### 5.3 Prompt 模板示例: 新增表单字段

```markdown
## 任务: 新增表单字段

在现有配置表单中新增一个字段。

### 输入信息
- **字段名**: {{fieldKey}}
- **字段类型**: {{fieldType}} (text | number | password | select | switch | slider)
- **显示标签**: {{label}}
- **默认值**: {{defaultValue}}
- **提示文字**: {{hint}}
- **目标组件**: {{targetComponentPath}}
- **所属 API 类型**: {{apiType}} (AppConfig 的字段 / 独立 API)

### 需要修改的文件
1. `src/types/preview.ts` — 在 {{apiType}} 接口中新增字段
2. `{{targetComponentPath}}` — 在表单 JSX 中新增字段行
3. (如后端需要) 提示 `config.py` 添加默认值

### 生成格式
请只输出每个文件的 diff (使用 `+` `-` 标记)，不要重写整个文件。

### 示例

```diff
// types/preview.ts
export interface AppConfig {
+  {{fieldKey}}: {{tsType}};
}
```

```diff
// {{targetComponentPath}}
+  <FieldGroup
+    id="cfg-{{fieldKey}}"
+    label="{{label}}"
+    hint="{{hint}}"
+    type="{{fieldType}}"
+    value={String(config.{{fieldKey}})}
+    placeholder="{{defaultValue}}"
+    onChange={v => handleChange('{{fieldKey}}', v)}
+  />
```
```

---

## 6. AI 上下文协议

### 6.1 项目知识注入

每次 AI 生成代码前，自动注入以下上下文：

#### CLAUDE.md（项目级）

```markdown
# CLAUDE.md 补充: UI 生成指令

## 生成模式识别

当用户说以下模式时，触发对应 prompt:
- "加一个新页面" → 使用 prompts/add-page.md
- "加一个字段" / "新增配置项" → 使用 prompts/add-field.md
- "加一个 API" → 使用 prompts/add-api.md
- "加一列" → 使用 prompts/add-table-column.md
- "加一个组件" → 使用 prompts/add-component.md
- "补状态" / "加骨架屏" → 使用 prompts/fix-error-states.md

## 生成时必读文件
1. docs/conventions/ 下所有文件（按需加载具体子文件）
2. 与需求最相似的一个现有组件（作为风格参考）

## 生成后必做检查
- [ ] TypeScript 编译通过
- [ ] ESLint 无新增警告
- [ ] 没有硬编码颜色
- [ ] 没有 `any` 类型
- [ ] 没有内联 style
- [ ] loading/empty/error 三种状态齐全
```

### 6.2 模块索引

```typescript
// docs/MODULE_MAP.md — 帮助 AI 快速定位相关文件

// 当用户说"修改设置页的 XXX"，AI 应读取:
//   src/pages/SettingsPage.tsx
//   src/components/settings/SettingsSidebar.tsx
//   src/components/settings/GeneralConfigForm.tsx
//   src/components/settings/QbitConfigForm.tsx
//   src/components/settings/RssToolsPanel.tsx
//   src/hooks/useConfig.ts
//   src/api/torrentApi.ts (getConfig, updateConfig)
//   src/types/preview.ts (AppConfig)

// 当用户说"修改 RSS 订阅列表"，AI 应读取:
//   src/pages/RssPage.tsx
//   src/components/rss/SubscriptionList.tsx
//   src/components/rss/SubscriptionCard.tsx
//   src/hooks/useSubscriptions.ts
//   src/api/rssApi.ts (listSubscriptions, deleteSubscription, ...)
//   src/types/preview.ts (SubscriptionOut, ...)

// ...
```

### 6.3 组件依赖图

```
App.tsx
├── AppLayout.tsx
│   └── Sidebar.tsx           → useTheme
├── TorrentPage.tsx
│   ├── TorrentUpload.tsx     → usePreviewFlow
│   ├── SeasonMappingTable.tsx→ usePreviewData
│   ├── EpisodeMappingTable.tsx
│   ├── EpisodeEditSheet.tsx
│   ├── ConfirmBar.tsx
│   ├── ResultCard.tsx
│   ├── ErrorCard.tsx
│   └── ProcessingOverlay.tsx
├── RssPage.tsx
│   ├── RssSearchBar.tsx      → useRssSearch
│   ├── SubtitleGroupTable.tsx
│   ├── TagFilterPanel.tsx
│   ├── FeedPreview.tsx
│   ├── SubscriptionList.tsx  → useSubscriptions
│   │   └── SubscriptionCard.tsx
│   └── DownloadHistoryDialog.tsx → useDownloadHistory
└── SettingsPage.tsx
    ├── SettingsSidebar.tsx
    ├── GeneralConfigForm.tsx → useConfig
    ├── QbitConfigForm.tsx
    └── RssToolsPanel.tsx
        ├── DataManager.tsx
        ├── PollerController.tsx
        └── ExcludePatternManager.tsx
```

---

## 7. 生成工作流

### 7.1 标准生成流程

```
开发者输入需求
      │
      ▼
┌──────────────┐
│ 识别指令类型  │  ← 匹配 prompt 模板
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ 上下文注入    │  ← CLAUDE.md + 规范文档 + 参考组件 + 模块索引
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ AI 生成代码   │  ← 按模板 + 规范输出
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ 自动验证      │  ← tsc --noEmit + eslint
└──────┬───────┘
       │
   ┌───┴───┐
   │ PASS?  │
   └───┬───┘
   YES │  NO → 自动修复 → 重新验证 → 最多 3 次重试 → 报告失败
       │
       ▼
┌──────────────┐
│ 开发者 Review │  ← 人工确认 diff，运行 `npm run dev` 实际验证
└──────┬───────┘
       │
       ▼
   ✅ 合并
```

### 7.2 多人/AI 协作流程

```
开发者 A: "在设置页加一个日志级别下拉框"
    │                 │
    │    ┌────────────┴──────────────┐
    │    │  AI 生成 diff              │
    │    │  1. types/preview.ts +1     │
    │    │  2. RssToolsPanel.tsx +10   │
    │    │  3. config.py +2 (提示)     │
    │    └────────────┬──────────────┘
    │                 │
    ▼                 ▼
  开发者 Review → 调整 → ✅ 合并
```

### 7.3 失败处理

| 失败场景 | 处理方式 |
|---|---|
| TypeScript 编译错误 | AI 读取错误信息 → 修复 → 重新生成 |
| ESLint 警告 | AI 读取警告 → 自动修复（如 import 排序、未使用变量） |
| 样式不对 | 开发者提供截图描述 → AI 调整 Tailwind class |
| 逻辑错误 | 开发者 comment → AI 按反馈修改 |
| 3 次重试仍失败 | AI 输出失败报告 + 建议人工介入点 |

---

## 8. 质量保障

### 8.1 自动化检查清单

每次生成后，AI 必须自检：

```bash
# 类型检查
cd frontend && npx tsc --noEmit

# Lint 检查
cd frontend && npx eslint src/ --max-warnings 0

# 禁止项扫描 (grep 规则)
rg "style=\{\{" src/                    # 内联 style (排除百分比/渐变)
rg ": any" src/                         # any 类型
rg "#[0-9a-fA-F]{3,6}" src/components/  # 硬编码颜色
rg "text-\[#[0-9a-fA-F]" src/           # Tailwind 任意值颜色
```

### 8.2 人工 Review 检查点

| 检查项 | 关注点 |
|---|---|
| 视觉一致性 | 间距、字号、颜色与项目其他部分一致 |
| 状态完整性 | loading、empty、error 状态均可用且交互正确 |
| 交互逻辑 | 按钮禁用态、表单提交、导航跳转正确 |
| 响应式 | 移动端不崩，横向滚动不出界 |
| 文案 | 中文表述准确，无错别字 |

### 8.3 回归测试

生成新代码后，运行已有功能的冒烟测试：

- [ ] Torrent 上传 → 预览 → 确认 全流程不受影响
- [ ] RSS 搜索 → 订阅 → 历史 全流程不受影响
- [ ] 设置页所有配置项可正常读写
- [ ] 主题切换正常
- [ ] 侧边栏导航正常

---

## 9. 文件产出物清单

### 9.1 新建文件

```
docs/
├── conventions/
│   ├── README.md
│   ├── 01-project-structure.md
│   ├── 02-naming-conventions.md
│   ├── 03-component-patterns.md
│   ├── 04-styling-guide.md
│   ├── 05-api-integration.md
│   ├── 06-state-management.md
│   ├── 07-error-handling.md
│   ├── 08-typescript-guide.md
│   └── 09-accessibility.md
│
├── templates/
│   ├── page.template.tsx
│   ├── component.template.tsx
│   ├── hook.template.ts
│   ├── api-function.template.ts
│   ├── form-field.template.tsx
│   ├── table-page.template.tsx
│   └── dialog.template.tsx
│
├── prompts/
│   ├── add-page.md
│   ├── add-field.md
│   ├── add-api.md
│   ├── add-table-column.md
│   ├── add-component.md
│   ├── fix-error-states.md
│   └── refactor-component.md
│
└── MODULE_MAP.md
```

### 9.2 修改文件

```
CLAUDE.md                → 新增 "UI 生成指令" 段
frontend/.eslintrc.json  → 新增禁止 any/style 的规则 (如需要)
```

---

## 10. 验收标准

### 10.1 文档完整度

- [ ] 所有 9 个规范文件编写完成，每个 ≥ 50 行
- [ ] 所有 7 个模板文件编写完成，可直接复制使用
- [ ] 所有 7 个 prompt 文件编写完成，包含占位符变量
- [ ] MODULE_MAP.md 覆盖全部现有页面/组件
- [ ] CLAUDE.md 包含 UI 生成指令段

### 10.2 生成可用性

- [ ] 使用 `add-page` prompt 可生成一个完整的新页面（含 hook + 类型 + 路由）
- [ ] 生成的代码通过 `tsc --noEmit` 和 `eslint`
- [ ] 生成的代码包含 loading/empty/error/data 四种状态
- [ ] 生成的代码与现有手写代码无法从风格上区分

### 10.3 维护性

- [ ] 当项目规范更新时，规范文档可同步更新（不是一次性文档）
- [ ] 模板本身使用 TypeScript，类型完整
- [ ] prompt 模板有版本号和变更记录

---

## 附录 A: 与手动开发的对比

| 维度 | 手动开发 | AI 辅助生成 |
|---|---|---|
| 新页面（表格型） | 2-4 小时 | 5-15 分钟 (+ Review) |
| 新增表单字段 | 15-30 分钟 | 1-3 分钟 |
| 新增 API 函数 | 10-20 分钟 | 1-2 分钟 |
| 补齐状态 | 30-60 分钟 | 5-10 分钟 |
| 风格一致性 | 依赖开发者纪律 | 规范强制 + 自检 |
| 类型安全 | 依赖开发者意识 | 生成即检查 |
| 适用场景 | 所有 | 80% 的 CRUD 型 UI |

## 附录 B: 风险与缓解

| 风险 | 缓解措施 |
|---|---|
| AI 不理解业务逻辑 | 提供参考组件 + 详细功能描述 |
| 生成代码风格随时间漂移 | 规范文档持续更新 + 模板版本锁定 |
| 重度定制页面生成质量差 | 仅对 CRUD 型页面使用全自动生成，定制页面用半自动（AI 出骨架，人工调细节） |
| prompt 维护成本高 | 初始投入 1-2 天，之后每个新模块追加 10-20 分钟更新 prompt |
