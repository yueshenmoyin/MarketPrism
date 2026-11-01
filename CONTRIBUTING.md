# 参与 MarketPrism

欢迎提交问题与改进。请先阅读 README 中的运行架构和当前边界。

## 报告问题

请提供操作系统、Python / Node.js 版本、复现步骤、预期行为和实际行为。模型问题可提供供应商与模型名，数据问题可提供股票代码、分析日期和数据源。日志只保留复现所需片段，删除密钥、令牌、密码、私人网关及个人信息。

## 提交代码

1. 从自己的分支开始，保持修改范围集中。
2. 沿用现有 Python、TypeScript 和 Rust 模块结构。
3. 行为修复尽可能增加可重复的回归测试；测试使用模拟响应和虚构凭据，不依赖个人真实账号。
4. 运行相关测试及构建，在 PR 中说明变更、验证结果和已知限制。
5. 对外文档使用 MarketPrism；修改 `aitrade` 内部标识、环境变量或数据目录前请说明迁移方案。

```text
python -m pip install -e .
python -m pip install pytest httpx
python -m pytest tests -q
npm ci
npm run test:frontend
npm run build
cargo test --manifest-path src-tauri/Cargo.toml
```

请勿提交 `.env`、本地部署配置、桌面配置、账号数据库、研究日志、缓存或安装包。提交前检查 `git status` 与暂存差异。遵守仓库现有许可证，保留第三方来源与版权说明。
