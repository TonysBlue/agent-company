# Task 138 — 受控 Beta 真实客户验证董事长审批包

编制日期：2026-07-23
编制角色：Customer & Revenue
材料状态：`internal_draft_pending_chairman_decision`
证据类别：仅内部证据；不是客户验证结果、法律意见或执行授权

## 决策入口

- `chairman-approval-package.md`：董事长阅读及决策主文档。
- `decision-form.json`：供 CEO 在董事长决策后转录到正式审批账本的结构化草案；当前全部为 `pending`。
- `session-evidence-plan.json`：6 个拟议会话（最低完成门槛 5 次）的候选范围、场景和逐会话证据清单。
- `evidence-manifest.json`：本包与仓库内部依据的 SHA-256 固定清单。
- `verification-report.md`：验收项、边界和仓库测试的核验结果。

## 使用边界

本目录不含姓名、联系方式、客户资产、凭据、合同、支付资料或真实客户数据。编制本包没有执行外联、启用外部账号、处理客户数据、发布、定价、收费或作出法律承诺。

依据公司章程，只有 CEO 可以创建董事长 inbox/outbox 工作流文件；本目录不是正式董事长收件箱或正式决策记录。只有董事长真实作出的选择，经 CEO 写入公司 SQLite 审批/审计账本并归档后，才构成相应的公司决定。
