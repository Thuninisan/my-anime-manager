# Frontend

React 19 + TypeScript + Vite + Tailwind CSS v4 前端。页面入口在 `src/pages/`，业务组件在 `src/components/`，API 调用在 `src/api/`。

```bash
npm ci
npm run dev
npm run build
```

开发服务器请求由 `vite.config.ts` 代理到 FastAPI；后端从 `frontend/dist/` 提供构建后的网页。完整的安装与部署说明见项目根目录的 `README.md`。
