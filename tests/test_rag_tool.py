"""RAG 知识库检索工具测试: 纯排序逻辑 + 服务降级, 不依赖真实 Milvus/Ollama。"""

import pytest

from ai_agents.tools import rag as rag_tool
from ai_agents.tools.rag import format_hits, rank_hits, search_knowledge_base


def _hit(score: float, text: str, source: str = "doc.md", product: str = "产品", section: str = "章节") -> dict:
    return {
        "distance": score,
        "entity": {"text": text, "source": source, "product": product, "section": section},
    }


def test_rank_filters_below_threshold():
    hits = [_hit(0.80, "相关内容一"), _hit(0.30, "无关噪声")]

    result = rank_hits(hits, score_threshold=0.45, top_k=5, per_source_cap=2)

    assert len(result) == 1
    assert result[0]["entity"]["text"] == "相关内容一"


def test_rank_deduplicates_same_text_keeps_higher_score():
    # 23 份手册章节重复: 同一正文出现在不同文档, 只保留分数最高的一条
    shared = "请勿将金属物导体与电池两极对接，以免短路。"
    hits = [_hit(0.64, shared, source="W515X.md"), _hit(0.70, shared, source="W585X.md")]

    result = rank_hits(hits, score_threshold=0.45, top_k=5, per_source_cap=2)

    assert len(result) == 1
    assert result[0]["distance"] == 0.70
    assert result[0]["entity"]["source"] == "W585X.md"


def test_rank_dedup_ignores_whitespace_and_punctuation_differences():
    hits = [
        _hit(0.60, "快捷键 功能介绍：F1 帮助", source="a.md"),
        _hit(0.55, "快捷键功能介绍:F1帮助", source="b.md"),
    ]

    result = rank_hits(hits, score_threshold=0.45, top_k=5, per_source_cap=2)

    assert len(result) == 1
    assert result[0]["entity"]["source"] == "a.md"


def test_rank_caps_results_per_source_document():
    # 同一文档召回 4 条不同内容, 每文档最多保留 2 条 (来源多样性)
    hits = [_hit(0.70, f"电池章节不同内容第{i}段", source="g540.md") for i in range(4)]
    hits += [_hit(0.66, "员工手册中的离职规定", source="员工手册.md")]

    result = rank_hits(hits, score_threshold=0.45, top_k=5, per_source_cap=2)

    assert len(result) == 3
    assert sum(1 for h in result if h["entity"]["source"] == "g540.md") == 2
    assert result[-1]["entity"]["source"] == "员工手册.md"


def test_rank_limits_to_top_k():
    hits = [_hit(0.50 + i * 0.01, f"不同内容片段编号{i}", source=f"doc{i}.md") for i in range(8)]

    result = rank_hits(hits, score_threshold=0.45, top_k=5, per_source_cap=2)

    assert len(result) == 5
    assert result[0]["distance"] > result[-1]["distance"]


def test_format_hits_includes_source_section_and_score():
    output = format_hits([_hit(0.65, "电池不要过热。", product="华为擎云 G540", section="4.9 电池安全")])

    assert "华为擎云 G540" in output
    assert "4.9 电池安全" in output
    assert "电池不要过热。" in output
    assert "0.65" in output


def test_tool_returns_graceful_message_when_service_unavailable(monkeypatch):
    monkeypatch.setattr(rag_tool, "retrieve_hits", lambda query: None)

    output = search_knowledge_base.invoke({"query": "电池怎么保养"})

    assert "知识库暂时不可用" in output


def test_tool_rejects_empty_query():
    output = search_knowledge_base.invoke({"query": "   "})

    assert "不能为空" in output


def test_tool_returns_no_match_message(monkeypatch):
    monkeypatch.setattr(rag_tool, "retrieve_hits", lambda query: [])

    output = search_knowledge_base.invoke({"query": "xyz"})

    assert "没有找到" in output


def test_tool_formats_ranked_results(monkeypatch):
    hits = [
        _hit(0.72, "试用期提前三十天提出离职。", product="奥德", section="离职管理"),
    ]
    monkeypatch.setattr(rag_tool, "retrieve_hits", lambda query: hits)

    output = search_knowledge_base.invoke({"query": "离职提前几天"})

    assert "离职管理" in output
    assert "试用期提前三十天提出离职。" in output


