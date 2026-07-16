from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "docs" / "pixweave-self-serve-subscription-validation-v1.md"


class Task131SubscriptionStrategyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = STRATEGY.read_text(encoding="utf-8")
        cls.lower = cls.text.lower()

    def test_document_is_versioned_internal_and_non_commercial(self) -> None:
        self.assertIn("`pixweave-self-serve-subscription-validation/v1.0`", self.text)
        self.assertIn("所有者：Customer & Revenue", self.text)
        self.assertIn("任务：Agent Company 账本任务 131", self.text)
        self.assertIn("`commercial_status: not_offered`", self.text)
        self.assertIn("`external_action_authorized: false`", self.text)
        self.assertIn("本方案不引用外部市场事实", self.text)
        self.assertIn("内部演练结果不得改写成客户需求、付费意愿或收入证据", self.text)

        for prohibited in (
            "外联",
            "公开发布",
            "真实定价上线",
            "收费或支付",
            "合同或法律承诺",
            "真实客户数据处理或导出",
        ):
            self.assertIn(prohibited, self.text)

    def test_three_segments_have_scenarios_and_common_and_distinct_needs(self) -> None:
        for segment in ("SEG-I 个人", "SEG-S 小公司", "SEG-E 企业"):
            self.assertIn(segment, self.text)
        self.assertIn("### 3.1 共同需求假设", self.text)
        self.assertIn("### 3.2 差异需求假设", self.text)
        for need in ("N-C1", "N-C2", "N-C3", "N-C4", "N-C5"):
            self.assertIn(need, self.text)
        for dimension in ("协作 `[H-D1]`", "治理 `[H-D2]`", "用量 `[H-D3]`", "上手 `[H-D4]`", "支持 `[H-D5]`", "采购 `[H-D6]`"):
            self.assertIn(dimension, self.text)

    def test_value_proposition_and_path_do_not_depend_on_sales_negotiation(self) -> None:
        self.assertIn("## 4. 标准价值主张", self.text)
        self.assertIn("不依赖逐客户销售谈判", self.text)
        self.assertIn("不得以逐客报价、私下折扣、定制功能、定制 SLA、人工代运营或临时合同条款换取转化", self.text)
        for stage in ("F0 发现", "F1 资格自检", "F2 价值预览", "F3 内部评估", "F4 激活", "F5 选包", "F6 商业步骤", "F7 上手与留存"):
            self.assertIn(stage, self.text)
        self.assertIn("所有转化数值初始为 `unknown`", self.text)
        self.assertIn("不得创建销售线索、索取联系方式、安排商务跟进或协商例外", self.text)

    def test_packages_and_billing_are_non_price_hypotheses(self) -> None:
        for package in ("P-I Individual", "P-T Team", "P-O Organization"):
            self.assertIn(package, self.text)
        for billing_id in ("B1", "B2", "B3", "B4", "B5", "B6"):
            self.assertRegex(self.text, rf"\| {billing_id} \|")
        self.assertIn("以下仅是无金额的内部包装假设", self.text)
        self.assertIn("人工支持、定制开发、谈判折扣和合同例外不进入组合", self.text)

        package_section = self.text.split("## 6. 非承诺套餐假设", 1)[1].split("## 7. 计费维度假设比较", 1)[0]
        self.assertNotRegex(package_section, re.compile(r"(?:CNY|RMB|USD|[¥$€£])\s*\d", re.IGNORECASE))
        self.assertNotIn("联系销售", package_section.split("明确排除", 1)[0])

    def test_auditable_matrix_separates_product_and_demand_evidence(self) -> None:
        self.assertIn("## 8. 可审计比较矩阵", self.text)
        for matrix_id in ("M-I1", "M-S1", "M-E1", "M-C1", "M-F1", "M-B1"):
            self.assertRegex(self.text, rf"\| {matrix_id} \|")
        self.assertIn("`需求证据` 在没有真实验证时必须为 `none`", self.text)
        self.assertIn("不得写“已验证市场”", self.text)
        self.assertIn("不得用单一层级结果外推全部层级", self.text)
        for source in ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"):
            self.assertRegex(self.text, rf"\| {source} ")

    def test_internal_validation_has_thresholds_missing_value_rules_and_audit_fields(self) -> None:
        for experiment in ("IV-1", "IV-2", "IV-3", "IV-4", "IV-5", "IV-6"):
            self.assertRegex(self.text, rf"\| {experiment} ")
        self.assertIn("样本不足时状态为 `insufficient_internal_evidence`", self.text)
        self.assertIn("分母、排除理由和缺失项必须同时报告", self.text)
        for field in (
            "`scheme_version`",
            "`experiment_id`",
            "`matrix_ids`",
            "`synthetic_persona_id`",
            "`internal_reviewer_id`",
            "`source_ids`",
            "`missing_values`",
            "`falsifiers_observed`",
            "`artifact_sha256`",
        ):
            self.assertIn(field, self.text)
        self.assertIn("证据文件只证明内部任务执行和验证结果，不改变公司 SQLite 账本的任务状态", self.text)


if __name__ == "__main__":
    unittest.main()
