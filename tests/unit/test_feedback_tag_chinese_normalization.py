import pytest
from packages.insights.tag_manager import TagManager
from apps.api.routes.insights import TYPE_LABEL_MAP


def test_feedback_and_tag_chinese_mappings():
    assert 'usability_opportunity' in TYPE_LABEL_MAP
    assert TYPE_LABEL_MAP['usability_opportunity'] == '🎯 体验与易用性优化'

    assert 'documentation_gap' in TYPE_LABEL_MAP
    assert TYPE_LABEL_MAP['documentation_gap'] == '📖 说明与文档缺失'

    assert 'product_issue' in TYPE_LABEL_MAP
    assert TYPE_LABEL_MAP['product_issue'] == '🐛 产品缺陷'

    assert 'explicit_requirement' in TYPE_LABEL_MAP
    assert TYPE_LABEL_MAP['explicit_requirement'] == '💡 明确功能需求'

    assert 'latent_need' in TYPE_LABEL_MAP
    assert TYPE_LABEL_MAP['latent_need'] == '🔍 潜在需求挖掘'


def test_tag_manager_format_tag_display():
    assert TagManager.format_tag_display('usability_opportunity') == '易用性与交互优化'
    assert TagManager.format_tag_display('documentation_gap') == '说明与文档缺失'
    assert TagManager.format_tag_display('feature_request') == '功能与体验需求'

    colon_tag = 'general:usage_confusion:travel_lock'
    formatted = TagManager.format_tag_display(colon_tag)
    assert '综合交流' in formatted
    assert '操作使用困惑' in formatted
    assert '旅行锁' in formatted

    assert TagManager.format_tag_display('界面设计优化') == '界面设计优化'
    assert TagManager.format_tag_display('ui_font') == '界面与显示 · 字体与清晰度'
