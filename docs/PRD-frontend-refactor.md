# PRD: My Anime Manager 前端 UI 重构

> **版本**: v1.0  
> **日期**: 2026-06-22  
> **状态**: Draft  
> **作者**: Thunini

---

## 目录

1. [背景与目标](#1-背景与目标)
2. [现状分析](#2-现状分析)
3. [功能清单（不变部分）](#3-功能清单不变部分)
4. [重构目标](#4-重构目标)
5. [信息架构与路由设计](#5-信息架构与路由设计)
6. [页面级设计](#6-页面级设计)
7. [组件拆分计划](#7-组件拆分计划)
8. [状态管理方案](#8-状态管理方案)
9. [UI/UX 改进清单](#9-uiux-改进清单)
10. [技术债务清理](#10-技术债务清理)
11. [实施阶段](#11-实施阶段)
12. [验收标准](#12-验收标准)

---

## 1. 背景与目标

### 1.1 背景

My Anime Manager 是一个面向 Jellyfin 媒体服务器的番剧元数据管理工具，前端负责提供 Torrent 预览/确认、RSS 订阅管理、系统配置等交互界面。

当前前端为 **单文件 App + 大组件** 架构，随着功能不断堆叠，以下问题日益突出：

- `App.tsx` 包含全部路由逻辑 + 内联 SVG 图标，职责不清
- `RssManager.tsx` (372 行) 和 `SettingsModal.tsx` (377 行) 为超大组件，难以维护
- `MappingOverviewCard.tsx` (428 行) 承担了季/集双视图 + 编辑 Sheet + 状态控制，耦合严重
- UI 组件（button/card/dialog 等）为自建 shadcn 风格，存在不完全实现和样式不一致
- 无路由系统，所有页面通平铺在 Tab 切换中，扩展性差
- TypeScript 类型定义存在重复（`ConfirmResponse` 定义两次）

### 1.2 目标

本次重构的 **核心目标** 是：

1. **提升可维护性** — 组件拆细、职责单一、目录结构清晰
2. **改善用户体验** — 加载骨架屏、空状态插画、更好的错误处理、响应式适配
3. **建立可扩展架构** — 引入路由、统一定义状态管理、规范化 API 层
4. **清理技术债务** — 重复类型、内联 SVG、魔数字符串、CSS 碎片

### 1.3 非目标

- **不改变后端 API** — 所有后端接口保持不变
- **不增加新功能** — 纯重构，功能范围与当前一致（在实施阶段可根据拆分结果微调 UX）
- **不更换技术栈** — 继续使用 React + TypeScript + Tailwind CSS

---

## 2. 现状分析

### 2.1 技术栈

| 层 | 现状 | 备注 |
|---|---|---|
| 框架 | React 19.2 + TypeScript 6.0 | ✅ 版本较新 |
| 构建 | Vite 8.0 | ✅ 快速 |
| 样式 | Tailwind CSS 4.3 | ✅ 使用 CSS 变量 + dark 模式 |
| UI 库 | 自建 10 个 shadcn 风格组件 | ⚠️ 维护成本高 |
| Toast | sonner 2.0 | ✅ 轻量 |
| HTTP | 原生 fetch | ✅ 无额外依赖 |
| 路由 | 无，基于 `activeTab` 状态切换 | ❌ 无 URL 映射、无前进后退 |

### 2.2 当前页面/视图结构

```
App
├── 侧边栏 (Sidebar)
│   ├── Logo: "My Anime Manager"
│   ├── 导航: Torrent 处理 | RSS 订阅
│   └── 底部: 主题切换 | 设置入口
│
├── Torrent 模式 (activeTab === 'torrent')
│   ├── idle/uploading  → TorrentUpload (拖拽上传)
│   ├── preview         → PreviewDashboard → MappingOverviewCard
│   │   ├── 季对应 Tab   → 季映射表格 (TMDB 季 ↔ Bangumi 条目)
│   │   └── 集对应 Tab   → 集映射表格 + EpisodeEditSheet (滑出编辑面板)
│   ├── confirming      → 加载动画
│   ├── done            → ProcessingResult (结果卡片)
│   └── error           → 错误卡片 + 重新上传按钮
│
├── RSS 模式 (activeTab === 'rss')
│   └── RssManager (单体大组件)
│       ├── 搜索区: Bangumi ID 输入 + 查询按钮
│       ├── 结果区: 字幕组表格 (主/备订阅、标签过滤、RSS 预览)
│       ├── 订阅列表: 卡片列表 (封面占位、元数据、操作按钮)
│       └── 下载历史 Dialog: 集列表 + qBittorrent 进度条
│
└── 设置弹窗 (SettingsModal)
    ├── 配置 Tab: TMDB API Key, UA, Proxy 等
    ├── qBittorrent Tab: 连接信息 + 连接测试
    └── RSS Tab: 映射数据、下载路径、轮询器开关/间隔、全局排除
```

### 2.3 组件规模（行数）

| 组件 | 行数 | 问题 |
|---|---|---|
| MappingOverviewCard | 428 | 包含双视图 + 编辑 Sheet + 状态 + 路径构建 |
| SettingsModal | 377 | 内含 Slider/Switch/NavItem 子组件 |
| RssManager | 372 | 搜索 + 订阅 + 历史 Dialog 全在一个文件 |
| EpisodeEditSheet | 306 | 可接受，但依赖父组件透传大量 props |
| App | 224 | 内联 5 个 SVG 图标组件 + 状态机条件渲染 |
| ConfirmAction | 21 | ✅ 合理 |
| ProcessingResult | 32 | ✅ 合理 |
| TorrentUpload | 76 | ✅ 合理 |

### 2.4 API 层现状

- `torrentApi.ts`: uploadPreview, confirmTorrent, getConfig, updateConfig (4 个函数)
- `rssApi.ts`: 17 个函数，覆盖 RSS 搜索/订阅/下载器/历史/qBittorrent 检测
- 所有请求经过 Vite proxy → FastAPI 后端
- 错误处理依赖 `res.json().catch(() => ...)` fallback 模式

### 2.5 类型系统

- `preview.ts` 包含全部 21 个 interface/type，236 行
- 存在重复定义: `ConfirmResponse` 定义了两次（第 86-93 行 和 第 95-102 行）
- `ExtraBlock` 在预览中使用但在 EpisodeEditSheet 中未覆盖

---

## 3. 功能清单（不变部分）

以下功能**必须保持完整**，仅重构实现方式：

### 3.1 Torrent 处理

| ID | 功能 | 说明 |
|---|---|---|
| T1 | 文件上传（拖拽/点击） | 仅接受 .torrent，显示文件名和大小 |
| T2 | 上传进度状态 | uploading 状态显示加载动画 |
| T3 | 季映射预览 | 表格展示: 文件数 → TMDB 季(下拉选择) → Bangumi 条目(下拉选择) |
| T4 | 集映射预览 | 表格展示: 文件名 → TMDB → Bangumi(含 HoverCard 详情) → 新路径 |
| T5 | 单集编辑 | Sheet 面板: TMDB 季/集选择、Bangumi 条目/集选择、实时路径预览 |
| T6 | 确认执行 | 底部粘性栏: 确认按钮 → confirming 状态 → 结果 |
| T7 | 处理结果 | 成功: NFO 数/图片数/重命名数; 失败: 错误信息 + 部分成功提示 |
| T8 | 错误处理与重试 | 上传/确认失败显示错误卡片，一键重新开始 |

### 3.2 RSS 订阅管理

| ID | 功能 | 说明 |
|---|---|---|
| R1 | Bangumi ID 搜索 | 输入 ID → 查询 Mikan 字幕组 RSS 列表 |
| R2 | 字幕组列表 | 表格: 展开/折叠、字幕组名、RSS URL、标签过滤、订阅操作 |
| R3 | RSS Feed 预览 | 展开显示 Feed 条目列表：标题、标签、大小、通过/排除状态 |
| R4 | 标签过滤 | 可选标签(简体/繁体/日语/内封/内嵌/双语) + 满足条件预览 |
| R5 | 主/备订阅 | 下拉菜单: 作为主 RSS / 作为备用 RSS |
| R6 | 订阅列表 | 卡片列表: 封面占位、Bangumi ID、季/集范围、标签、启用/已完成状态 |
| R7 | 取消/恢复订阅 | 启用中 → 取消; 已完成 → 恢复 |
| R8 | 下载历史 | Dialog: 已下载/总数统计、每集来源(主/备)、qBittorrent 状态+进度条、自动刷新(5s) |

### 3.3 系统设置

| ID | 功能 | 说明 |
|---|---|---|
| S1 | API/代理配置 | TMDB Key, Bangumi UA, API 延迟, 代理地址/端口, Watch Dir, Mikan URL |
| S2 | qBittorrent 配置 | URL, 用户名, 密码, 保存路径 + 连接测试 |
| S3 | RSS 数据管理 | 映射数据下载/刷新、下载路径 |
| S4 | RSS 轮询器 | 开关、间隔滑块(0-1440min)、状态显示 |
| S5 | 全局排除模式 | 添加/删除排除关键词 |
| S6 | 配置保存 | 保存按钮(有脏数据时高亮)、保存成功/失败反馈 |
| S7 | 主题切换 | Light/Dark 切换，持久化 |

### 3.4 全局

| ID | 功能 | 说明 |
|---|---|---|
| G1 | 深色/浅色主题 | 侧边栏底部切换，持久化到 localStorage |
| G2 | Toast 通知 | 操作反馈（成功/失败） |
| G3 | 响应式布局 | 侧边栏可折叠，移动端可用 |

---

## 4. 重构目标

### 4.1 架构目标

| 目标 | 指标 | 优先级 |
|---|---|---|
| 组件平均行数 < 150 行 | 可维护性 | P0 |
| 引入 React Router，实现 URL 驱动的页面切换 | 可扩展性 | P0 |
| UI 组件库选型确定（继续自建 or 迁移到 Radix/shadcn） | 一致性 | P0 |
| API 层统一错误处理 + 请求状态管理 | 健壮性 | P1 |
| 全局状态管理（Zustand or Context）替代 prop drilling | 数据流清晰 | P1 |
| 骨架屏 + 空状态 + 错误边界 | 用户体验 | P1 |
| TypeScript 类型无重复、无 `any` | 类型安全 | P2 |

### 4.2 性能目标

- 首屏加载: < 2s (本地)
- 组件重渲染次数: 减少 30%+（通过合理拆分 + memo）
- 无不必要的全局状态订阅导致的全树渲染

---

## 5. 信息架构与路由设计

### 5.1 路由表

```
/                        → 重定向到 /torrent
/torrent                 → Torrent 处理页 (上传 + 预览 + 结果)
/torrent/:id?            → (可选) 历史记录详情
/rss                     → RSS 订阅管理页
/rss/history/:bangumiId  → (可选) 下载历史独立页
/settings                → 设置页 (独立路由，不再弹窗)
```

### 5.2 路由实现

```typescript
// 使用 react-router-dom v7
const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,   // Sidebar + Outlet
    children: [
      { index: true, element: <Navigate to="/torrent" /> },
      { path: 'torrent', element: <TorrentPage /> },
      { path: 'rss', element: <RssPage /> },
      { path: 'settings', element: <SettingsPage /> },
    ],
  },
]);
```

### 5.3 导航结构

```
AppLayout
├── Sidebar (固定)
│   ├── Logo → /
│   ├── NavItem: Torrent 处理 → /torrent
│   ├── NavItem: RSS 订阅   → /rss
│   ├── NavItem: 设置        → /settings
│   └── Footer: 主题切换
└── <Outlet /> (页面内容)
```

### 5.4 设置页说明

当前 Settings 为 Modal，在重构中改为**独立页面**，理由：

- 设置项较多（17+ 字段），弹窗空间局促
- 有三个子分类（配置/qBittorrent/RSS），需要子导航
- 独立页面可在移动端获得更好的体验
- 设置页内部保持 Tab 切换（配置/qBittorrent/RSS）

如果团队偏好保留弹窗形式，可用 URL search param `/settings?modal=1` 实现回退兼容。

---

## 6. 页面级设计

### 6.1 Torrent 处理页 `/torrent`

```
TorrentPage
├── 状态: idle / uploading / preview / confirming / done / error
│
├── idle/uploading
│   └── TorrentUpload (拖拽区 + 文件信息 + 上传按钮)
│
├── preview
│   ├── PreviewToolbar (剧名 + 状态标签 + 操作按钮)
│   ├── SeasonMappingTable (季对应表格)
│   │   └── 每行: 文件数 | → | TMDB 季 Select | → | Bangumi 条目 Select
│   ├── EpisodeMappingTable (集对应表格) [Tab 切换]
│   │   └── 每行: 文件名 | → | TMDB | → | Bangumi(HoverCard) | 路径 | 编辑按钮
│   ├── EpisodeEditDrawer (编辑面板, 从右侧滑出)
│   └── ConfirmBar (底部粘性操作栏)
│
├── confirming
│   └── ProcessingOverlay (加载动画 + 进度提示)
│
├── done
│   └── ResultCard (成功/失败统计)
│
└── error
    └── ErrorCard (错误信息 + 重试/重新开始)
```

### 6.2 RSS 订阅管理页 `/rss`

```
RssPage
├── RssSearchBar (Bangumi ID 输入 + 搜索)
│
├── RssSearchResult (搜索结果区, 可折叠)
│   └── SubtitleGroupTable
│       ├── 展开按钮 | 字幕组名 | RSS URL | 标签过滤 | 订阅操作
│       ├── [展开] TagFilterPanel (标签选择器)
│       └── [展开] FeedPreview (RSS 条目列表)
│
├── SubscriptionList (我的订阅)
│   └── SubscriptionCard × N
│       ├── 封面占位 (渐变色 + 首字母)
│       ├── 标题 + 元数据标签行
│       ├── 过滤标签 + 备用标签
│       └── 操作区: 历史 / 取消(恢复)
│
└── DownloadHistoryDialog (下载历史弹窗)
    ├── 统计栏 (已下载/总数、主/备源数、缺失集)
    └── EpisodeTable (集号|来源|状态|进度条|种子名)
```

### 6.3 设置页 `/settings`

```
SettingsPage
├── SettingsSidebar (页面内子导航)
│   ├── 通用配置
│   ├── qBittorrent
│   └── RSS 工具
│
├── 通用配置 Panel
│   └── ConfigForm (TMDB Key, UA, Proxy 等字段)
│
├── qBittorrent Panel
│   ├── ConfigForm (URL, 用户名, 密码, 保存路径)
│   └── ConnectionChecker (连接检测 + 状态展示)
│
└── RSS 工具 Panel
    ├── DataManager (映射数据下载/刷新)
    ├── DownloadPathInput
    ├── PollerController (开关 + 间隔滑块 + 状态 + 错误日志)
    └── ExcludePatternManager (添加/删除排除关键词)
```

---

## 7. 组件拆分计划

### 7.1 组件树（目标状态）

```
components/
├── ui/                        # 基础 UI 组件（现有 + 新增）
│   ├── button.tsx             # 保留，Review 完整性
│   ├── card.tsx               # 保留
│   ├── input.tsx              # 保留
│   ├── tabs.tsx               # 保留
│   ├── table.tsx              # 保留
│   ├── dialog.tsx             # 保留
│   ├── dropdown-menu.tsx      # 保留（可选迁移到 Radix）
│   ├── sidebar.tsx            # 保留
│   ├── sheet.tsx              # 保留
│   ├── hover-card.tsx         # 保留
│   ├── select.tsx             # [新增] 封装原生 select + 样式
│   ├── skeleton.tsx           # [新增] 骨架屏组件
│   ├── badge.tsx              # [新增] 标签徽章
│   ├── progress.tsx           # [新增] 进度条
│   ├── switch.tsx             # [新增] 开关（从 SettingsModal 提取）
│   ├── slider.tsx             # [新增] 滑块（从 SettingsModal 提取）
│   └── empty-state.tsx        # [新增] 空状态插画
│
├── layout/                    # 布局组件
│   ├── AppLayout.tsx          # Sidebar + Outlet
│   ├── Sidebar.tsx            # 侧边栏导航（从 App 提取）
│   └── PageHeader.tsx         # 页面标题栏
│
├── torrent/                   # Torrent 功能模块
│   ├── TorrentUpload.tsx      # 文件上传区 (≈不变)
│   ├── PreviewToolbar.tsx     # 预览页工具栏
│   ├── SeasonMappingTable.tsx # 季映射表格 (从 MappingOverviewCard 提取)
│   ├── EpisodeMappingTable.tsx# 集映射表格 (从 MappingOverviewCard 提取)
│   ├── EpisodeEditSheet.tsx   # 集编辑面板 (≈不变，减少 props)
│   ├── ConfirmBar.tsx         # 确认操作栏 (≈不变)
│   ├── ResultCard.tsx         # 处理结果卡片 (≈不变)
│   ├── ErrorCard.tsx          # 错误状态卡片 (从 App 内联提取)
│   └── ProcessingOverlay.tsx  # 处理中遮罩 (从 App 内联提取)
│
├── rss/                       # RSS 功能模块
│   ├── RssSearchBar.tsx       # 搜索栏 (从 RssManager 提取)
│   ├── SubtitleGroupTable.tsx # 字幕组列表 (从 RssManager 提取)
│   ├── TagFilterPanel.tsx     # 标签过滤面板 (从 RssManager 提取)
│   ├── FeedPreview.tsx        # RSS Feed 预览 (从 RssManager 提取)
│   ├── SubscriptionList.tsx   # 订阅列表容器 (从 RssManager 提取)
│   ├── SubscriptionCard.tsx   # 单个订阅卡片 (从 RssManager 提取)
│   ├── DownloadHistoryDialog.tsx # 下载历史弹窗 (从 RssManager 提取)
│   └── EpisodeStatusTable.tsx # 集状态表格 (从 DownloadHistoryDialog 提取)
│
├── settings/                  # 设置功能模块
│   ├── SettingsSidebar.tsx    # 设置子导航 (从 SettingsModal 提取)
│   ├── GeneralConfigForm.tsx  # 通用配置表单
│   ├── QbitConfigForm.tsx     # qBittorrent 配置 + 连接检测
│   ├── RssDataManager.tsx     # RSS 数据管理
│   ├── PollerController.tsx   # 轮询器控制 (开关 + 滑块 + 状态)
│   └── ExcludePatternManager.tsx # 排除模式管理
│
├── shared/                    # 跨模块共享组件
│   ├── FieldGroup.tsx         # 配置字段组 (label + input + hint + dirty 标记)
│   └── ConnectionChecker.tsx  # 连接检测按钮组
│
└── icons/                     # SVG 图标组件
    ├── index.ts               # 统一导出
    ├── UploadIcon.tsx
    ├── RssIcon.tsx
    ├── SunIcon.tsx
    ├── MoonIcon.tsx
    ├── SettingsIcon.tsx
    ├── SearchIcon.tsx
    ├── EditIcon.tsx
    ├── ChevronDownIcon.tsx
    └── CloseIcon.tsx
```

### 7.2 拆分原则

1. **每个文件只导出一个组件**（除 icons/index.ts 外）
2. **组件行数目标: < 150 行**（复杂表格容许 ~200 行）
3. **业务逻辑在 hooks 中，组件只负责渲染**
4. **Props 数量: ≤ 5 个**（超过则考虑合并为 options object 或使用 context）

---

## 8. 状态管理方案

### 8.1 选择: React Context + useReducer（轻量场景）

在以下范围内使用 Context 避免 prop drilling：

| Context | 范围 | 内容 |
|---|---|---|
| `ThemeContext` | 全局 | theme, toggleTheme |
| `TorrentFlowContext` | /torrent 页面 | state, previewData, confirmResult, error, 操作方法 |
| `SettingsContext` | /settings 页面 | config, dirty, save, reset |

### 8.2 备选：Zustand

如果 Context 导致不必要的重渲染，可迁移到 Zustand：

```typescript
// stores/torrentStore.ts
interface TorrentStore {
  state: AppState;
  previewData: TorrentPreviewResponse | null;
  confirmResult: ConfirmResponse | null;
  error: string | null;
  uploadTorrent: (file: File) => Promise<void>;
  confirmTorrent: () => Promise<void>;
  reset: () => void;
}
```

### 8.3 远程数据请求

- 统一使用自定义 hook 封装 API 调用
- 每个 hook 返回 `{ data, loading, error, refetch }`
- Loading 状态驱动骨架屏渲染
- Error 状态驱动错误边界展示

```typescript
// hooks/useRssSearch.ts
function useRssSearch() {
  const [data, setData] = useState<BangumiRssResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const search = useCallback(async (bangumiId: number) => {
    setLoading(true);
    setError(null);
    try {
      const result = await rssApi.lookupBangumiRss(bangumiId);
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Search failed');
    } finally {
      setLoading(false);
    }
  }, []);

  return { data, loading, error, search };
}
```

---

## 9. UI/UX 改进清单

### 9.1 加载状态

| 场景 | 现状 | 目标 |
|---|---|---|
| Torrent 上传分析中 | 居中 spinner + 文字 | 骨架屏: 预览表格形状占位 |
| RSS Feed 加载 | 小 spinner 按钮 | 骨架屏: Feed 条目占位行 × 3 |
| 订阅列表加载 | "加载中..." 文字 | 骨架屏: 卡片占位 × 2 |
| 下载历史加载 | Dialog 内 spinner | 骨架屏: 表格占位 |
| 设置页加载 | Modal 内 spinner | 骨架屏: 表单字段占位 |
| 连接检测 | 无状态指示 | 按钮内 loading spinner + 结果反馈 |

### 9.2 空状态

| 场景 | 现状 | 目标 |
|---|---|---|
| 无字幕组结果 | "未找到字幕组" 文字 | 插画 + "该番剧暂无字幕组 RSS" + 建议提示 |
| 无订阅 | "暂无订阅" 文字 | 插画 + "添加你的第一个 RSS 订阅" + 跳转搜索 |
| RSS Feed 为空 | "暂无条目" 文字 | "该 RSS 源暂无更新" |
| 无历史记录 | 空表格 | "尚未下载任何剧集" + 进度提示 |

### 9.3 错误处理

| 场景 | 现状 | 目标 |
|---|---|---|
| 网络错误 | toast + 红框文字 | 错误卡片（含重试按钮）+ toast |
| 配置保存失败 | 红框文字 | 字段级错误提示 + toast |
| RSS Feed 获取失败 | "获取失败" | "Feed 获取失败，点击重试" 按钮 |
| qBittorrent 连接失败 | 红色文字 | 诊断建议（检查 URL/网络/认证） |
| 搜索失败 | 红框文字 | 输入框下方内联错误 + 建议（检查 ID 格式） |

### 9.4 响应式改进

| 断点 | 目标 |
|---|---|
| ≥ 1024px | 完整三栏/两栏布局 |
| 768-1023px | 侧边栏折叠为图标模式，表格隐藏次要列 |
| < 768px | 侧边栏变为底部 Tab Bar / 汉堡菜单，表格转为卡片列表 |

### 9.5 交互细节

| 改进项 | 说明 |
|---|---|
| 设置页脏数据提示 | 修改字段高亮 + 离开页面前确认（`beforeunload` + 路由守卫） |
| 查询按钮防抖 | Bangumi ID 输入后 Enter 直接查询（已支持），额外增加输入校验 |
| 标签选择器优化 | 点击整个标签区域即可选中，无需精确定位 checkbox |
| 下载历史自动刷新 | 已支持（5s），增加手动刷新按钮 + 最后刷新时间显示 |
| 路径复制 | 新路径列增加一键复制按钮 |

### 9.6 无障碍 (a11y)

| 要求 | 范围 |
|---|---|
| 键盘导航 | 所有交互元素可通过 Tab/Enter/Escape 操作 |
| Focus 可见 | 使用 `focus-visible:ring` 样式 |
| aria-label | 图标按钮添加描述性 label |
| 语义化 HTML | 使用 `<nav>`, `<main>`, `<section>`, `<table>` 等 |

---

## 10. 技术债务清理

### 10.1 立即修复

| 项 | 文件 | 操作 |
|---|---|---|
| 重复的 `ConfirmResponse` 定义 | `types/preview.ts` L86-102 | 删除重复，保留一份 |
| 内联 SVG 图标 | `App.tsx` L28-74 | 提取到 `components/icons/` |
| `SidebarNavItem` 定义在 SettingsModal | `SettingsModal.tsx` L74-91 | 提取到 `components/ui/` 或 settings 内部 |
| `Slider` 定义在 SettingsModal | `SettingsModal.tsx` L32-49 | 提取到 `components/ui/slider.tsx` |
| `Switch` 定义在 SettingsModal | `SettingsModal.tsx` L53-72 | 提取到 `components/ui/switch.tsx` |

### 10.2 代码质量

| 项 | 操作 |
|---|---|
| `any` 类型使用 | 替换为具体类型（如 `RssManager` 中的 `catch { /* */ }` 空处理） |
| 魔数字符串 | 抽取为常量 (e.g. `EXTRA_KEY_BASE = 900`, 状态值) |
| CSS 内联渐变 | 订阅卡片封面渐变色抽为 CSS 变量或工具函数 |
| API 层错误处理 | 创建 `api/client.ts` 统一 fetch 封装（超时、重试、统一错误格式） |
| Tailwind class 合并 | 确认所有组件使用 `cn()` 工具函数 |

### 10.3 目录重组

```
frontend/src/
├── api/               # API 调用层 (保持不变)
│   ├── client.ts      # [新增] 统一 fetch 封装
│   ├── torrentApi.ts
│   └── rssApi.ts
├── components/        # 组件 (按功能模块组织)
│   ├── ui/            # 基础 UI 组件
│   ├── icons/         # [新增] SVG 图标
│   ├── layout/        # [新增] 布局组件
│   ├── torrent/       # [新增] Torrent 功能
│   ├── rss/           # [新增] RSS 功能
│   ├── settings/      # [新增] 设置功能
│   └── shared/        # [新增] 共享组件
├── hooks/             # 自定义 hooks
│   ├── usePreviewFlow.ts  (保留)
│   ├── useTheme.ts        (保留)
│   ├── useRssSearch.ts    # [新增]
│   ├── useSubscriptions.ts# [新增]
│   ├── useConfig.ts       # [新增]
│   └── useDownloadHistory.ts # [新增]
├── pages/             # [新增] 页面级组件
│   ├── TorrentPage.tsx
│   ├── RssPage.tsx
│   └── SettingsPage.tsx
├── stores/            # [新增] 状态管理 (Context or Zustand)
│   └── ...
├── types/             # 类型定义
│   └── preview.ts     (清理重复)
├── lib/               # 工具函数
│   ├── utils.ts
│   └── toast.ts
├── App.tsx            # 入口: RouterProvider
├── App.css            # 全局样式 + CSS 变量
└── main.tsx           # 渲染入口
```

---

## 11. 实施阶段

### 阶段 1: 基础设施 (预计 1-2 天)

- [ ] 安装 react-router-dom
- [ ] 创建 `api/client.ts` 统一 fetch 封装
- [ ] 提取所有 SVG 图标到 `components/icons/`
- [ ] 提取 `Slider`, `Switch` 到 `components/ui/`
- [ ] 清理 `types/preview.ts` 中的重复定义
- [ ] 建立 `pages/` 和 `stores/` 目录结构
- [ ] 配置路由 + AppLayout 壳

### 阶段 2: Torrent 模块重构 (预计 2 天)

- [ ] 拆分 `MappingOverviewCard` → `SeasonMappingTable` + `EpisodeMappingTable`
- [ ] 提取 `ErrorCard`, `ProcessingOverlay` 为独立组件
- [ ] 创建 `TorrentFlowContext` 或 `useTorrentFlow` hook
- [ ] 组装 `TorrentPage`
- [ ] **回归测试**: 上传 → 预览 → 编辑 → 确认 → 结果全流程

### 阶段 3: RSS 模块重构 (预计 2 天)

- [ ] 拆分 `RssManager` → 7 个子组件
- [ ] 创建 `useRssSearch`, `useSubscriptions`, `useDownloadHistory` hooks
- [ ] 添加骨架屏 + 空状态 + 错误状态
- [ ] 组装 `RssPage`
- [ ] **回归测试**: 搜索 → 订阅 → Feed 预览 → 历史查看全流程

### 阶段 4: 设置模块重构 (预计 1 天)

- [ ] 拆分 `SettingsModal` → 5 个子组件
- [ ] 创建 `useConfig` hook
- [ ] 改为独立页面 `/settings`
- [ ] 添加离开确认（脏数据保护）
- [ ] **回归测试**: 配置读写、连接检测、轮询器控制、排除模式

### 阶段 5: 收尾 (预计 1 天)

- [ ] 响应式适配完善
- [ ] 无障碍 (a11y) review
- [ ] 整体 UI 一致性检查（间距、字号、颜色）
- [ ] TypeScript strict mode 通过
- [ ] 端到端手动测试

---

## 12. 验收标准

### 12.1 功能完整性

- [ ] Torrent 上传 → 预览 → 编辑 → 确认 → 结果 流程无回归
- [ ] RSS 搜索 → 订阅 → 标签过滤 → Feed 预览 → 历史查看 流程无回归
- [ ] 设置页所有配置项可正常读写，脏数据检测正常
- [ ] 主题切换正常工作
- [ ] 所有 toast 通知正常弹出

### 12.2 代码质量

- [ ] 无 > 250 行的组件文件
- [ ] 无 `any` 类型（除必要的外部 API 响应）
- [ ] TypeScript 编译零错误
- [ ] ESLint 零警告
- [ ] 目录结构符合第 10.3 节规划

### 12.3 UI/UX

- [ ] 所有加载状态有骨架屏或 spinner（不可为空白）
- [ ] 所有空状态有友好提示（不可为空白或纯文字）
- [ ] 所有错误状态有重试入口
- [ ] 移动端 (< 768px) 布局可用
- [ ] 键盘可完成 Torrent 上传全流程
- [ ] 设置页离开时如有未保存更改，弹出确认

### 12.4 附录: 技术选型对比

| 维度 | 现状 (自建) | 方案 A: 装 Radix UI | 方案 B: 装 shadcn/ui CLI |
|---|---|---|---|
| 学习成本 | — | 低 (与现有 code 相似) | 中 (需了解 CLI 工作流) |
| 可访问性 | 手动 | Radix 内置 80%+ | Radix 内置 + shadcn 扩展 |
| 包大小 | 0 | +~5KB per component | +~5KB per component |
| 一致性 | ⚠️ 依赖人工 | ✅ | ✅ |
| 建议 | — | **推荐** (与现有风格兼容最好) | 可选 (偏好 CLI 管理) |

> **推荐方案 A**: 逐步将 `ui/` 组件迁移到 Radix UI primitives，保留 Tailwind 样式层。迁移优先级: Select > Dialog > Switch > Slider > DropdownMenu。