def test_tool_logs_retrieval_with_sources_and_scores(monkeypatch, caplog):
    import logging

    hits = [
        _hit(0.72, "试用期提前三十天提出离职。", source="奥德-员工手册.md"),
    ]
    monkeypatch.setattr(rag_tool, "retrieve_hits", lambda query: hits)

    with caplog.at_level(logging.INFO, logger="ai_agents.tools.rag"):
        search_knowledge_base.invoke({"query": "离职提前几天"})

    record = caplog.records[-1]
    assert "离职提前几天" in record.getMessage()
    assert "奥德-员工手册.md" in record.getMessage()
    assert "0.72" in record.getMessage()


def test_tool_logs_unavailable(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(rag_tool, "retrieve_hits", lambda query: None)

    with caplog.at_level(logging.WARNING, logger="ai_agents.tools.rag"):
        search_knowledge_base.invoke({"query": "电池保养"})

    assert any("知识库不可用" in r.getMessage() for r in caplog.records)


def test_extract_model_tokens():
    assert rag_tool.extract_model_tokens("b5 440怎么连接打印机") == ["b5440"]
    assert rag_tool.extract_model_tokens("HUAWEI MateBook B5-440 连接打印机") == [
        "huaweimatebookb5440"
    ]
    assert rag_tool.extract_model_tokens("G540电池保养") == ["g540"]
    assert rag_tool.extract_model_tokens("扩展坞怎么用") == []
    assert rag_tool.extract_model_tokens("2024年有什么新款") == []  # 纯数字不算型号


def test_match_products_by_model_token():
    known = ["HUAWEI MateBook B5-440", "华为擎云 G540", "hak180"]
    assert rag_tool.match_products("b5 440怎么连接打印机", known) == [
        "HUAWEI MateBook B5-440"
    ]
    assert rag_tool.match_products("擎云G540电池保养", known) == ["华为擎云 G540"]
    assert rag_tool.match_products("怎么连接打印机", known) == []


def _fake_client(filtered_hits: list[dict], general_hits: list[dict], captured: dict):
    class FakeClient:
        def search(self, name, data, limit, output_fields, filter=""):
            captured.setdefault("filters", []).append(filter)
            return [filtered_hits if "B5-440" in filter else general_hits]

    return FakeClient()


def test_retrieve_prefers_filtered_hits(monkeypatch):
    monkeypatch.setattr(
        rag_tool,
        "_known_products",
        lambda uri, coll: ("HUAWEI MateBook B5-440", "华为擎云 G540"),
    )
    monkeypatch.setattr(rag_tool, "embed_query", lambda q, s: [0.1] * 8)
    captured: dict = {}
    # 过滤池分数(0.52)低于全局池(0.63), 但产品过滤结果必须排前面
    client = _fake_client(
        filtered_hits=[_hit(0.52, "通过扩展坞USB-A接口连接设备。", source="B5-440.md",
                            product="HUAWEI MateBook B5-440", section="连接USB")],
        general_hits=[_hit(0.63, "hak180 其他内容。", source="hak180.md",
                           product="hak180", section="章节")],
        captured=captured,
    )
    monkeypatch.setattr(rag_tool, "_milvus_client", lambda uri: client)

    result = rag_tool.retrieve_hits("b5 440怎么连接打印机")

    assert "B5-440" in captured["filters"][0]  # 第一次: 产品过滤检索
    assert captured["filters"][1] == ""  # 第二次: 全局补齐
    assert result[0]["entity"]["source"] == "B5-440.md"
    assert "hak180.md" in [h["entity"]["source"] for h in result]  # 不足 top_k 时全局补齐


def test_retrieve_without_product_uses_general_only(monkeypatch):
    monkeypatch.setattr(
        rag_tool,
        "_known_products",
        lambda uri, coll: ("HUAWEI MateBook B5-440",),
    )
    monkeypatch.setattr(rag_tool, "embed_query", lambda q, s: [0.1] * 8)
    captured: dict = {}
    client = _fake_client(
        filtered_hits=[],
        general_hits=[_hit(0.70, "通用连接方法。", source="G540.md")],
        captured=captured,
    )
    monkeypatch.setattr(rag_tool, "_milvus_client", lambda uri: client)

    result = rag_tool.retrieve_hits("怎么连接打印机")

    assert captured["filters"] == [""]  # 无型号: 只做一次全局检索
    assert result[0]["entity"]["source"] == "G540.md"
