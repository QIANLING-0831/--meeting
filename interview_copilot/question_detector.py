from __future__ import annotations

import re

from .models import QuestionCandidate


QUESTION_MARKERS = (
    "吗", "呢", "为什么", "怎么", "如何", "什么", "哪些", "多少", "是否",
    "能不能", "有没有", "请介绍", "介绍一下", "讲一下", "谈谈", "说说",
    "说一下", "可以说一下", "能说一下", "可以讲一下",
    "区别", "原理", "流程", "设计", "实现", "解决", "遇到", "负责",
)


class QuestionDetector:
    """Small deterministic detector; callers decide how to handle low confidence."""

    def detect(self, text: str) -> QuestionCandidate | None:
        normalized = re.sub(r"\s+", " ", text).strip()
        if "大模型" in normalized:
            normalized = re.sub(r"(?<![A-Za-z])L(?=[，,。.!！?？\s]|$)", "LLM", normalized)
        if len(normalized) < 2:
            return None
        incomplete_prompt = normalized.rstrip("，,。.!！?？；;：:").endswith(
            ("说一下", "讲一下", "介绍一下", "谈一下")
        )
        if incomplete_prompt:
            return None

        marker_count = sum(marker in normalized for marker in QUESTION_MARKERS)
        has_question_punctuation = normalized.endswith(("?", "？"))
        imperative_question = normalized.startswith(("请", "你", "能", "如果", "假设"))

        confidence = 0.2
        confidence += min(0.5, marker_count * 0.18)
        confidence += 0.25 if has_question_punctuation else 0.0
        confidence += 0.1 if imperative_question else 0.0
        confidence = min(confidence, 1.0)
        if confidence < 0.48:
            return None
        return QuestionCandidate(normalized, confidence, self.classify(normalized))

    def classify(self, text: str) -> str:
        if any(word in text for word in ("报错", "故障", "排查", "定位", "失败", "线上问题")):
            return "debugging"
        if any(word in text for word in ("系统设计", "架构", "设计一个", "高并发", "扩展性")):
            return "system_design"
        if any(word in text for word in ("算法", "复杂度", "代码", "手写", "数据结构")):
            return "coding"
        if any(word in text for word in ("项目", "负责", "难点", "指标", "上线")):
            return "project"
        if any(word in text for word in ("冲突", "压力", "失败经历", "优缺点", "离职")):
            return "behavioral"
        if len(text) <= 14 and text.endswith(("呢", "？", "?")):
            return "follow_up"
        return "concept"
