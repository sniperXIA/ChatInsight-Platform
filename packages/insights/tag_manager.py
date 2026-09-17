import json
import re
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, Field

TAG_CONFIG_PATH = Path("config/tag_definitions.json")


class TagDefinition(BaseModel):
    key: str = Field(description="唯一标签标识键名，如 ui_ux, ui_font, sound_preset")
    name_zh: str = Field(description="中文显示名称，如 界面与显示, 字体与清晰度")
    category: str = Field(default="general", description="所属主大类 key，若自身为一级标签则为自身key")
    level: int = Field(default=1, description="层级: 1 表示一级大类, 2 表示二级细分类")
    parent_key: Optional[str] = Field(default=None, description="若是二级分类，指向对应的一级大类 key")
    issue_types: list[str] = Field(default_factory=lambda: ["general"], description="包含的问题类型属性: bug | suggestion | general")
    keywords: list[str] = Field(default_factory=list, description="用于文本语义自动分类的代表特征词列表")
    color: str = Field(default="indigo", description="主题颜色: indigo | purple | blue | violet | cyan | teal | amber | orange | sky | rose | emerald | slate")
    description: str = Field(default="", description="标签用途与包含的业务范围说明")
    is_system: bool = Field(default=False, description="是否为内置系统标签")
    topic_count: int = Field(default=0, description="已绑定的聚合主题数")
    episode_count: int = Field(default=0, description="已绑定的原始片段数")


# Comprehensive Multi-Level Built-in Tags derived from app问题现有标签.xlsx + domain hardware/logistics
BUILTIN_TAGS: list[dict[str, Any]] = [
    # ---------------- 0. 软件与App生态 Software & App ----------------
    {
        "key": "software_app",
        "name_zh": "软件与App生态",
        "category": "software_app",
        "level": 1,
        "parent_key": None,
        "issue_types": ["bug", "suggestion", "general"],
        "keywords": ["app", "软件", "下载", "安装包", "应用商店", "appstore", "apk", "更新app", "新版app", "客户端", "版本升级"],
        "color": "blue",
        "description": "移动端App应用下载、安装包获取、应用商店上架更新、软件闪退崩溃及版本升级维护",
        "is_system": True,
    },
    {
        "key": "app_download_install",
        "name_zh": "软件下载与安装更新",
        "category": "software_app",
        "level": 2,
        "parent_key": "software_app",
        "issue_types": ["bug", "suggestion", "general"],
        "keywords": ["app下载", "下载链接", "安装包", "apk", "testflight", "应用商店", "appstore", "app更新", "更新app", "新版app", "新版本app", "软件下载", "更新软件", "安卓安装包", "软件升级", "app升级", "升级app", "苹果商店", "应用市场"],
        "color": "blue",
        "description": "用户咨询或反馈移动端App官方下载渠道、新版安装包链接、应用商店更新与版本升级",
        "is_system": True,
    },
    {
        "key": "app_crash_freeze",
        "name_zh": "App闪退与运行卡死",
        "category": "software_app",
        "level": 2,
        "parent_key": "software_app",
        "issue_types": ["bug"],
        "keywords": ["app闪退", "软件卡死", "应用崩溃", "app卡住", "无响应", "闪退出来", "停止运行", "白屏", "闪退了"],
        "color": "blue",
        "description": "App客户端打开闪退、运行中卡死无响应或启动黑白屏等软件层稳定性问题",
        "is_system": True,
    },

    # ---------------- 1. 界面与显示 UI/UX ----------------
    {
        "key": "ui_ux",
        "name_zh": "界面与显示",
        "category": "ui_ux",
        "level": 1,
        "parent_key": None,
        "issue_types": ["bug", "suggestion"],
        "keywords": ["看不清", "看不清楚", "字太小", "字号", "字体", "排版", "瞎了", "看不见", "太小了", "界面", "ui", "暗黑", "深色", "皮肤", "遮挡", "缩放", "错位"],
        "color": "indigo",
        "description": "App界面视觉设计、字体大小辨识度、暗黑/深色主题皮肤及谱面屏幕排版布局",
        "is_system": True,
    },
    {
        "key": "ui_font",
        "name_zh": "字体与清晰度",
        "category": "ui_ux",
        "level": 2,
        "parent_key": "ui_ux",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["看不清", "看不清楚", "字太小", "字号", "字体", "瞎了", "看不见", "太小了", "字体大小", "字太密", "看不清字", "放大", "清晰度"],
        "color": "indigo",
        "description": "反馈App各界面文字/和弦字号偏小、字体易混淆或视力不便用户看不清谱面",
        "is_system": True,
    },
    {
        "key": "ui_theme",
        "name_zh": "皮肤与暗黑模式",
        "category": "ui_ux",
        "level": 2,
        "parent_key": "ui_ux",
        "issue_types": ["suggestion"],
        "keywords": ["皮肤", "暗黑", "深色", "夜间模式", "配色", "刺眼", "太亮", "主题换肤", "背景图", "封面"],
        "color": "indigo",
        "description": "用户对App深色/浅色模式切换、主题皮肤自定义、背景图与护眼配色的建议",
        "is_system": True,
    },
    {
        "key": "ui_layout",
        "name_zh": "排版与界面布局",
        "category": "ui_ux",
        "level": 2,
        "parent_key": "ui_ux",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["遮挡", "排版", "布局", "横屏", "竖屏", "界面重叠", "错位", "ui卡顿", "显示不全", "按键挡住"],
        "color": "indigo",
        "description": "App各功能页面元素遮挡、横竖屏排版错位及按键UI重叠等视觉布局问题",
        "is_system": True,
    },

    # ---------------- 2. 曲谱与乐库 ----------------
    {
        "key": "sheet_music",
        "name_zh": "曲谱与乐库",
        "category": "sheet_music",
        "level": 1,
        "parent_key": None,
        "issue_types": ["bug", "suggestion"],
        "keywords": ["曲谱", "乐库", "曲库", "歌曲", "歌曲资源", "曲目", "求谱", "错谱", "和弦", "歌单", "歌单资源", "扒谱", "加歌", "制谱", "新歌", "多维曲库", "音乐资源", "伴奏歌曲"],
        "color": "purple",
        "description": "官方曲谱乐库覆盖度、曲库资源、曲谱纠错报错、求谱扩充、歌单分类及AI/手动制谱功能",
        "is_system": True,
    },
    {
        "key": "sheet_report",
        "name_zh": "曲谱报错纠错",
        "category": "sheet_music",
        "level": 2,
        "parent_key": "sheet_music",
        "issue_types": ["bug"],
        "keywords": ["错谱", "标错", "和弦不对", "歌词错", "谱子错", "曲谱报错", "节奏不对", "音不对", "小节不对", "缺字"],
        "color": "purple",
        "description": "官方曲谱和弦配置错误、歌词错漏或节奏小节标记不准等内容报错",
        "is_system": True,
    },
    {
        "key": "sheet_request",
        "name_zh": "求谱与乐库扩充",
        "category": "sheet_music",
        "level": 2,
        "parent_key": "sheet_music",
        "issue_types": ["suggestion"],
        "keywords": ["求谱", "加歌", "乐库没有", "没有这首歌", "上新", "新歌", "想弹", "希望能出", "流行歌", "经典老歌", "曲库", "歌曲", "歌曲资源", "求歌", "歌曲扩充", "曲目扩充", "曲库资源"],
        "color": "purple",
        "description": "用户向官方反馈期望乐库新增的热门歌曲、流行金曲及分类曲目扩充建议",
        "is_system": True,
    },
    {
        "key": "sheet_category",
        "name_zh": "曲谱分类与歌单",
        "category": "sheet_music",
        "level": 2,
        "parent_key": "sheet_music",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["曲谱分类", "歌单分类", "分类标签", "筛选歌曲", "搜歌分类", "收藏夹", "分级", "难易度分类", "曲库分类", "歌曲分类", "多维曲库"],
        "color": "purple",
        "description": "曲谱按难易度、流派风格分类展示、个性化歌单管理与收藏分类检索体验",
        "is_system": True,
    },
    {
        "key": "sheet_ai_create",
        "name_zh": "AI制谱与扒谱",
        "category": "sheet_music",
        "level": 2,
        "parent_key": "sheet_music",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["ai制谱", "ai扒谱", "自动生成谱", "智能制谱", "ai做谱", "扒谱慢", "扒谱不准", "一键生成曲谱"],
        "color": "purple",
        "description": "利用AI算法自动音频扒谱、和弦伴奏生成及生成结果准确性反馈与建议",
        "is_system": True,
    },
    {
        "key": "sheet_manual_create",
        "name_zh": "手动制谱与自建导入",
        "category": "sheet_music",
        "level": 2,
        "parent_key": "sheet_music",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["手动制谱", "自建谱", "导入曲谱", "自制曲谱", "制谱工具", "编辑和弦", "导出曲谱", "txt导入", "分享曲谱"],
        "color": "purple",
        "description": "用户自主编辑创作曲谱、和弦节拍精调、第三方乐谱文件导入及自建谱分享",
        "is_system": True,
    },

    # ---------------- 3. 弹唱与走带体验 ----------------
    {
        "key": "performance",
        "name_zh": "弹唱与走带体验",
        "category": "performance",
        "level": 1,
        "parent_key": None,
        "issue_types": ["bug", "suggestion"],
        "keywords": ["走带", "弹唱", "多轨", "旋律跟唱", "投屏", "屏幕共享", "合奏", "打分", "卡顿", "伴奏同步"],
        "color": "blue",
        "description": "演奏过程中谱面走带流畅度、人声跟唱导唱、多轨伴奏、大屏投屏、合奏及打分评测系统",
        "is_system": True,
    },
    {
        "key": "play_track",
        "name_zh": "谱面走带与同步",
        "category": "performance",
        "level": 2,
        "parent_key": "performance",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["走带", "卡顿", "跳音", "谱面卡住", "光标跳", "走带慢", "走带快", "跟不上谱", "走带不同步", "滚屏"],
        "color": "blue",
        "description": "弹唱过程中谱面光标前进流畅度、滚动走带停滞、跳音及伴奏节拍同步表现",
        "is_system": True,
    },
    {
        "key": "play_vocal",
        "name_zh": "旋律跟唱与人声",
        "category": "performance",
        "level": 2,
        "parent_key": "performance",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["旋律跟唱", "导唱", "跟唱音量", "人声跟唱", "主旋律提示", "人声消除", "伴唱", "人声原唱"],
        "color": "blue",
        "description": "曲目主旋律导唱辅助、人声跟唱音量调节、原唱消音及歌词提示功能",
        "is_system": True,
    },
    {
        "key": "play_multitrack",
        "name_zh": "多轨伴奏与分轨",
        "category": "performance",
        "level": 2,
        "parent_key": "performance",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["多轨", "分轨", "伴奏分轨", "吉他轨", "贝斯轨", "鼓轨", "多轨道", "静音分轨", "各轨音量"],
        "color": "blue",
        "description": "多轨道专业伴奏支持、各乐器声部独立静音与音量配比精调",
        "is_system": True,
    },
    {
        "key": "play_cast",
        "name_zh": "投屏与屏幕共享",
        "category": "performance",
        "level": 2,
        "parent_key": "performance",
        "issue_types": ["suggestion"],
        "keywords": ["投屏", "屏幕共享", "电视投屏", "大屏投屏", "横屏投屏", "镜像", "投屏延迟", "无线投屏"],
        "color": "blue",
        "description": "将手机App谱面无线投屏至电视、平板或显示器大屏演奏练习体验",
        "is_system": True,
    },
    {
        "key": "play_ensemble_rating",
        "name_zh": "合奏与打分系统",
        "category": "performance",
        "level": 2,
        "parent_key": "performance",
        "issue_types": ["suggestion"],
        "keywords": ["合奏", "双人合奏", "联机", "打分系统", "评分", "练习反馈", "评星", "节奏准确率", "连击"],
        "color": "blue",
        "description": "多设备局域网/线上联机合奏、弹唱节奏与和弦准确度智能打分评分反馈",
        "is_system": True,
    },

    # ---------------- 4. 配置与音色 ----------------
    {
        "key": "audio_config",
        "name_zh": "配置与音色",
        "category": "audio_config",
        "level": 1,
        "parent_key": None,
        "issue_types": ["bug", "suggestion"],
        "keywords": ["风格包", "音色卡", "音色", "指板", "和弦", "鼓机", "bpm", "变调", "音量", "旅行锁", "音质"],
        "color": "violet",
        "description": "伴奏风格包、扩展音色卡加载、和弦指板把位、鼓机BPM速度、音调音量调节及旅行锁配置",
        "is_system": True,
    },
    {
        "key": "cfg_style_pack",
        "name_zh": "伴奏风格包",
        "category": "audio_config",
        "level": 2,
        "parent_key": "audio_config",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["风格包", "伴奏风格", "流行风格", "摇滚风格", "民谣风格", "扫弦风格", "节奏包", "扩展风格"],
        "color": "violet",
        "description": "不同曲风伴奏风格包的切换、演奏扫弦律动效果及官方风格库扩充诉求",
        "is_system": True,
    },
    {
        "key": "cfg_sound_preset",
        "name_zh": "旋律音色与音色卡",
        "category": "audio_config",
        "level": 2,
        "parent_key": "audio_config",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["音色卡", "扩展卡", "旋律音色", "换音色", "音质", "失真音色", "木吉他音色", "电吉他音色", "清音", "原声音色", "买音色卡"],
        "color": "violet",
        "description": "物理音色卡插拔识别、自定义吉他音色切换加载、音质还原度及音色卡购买探讨",
        "is_system": True,
    },
    {
        "key": "cfg_chord_board",
        "name_zh": "和弦指板与把位",
        "category": "audio_config",
        "level": 2,
        "parent_key": "audio_config",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["和弦指板", "指板切换", "和弦库", "高把位", "简化和弦", "自定义和弦", "指板按键", "变调夹"],
        "color": "violet",
        "description": "琴身和弦按键指板映射、把位切换、简化和弦与高阶和弦自定义配置",
        "is_system": True,
    },
    {
        "key": "cfg_drum_bpm",
        "name_zh": "鼓机与节拍BPM",
        "category": "audio_config",
        "level": 2,
        "parent_key": "audio_config",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["鼓机", "bpm", "节拍速度", "加拍", "减速", "节拍器", "鼓点", "加花", "打鼓"],
        "color": "violet",
        "description": "内置鼓机节奏型切换、BPM演奏速度微调、节拍器提示及鼓点加花操作",
        "is_system": True,
    },
    {
        "key": "cfg_tone_volume",
        "name_zh": "音调与音量平衡",
        "category": "audio_config",
        "level": 2,
        "parent_key": "audio_config",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["变调", "移调", "升调", "降调", "音量调节", "伴奏音量", "吉他音量", "杂音", "破音", "音量小", "底噪"],
        "color": "violet",
        "description": "歌曲升降调变调设置、吉他声音与伴奏音量平衡比及底噪杂音调节",
        "is_system": True,
    },
    {
        "key": "cfg_travel_lock",
        "name_zh": "旅行锁与按键防误触",
        "category": "audio_config",
        "level": 2,
        "parent_key": "audio_config",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["旅行锁", "按键锁", "锁定按键", "防误触", "锁机", "开锁", "背包误触"],
        "color": "violet",
        "description": "出行便携携带时的按键防误触旅行锁开启、解锁机制及偶发误锁排查",
        "is_system": True,
    },

    # ---------------- 5. 蓝牙与无线连接 ----------------
    {
        "key": "bluetooth_conn",
        "name_zh": "蓝牙与无线连接",
        "category": "bluetooth_conn",
        "level": 1,
        "parent_key": None,
        "issue_types": ["bug", "suggestion"],
        "keywords": ["蓝牙", "搜不到", "配对失败", "蓝牙断开", "蓝牙掉线", "连不上", "伴奏延迟", "音频不同步", "重连", "抗干扰"],
        "color": "cyan",
        "description": "手机App与吉他硬件蓝牙配对、连接掉线稳定性及无线伴奏音频传输延迟",
        "is_system": True,
    },
    {
        "key": "ble_guitar",
        "name_zh": "吉他配对与掉线",
        "category": "bluetooth_conn",
        "level": 2,
        "parent_key": "bluetooth_conn",
        "issue_types": ["bug"],
        "keywords": ["蓝牙搜不到", "配对失败", "蓝牙断开", "蓝牙掉线", "连不上吉他", "无法连接", "重连失败", "配对超时"],
        "color": "cyan",
        "description": "手机App搜索吉他蓝牙超时、初次配对失败、偶发断连掉线及自动重连机制",
        "is_system": True,
    },
    {
        "key": "ble_audio",
        "name_zh": "伴奏传输与延迟",
        "category": "bluetooth_conn",
        "level": 2,
        "parent_key": "bluetooth_conn",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["蓝牙音频", "伴奏延迟", "声音延迟", "音频不同步", "声音慢半拍", "蓝牙伴奏卡顿", "杂音卡顿", "蓝牙音质"],
        "color": "cyan",
        "description": "通过蓝牙无线向琴身或音箱传输伴奏音频时的声音延迟、卡顿及节拍同步问题",
        "is_system": True,
    },

    # ---------------- 6. 录制与社区分享 ----------------
    {
        "key": "recording_share",
        "name_zh": "录制与社区分享",
        "category": "recording_share",
        "level": 1,
        "parent_key": None,
        "issue_types": ["bug", "suggestion"],
        "keywords": ["录制", "美颜", "横屏", "不露脸", "反转", "封面", "视频", "评论区", "聊天对话", "求谱专区", "分享"],
        "color": "teal",
        "description": "App视频音频录制、美颜滤镜、横屏镜头控制、视频发布与琴友社群互动交流",
        "is_system": True,
    },
    {
        "key": "rec_video_lens",
        "name_zh": "横屏录制与镜头控制",
        "category": "recording_share",
        "level": 2,
        "parent_key": "recording_share",
        "issue_types": ["suggestion", "bug"],
        "keywords": ["横屏录制", "镜头反转", "不露脸", "遮脸", "录像", "录音", "视频上传", "无法录制", "画面倒置"],
        "color": "teal",
        "description": "弹唱视频横屏录制模式、前置/后置镜头翻转、虚拟遮脸不露脸录制及视频保存",
        "is_system": True,
    },
    {
        "key": "rec_beauty_cover",
        "name_zh": "美颜滤镜与封面编辑",
        "category": "recording_share",
        "level": 2,
        "parent_key": "recording_share",
        "issue_types": ["suggestion"],
        "keywords": ["美颜", "滤镜", "瘦脸", "封面编辑", "修改封面", "缩略图", "视频剪辑", "封面图"],
        "color": "teal",
        "description": "录制自带美颜美肤滤镜、作品发布前封面截取编辑与标题标签定制",
        "is_system": True,
    },
    {
        "key": "rec_social_comment",
        "name_zh": "社区动态与评论互动",
        "category": "recording_share",
        "level": 2,
        "parent_key": "recording_share",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["评论区", "聊天对话", "私信", "求谱专区", "点赞", "动态", "转发", "分享好友", "分享微信", "社群交流"],
        "color": "teal",
        "description": "琴友动态社区发布、评论区互动回复、内置求谱专区与微信好友分享链条",
        "is_system": True,
    },

    # ---------------- 7. 设备与系统兼容性 ----------------
    {
        "key": "device_compat",
        "name_zh": "设备与系统兼容性",
        "category": "device_compat",
        "level": 1,
        "parent_key": None,
        "issue_types": ["bug", "suggestion"],
        "keywords": ["平板", "ipad", "鸿蒙", "harmonyos", "兼容性", "新系统", "ios18", "安卓14", "旧版本", "闪退"],
        "color": "amber",
        "description": "iPad/平板大屏分辨率适配、华为鸿蒙HarmonyOS及iOS/Android各系统版本兼容性",
        "is_system": True,
    },
    {
        "key": "compat_pad",
        "name_zh": "平板与iPad适配",
        "category": "device_compat",
        "level": 2,
        "parent_key": "device_compat",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["平板", "ipad", "平板适配", "平板横屏", "大屏黑边", "分辨率", "比例不对", "ipad闪退"],
        "color": "amber",
        "description": "平板电脑与iPad设备横竖屏分辨率适配、黑边拉伸及大屏多行谱面显示优化",
        "is_system": True,
    },
    {
        "key": "compat_harmony",
        "name_zh": "鸿蒙HarmonyOS兼容",
        "category": "device_compat",
        "level": 2,
        "parent_key": "device_compat",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["鸿蒙", "harmonyos", "华为鸿蒙", "next", "鸿蒙版", "鸿蒙闪退", "鸿蒙不兼容", "纯血鸿蒙"],
        "color": "amber",
        "description": "华为手机HarmonyOS操作系统运行兼容性、权限申请与鸿蒙原生版App适配",
        "is_system": True,
    },
    {
        "key": "compat_os_ver",
        "name_zh": "系统版本与机型适配",
        "category": "device_compat",
        "level": 2,
        "parent_key": "device_compat",
        "issue_types": ["bug"],
        "keywords": ["新系统", "旧版本", "ios18", "安卓14", "安卓15", "闪退", "打不开", "系统更新后", "兼容性", "黑屏"],
        "color": "amber",
        "description": "手机系统跨版本升级后出现的App启动黑屏、闪退或特定机型兼容性缺陷",
        "is_system": True,
    },

    # ---------------- 8. 硬件做工与外设 ----------------
    {
        "key": "hardware_craft",
        "name_zh": "硬件做工与外设",
        "category": "hardware_craft",
        "level": 1,
        "parent_key": None,
        "issue_types": ["bug", "suggestion"],
        "keywords": ["琴身", "做工", "按键", "拨片", "手感", "外设", "麦克风", "c2", "话筒", "材质", "松动", "公差"],
        "color": "orange",
        "description": "吉他琴身结构做工公差、机械按键拨片手感、外设麦克风及C2扩展引擎硬件配件",
        "is_system": True,
    },
    {
        "key": "hw_body_craft",
        "name_zh": "琴身做工与材质",
        "category": "hardware_craft",
        "level": 2,
        "parent_key": "hardware_craft",
        "issue_types": ["bug"],
        "keywords": ["琴身", "做工", "缝隙", "毛刺", "公差", "材质", "外壳", "松动", "掉漆", "接缝", "琴颈", "做工粗糙"],
        "color": "orange",
        "description": "吉他琴身接缝做工公差、外壳材质触感、毛刺缝隙及机械部件装配精度",
        "is_system": True,
    },
    {
        "key": "hw_buttons_picks",
        "name_zh": "按键手感与拨片",
        "category": "hardware_craft",
        "level": 2,
        "parent_key": "hardware_craft",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["拨片", "按键手感", "回弹", "阻尼", "按键卡顿", "按键失灵", "拨片生硬", "琴键", "按压手感"],
        "color": "orange",
        "description": "拨片拨动手感阻尼、琴面按键按压回弹力度及机械按键灵敏度反馈",
        "is_system": True,
    },
    {
        "key": "hw_peripherals",
        "name_zh": "麦克风与C2外设",
        "category": "hardware_craft",
        "level": 2,
        "parent_key": "hardware_craft",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["麦克风", "话筒", "c2", "c2扩展引擎", "外设", "音箱外接", "耳机孔", "line out", "扩展底座", "收音效果"],
        "color": "orange",
        "description": "官方无线麦克风收音表现、C2扩展引擎多功能底座及外接音箱耳机适配",
        "is_system": True,
    },

    # ---------------- 9. 固件与电源管理 ----------------
    {
        "key": "firmware_power",
        "name_zh": "固件与电源管理",
        "category": "firmware_power",
        "level": 1,
        "parent_key": None,
        "issue_types": ["bug", "suggestion"],
        "keywords": ["固件", "ota", "死机", "重启", "开不了机", "报错", "充电", "电池", "续航", "掉电"],
        "color": "sky",
        "description": "设备OTA在线固件升级流程、系统运行报错死机排查及电池充放电续航表现",
        "is_system": True,
    },
    {
        "key": "fw_ota",
        "name_zh": "OTA固件在线升级",
        "category": "firmware_power",
        "level": 2,
        "parent_key": "firmware_power",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["固件", "ota", "固件升级", "升级失败", "固件更新", "固件包", "升级卡住", "变砖", "最新固件"],
        "color": "sky",
        "description": "吉他设备通过App在线更新OTA固件流程、升级进度停滞或升级失败恢复",
        "is_system": True,
    },
    {
        "key": "fw_crash",
        "name_zh": "死机与报错代码",
        "category": "firmware_power",
        "level": 2,
        "parent_key": "firmware_power",
        "issue_types": ["bug"],
        "keywords": ["死机", "重启", "自动关机", "开不了机", "无法开机", "报错代码", "指示灯闪烁", "异常响声", "卡死"],
        "color": "sky",
        "description": "设备开机无反应、运行中自动重启死机、状态指示灯异常闪烁或错误代码排查",
        "is_system": True,
    },
    {
        "key": "fw_battery",
        "name_zh": "充电与电池续航",
        "category": "firmware_power",
        "level": 2,
        "parent_key": "firmware_power",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["充电", "充不进电", "电量", "电池", "续航", "掉电快", "发烫", "充满电", "慢充", "电池寿命"],
        "color": "sky",
        "description": "电池充放电状态显示、快充慢充兼容、续航时长表现及设备发热排查",
        "is_system": True,
    },

    # ---------------- 10. 账号与安全管理 ----------------
    {
        "key": "account_login",
        "name_zh": "账号与安全管理",
        "category": "account_login",
        "level": 1,
        "parent_key": None,
        "issue_types": ["bug", "suggestion"],
        "keywords": ["验证码", "登录", "换绑", "解绑", "apple id", "封号", "违规", "注销", "账号安全", "密码"],
        "color": "rose",
        "description": "短信验证码接收、手机/微信/Apple ID登录与换绑、账号封禁申诉及账号注销",
        "is_system": True,
    },
    {
        "key": "acc_verify_code",
        "name_zh": "验证码收发",
        "category": "account_login",
        "level": 2,
        "parent_key": "account_login",
        "issue_types": ["bug"],
        "keywords": ["验证码", "收不到验证码", "验证码频繁", "短信延迟", "语音验证码", "获取验证码失败"],
        "color": "rose",
        "description": "手机登录与换绑时短信验证码收发延迟、接收不到或请求过于频繁限制",
        "is_system": True,
    },
    {
        "key": "acc_login_bind",
        "name_zh": "登录与第三方绑定",
        "category": "account_login",
        "level": 2,
        "parent_key": "account_login",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["登录", "登不上", "手机号登录", "微信登录", "apple id", "换绑", "解绑", "换手机号", "密码找回", "登录过期"],
        "color": "rose",
        "description": "多端账号同步登录、第三方Apple ID/微信绑定解绑、更换手机号及密码找回",
        "is_system": True,
    },
    {
        "key": "acc_violation",
        "name_zh": "违规封禁与注销",
        "category": "account_login",
        "level": 2,
        "parent_key": "account_login",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["违规", "封号", "注销", "账号注销", "隐私协议", "被封", "申诉", "注销冷静期"],
        "color": "rose",
        "description": "社区违规内容被禁言封号申诉、隐私协议保护及7天冷静期注销账号流程",
        "is_system": True,
    },

    # ---------------- 11. 新品探讨与研发 ----------------
    {
        "key": "new_product",
        "name_zh": "新品探讨",
        "category": "new_product",
        "level": 1,
        "parent_key": None,
        "issue_types": ["suggestion", "general"],
        "keywords": ["u1", "新品", "上市", "发售", "预售", "置换", "以旧换新", "下一代", "二代吉他", "配置曝光", "研发排期"],
        "color": "indigo",
        "description": "关于U1吉他等新品研发规划、发售排期、配置曝光、预售价格及置换政策探讨",
        "is_system": True,
    },
    {
        "key": "np_schedule",
        "name_zh": "研发排期与发售时间",
        "category": "new_product",
        "level": 2,
        "parent_key": "new_product",
        "issue_types": ["suggestion", "general"],
        "keywords": ["u1上市", "u1发售", "u1什么时候", "几月发售", "研发进度", "新品发布", "上市时间", "排产"],
        "color": "indigo",
        "description": "集中咨询U1新品吉他的研发进展、预计上市发售时间及量产排期计划",
        "is_system": True,
    },
    {
        "key": "np_pricing",
        "name_zh": "预售价格与置换政策",
        "category": "new_product",
        "level": 2,
        "parent_key": "new_product",
        "issue_types": ["suggestion", "general"],
        "keywords": ["u1价格", "u1多少钱", "预售", "定金", "以旧换新", "置换补贴", "老用户优惠", "抵扣"],
        "color": "indigo",
        "description": "集中咨询U1新品吉他的预售价格区间、定金抵扣活动及老用户升级置换政策",
        "is_system": True,
    },
    {
        "key": "np_specs",
        "name_zh": "硬件规格与外观曝光",
        "category": "new_product",
        "level": 2,
        "parent_key": "new_product",
        "issue_types": ["suggestion", "general"],
        "keywords": ["u1配置", "u1做工", "u1材质", "u1尺寸", "新款吉他", "二代琴", "u1曝光", "外观颜色"],
        "color": "indigo",
        "description": "探讨U1新品吉他的外观设计、琴体材质用料、硬件规格参数及用户期望特性",
        "is_system": True,
    },

    # ---------------- 12. 物流发货与售后保障 ----------------
    {
        "key": "logistics_srv",
        "name_zh": "物流与售后",
        "category": "logistics_srv",
        "level": 1,
        "parent_key": None,
        "issue_types": ["bug", "suggestion", "general"],
        "keywords": ["发货", "顺丰", "快递", "物流单号", "几天到", "退货", "换货", "售后", "保修", "客服"],
        "color": "emerald",
        "description": "官方商城购琴发货时效、顺丰物流轨迹追踪、售后退换货保障及保修维修政策",
        "is_system": True,
    },
    {
        "key": "logistics_track",
        "name_zh": "顺丰时效与物流单号",
        "category": "logistics_srv",
        "level": 2,
        "parent_key": "logistics_srv",
        "issue_types": ["general"],
        "keywords": ["顺丰", "发货", "快递", "物流单号", "几天到", "查物流", "发货时效", "包裹", "顺丰特快"],
        "color": "emerald",
        "description": "咨询商城订单顺丰物流发货时效、单号查询及快递在途轨迹追踪",
        "is_system": True,
    },
    {
        "key": "service_policy",
        "name_zh": "售后退换与保修保障",
        "category": "logistics_srv",
        "level": 2,
        "parent_key": "logistics_srv",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["退货", "换货", "售后", "客服", "保修", "寄修", "7天无理由", "发票", "配件补发", "维修费"],
        "color": "emerald",
        "description": "官方7天无理由退换政策、硬件故障寄修保修流程及售后客服支持",
        "is_system": True,
    },

    # ---------------- 13. 新手上手与教学指引 ----------------
    {
        "key": "tutorial_guide",
        "name_zh": "新手与教学",
        "category": "tutorial_guide",
        "level": 1,
        "parent_key": None,
        "issue_types": ["suggestion", "general"],
        "keywords": ["新手", "入门", "指法", "教学", "教程", "怎么弹", "扫弦", "零基础", "开机引导", "自学"],
        "color": "emerald",
        "description": "初学者零基础上手教学、演奏基础指法按法、扫弦节奏练习及官方新手开机引导",
        "is_system": True,
    },
    {
        "key": "tut_fingering",
        "name_zh": "初学指法与扫弦练习",
        "category": "tutorial_guide",
        "level": 2,
        "parent_key": "tutorial_guide",
        "issue_types": ["suggestion", "general"],
        "keywords": ["新手", "入门", "怎么弹", "指法", "扫弦", "和弦按法", "练习指法", "零基础", "自学", "弹唱姿势"],
        "color": "emerald",
        "description": "零基础琴友交流基础演奏姿势、和弦按法转换技巧及扫弦节奏自学建议",
        "is_system": True,
    },
    {
        "key": "tut_guide_video",
        "name_zh": "新手引导与教学视频",
        "category": "tutorial_guide",
        "level": 2,
        "parent_key": "tutorial_guide",
        "issue_types": ["suggestion", "general"],
        "keywords": ["新手引导", "教程", "教学视频", "重置引导", "使用说明", "说明书", "官方教学"],
        "color": "emerald",
        "description": "App开机交互引导步骤、官方教学视频内容质量及重置教学指引功能",
        "is_system": True,
    },

        # ---------------- Legacy Built-in Tag Aliases for Backward Compatibility ----------------
    {
        "key": "sound_preset",
        "name_zh": "音色与曲谱",
        "category": "audio_config",
        "level": 2,
        "parent_key": "audio_config",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["音色", "扩展卡", "旋律音色", "换音色", "音质"],
        "color": "violet",
        "description": "旋律音色与扩展音色卡加载配置",
        "is_system": True,
    },
    {
        "key": "software_app",
        "name_zh": "App与连接",
        "category": "ui_ux",
        "level": 2,
        "parent_key": "ui_ux",
        "issue_types": ["bug", "suggestion"],
        "keywords": ["app", "软件", "连接", "蓝牙"],
        "color": "blue",
        "description": "手机App与蓝牙连接体验",
        "is_system": True,
    },
    {
        "key": "hardware",
        "name_zh": "硬件做工",
        "category": "hardware_craft",
        "level": 1,
        "parent_key": None,
        "issue_types": ["bug", "suggestion"],
        "keywords": ["做工", "琴身", "按键", "拨片"],
        "color": "orange",
        "description": "吉他硬件装配与按键做工",
        "is_system": True,
    },
    {
        "key": "firmware",
        "name_zh": "固件系统",
        "category": "firmware_power",
        "level": 1,
        "parent_key": None,
        "issue_types": ["bug", "suggestion"],
        "keywords": ["固件", "ota", "升级", "死机"],
        "color": "sky",
        "description": "设备OTA固件在线升级与系统运行",
        "is_system": True,
    },
    {
        "key": "logistics",
        "name_zh": "物流售后",
        "category": "logistics_srv",
        "level": 1,
        "parent_key": None,
        "issue_types": ["bug", "suggestion", "general"],
        "keywords": ["发货", "顺丰", "快递", "售后"],
        "color": "emerald",
        "description": "订单顺丰发货时效与售后保障",
        "is_system": True,
    },

    # ---------------- 14. 综合交流与好评 ----------------
    {
        "key": "general",
        "name_zh": "综合交流",
        "category": "general",
        "level": 1,
        "parent_key": None,
        "issue_types": ["general"],
        "keywords": ["好听", "喜欢", "神器", "分享", "弹唱视频", "出游便携", "日常分享", "太棒了", "日常交流"],
        "color": "slate",
        "description": "社群琴友日常弹唱作品分享、出游便携体验、好评心得及闲聊互动交流",
        "is_system": True,
    },
]


# Comprehensive Chinese dictionary for feedback types, category tokens, and tag aliases
FEEDBACK_AND_TAG_ZH_MAP: dict[str, str] = {
    # Feedback Types & Insights (Directly resolves English terms like usability_opportunity)
    "usability_opportunity": "易用性与交互优化",
    "documentation_gap": "说明与文档缺失",
    "product_issue": "产品功能缺陷",
    "explicit_requirement": "明确功能需求",
    "latent_need": "潜在需求挖掘",
    "consultation": "使用咨询与指导",
    "positive_signal": "积极体验好评",
    "non_product": "社群日常交流",
    "feature_request": "功能与体验需求",
    "issue": "产品缺陷",
    "inquiry": "咨询求助",
    "praise": "体验好评",
    "suggestion": "体验建议",
    "bug": "产品缺陷",

    # Common Subcategories & Aliases
    "ui_theme": "皮肤与暗黑模式",
    "ui_font": "字体与清晰度",
    "ui_layout": "排版与界面布局",
    "ui_ux": "界面与显示",
    "sheet_music": "曲谱与乐库",
    "sheet_report": "曲谱报错纠错",
    "sheet_request": "求谱与乐库扩充",
    "sheet_category": "曲谱分类与歌单",
    "sheet_ai_create": "AI制谱与扒谱",
    "sheet_manual_create": "手动制谱与导入",
    "performance": "弹唱与伴奏",
    "play_track": "谱面走带与同步",
    "play_vocal": "旋律跟唱与人声",
    "play_multitrack": "多轨伴奏与分轨",
    "play_cast": "投屏与合奏",
    "play_ensemble_rating": "合奏与打分系统",
    "audio_config": "配置与音色",
    "cfg_style_pack": "伴奏风格包",
    "cfg_sound_preset": "旋律音色与音色卡",
    "cfg_chord_board": "和弦指板与把位",
    "cfg_drum_bpm": "鼓机与节拍BPM",
    "cfg_tone_volume": "音调与音量平衡",
    "cfg_travel_lock": "旅行锁与按键防误触",
    "bluetooth_conn": "蓝牙与无线连接",
    "ble_guitar": "吉他配对与掉线",
    "ble_audio": "伴奏传输与延迟",
    "recording_share": "录制与社区分享",
    "rec_video_lens": "横屏录制与镜头控制",
    "rec_beauty_cover": "美颜滤镜与封面编辑",
    "rec_social_comment": "社区动态与评论互动",
    "device_compat": "设备与系统兼容性",
    "compat_pad": "平板与iPad适配",
    "compat_harmony": "鸿蒙HarmonyOS兼容",
    "compat_os_ver": "系统版本与机型适配",
    "hardware_craft": "硬件做工与外设",
    "hw_body_craft": "琴身做工与材质",
    "hw_buttons_picks": "按键手感与拨片",
    "hw_peripherals": "麦克风与外设",
    "firmware_power": "固件与电源管理",
    "fw_ota": "OTA固件在线升级",
    "fw_crash": "死机与报错代码",
    "fw_battery": "充电与电池续航",
    "account_login": "账号与安全管理",
    "acc_verify_code": "验证码收发",
    "acc_login_bind": "登录与第三方绑定",
    "acc_violation": "违规封禁与注销",
    "new_product": "新品探讨",
    "np_schedule": "研发排期与发售时间",
    "np_pricing": "预售价格与置换政策",
    "np_specs": "硬件规格与外观曝光",
    "logistics_srv": "物流与售后",
    "logistics_track": "顺丰时效与物流单号",
    "service_policy": "售后退换与保修保障",
    "tutorial_guide": "新手与教学",
    "tut_fingering": "初学指法与扫弦练习",
    "tut_guide_video": "新手引导与教学视频",
    "general": "综合交流",

    # Colon-token sub-parts
    "usage_confusion": "操作使用困惑",
    "feature_confusion": "功能理解困惑",
    "feature_clarity": "功能说明与指引",
    "feature_clarification": "功能释疑与指引",
    "official_activity": "官方活动与调研",
    "official_event": "官方动态与事件",
    "official_announcement": "官方公告与发布",
    "official_response": "官方客服答复",
    "product_improvement": "产品体验改进",
    "accessory_experience": "外设配件体验",
    "accessory_demand": "外设需求反馈",
    "accessory_purchase": "配件选购咨询",
    "accessories_purchase": "配件选购咨询",
    "course_content": "教学课程内容",
    "video_recording": "视频录制与分享",
    "music_library_label": "曲谱乐库标签",
    "smart_mode_operation": "智能模式操作",
    "ui_layout_suggestion": "界面排版建议",
    "button_function": "按键功能与切换",
    "sound_expansion_card": "音色扩展卡",
    "custom_style_pack": "自定义风格包",
    "style_pack_customization": "风格包定制",
    "tone_expansion": "音色库扩展",
    "tone_auto_match": "音色自动匹配",
    "tone_quality": "音色品质",
    "lyrics_display": "歌词与字体显示",
    "secondhand_trade": "二手与转让交流",
    "app_usage_confusion": "App使用困惑",
    "app_update_guide": "App版本更新",
    "battery_charging": "电池充电与续航",
    "battery_protection": "低电量保护",
    "battery_prompt": "低电量提示",
    "customer_service": "客服响应与技术支持",
    "purchase_issue": "优惠券与购买",
    "purchase_availability": "货源与到货排期",
    "offline_community": "同城与社群交流",
    "pricing_feedback": "产品定价与优惠",
    "travel_lock": "旅行锁防误触",
    "beta_test_recruitment": "内测招募活动",
    "region_group_survey": "地区群友调研",
    "chord_following_play": "跟弹与和弦辅助",
    "music_theory_course": "乐理教学课程",
    "expansion_slot_accessory": "扩展槽与外设接口",
    "update_entry_guide": "固件升级入口指引",
    "expansion_port_tone_card": "扩展接口与音色卡",
    "return_refund": "售后退换货",
    "screen_display": "屏幕与界面显示",
    "accessory_craft": "配件做工与材质",
    "pairing_guide": "配对指引与连接",
    "ugc_incentive": "制谱激励与活动",
    "operation_guide": "操作指引与教程",
    "paddle_no_sound": "拨片手感与发声",
    "button_issue": "按键反馈与失灵",
    "community_ops": "社群互动与运营",
    "playing_technique": "演奏技巧与手型",
    "operation_confusion": "操作界面困惑",
    "timbre_switch_limit": "音色切换限制",
    "sound_pickup": "拾音与外接输出",
    "lyrics_sensitive_word": "歌词审核与提示",
    "app_bug": "App异常与报错",
    "sheet_library_missing": "曲谱乐库缺失",
    "surface_cleaning": "琴体清洁与保养",
    "interface_design": "硬件接口设计",
    "external_connection": "外部设备连接",
    "audio_interface": "音频接口与扩展",
    "switch_design": "开关与物理按键",
    "firmware_version_clarification": "固件版本说明",
}


# Backwards compatibility key mapping for legacy tags
TAG_LEGACY_ALIASES: dict[str, str] = {
    "software_app": "ui_ux",
    "sound_preset": "audio_config",
    "hardware": "hardware_craft",
    "firmware": "firmware_power",
    "logistics": "logistics_srv",
    "feature_request": "ui_ux",
    "issue": "general",
    "inquiry": "general",
    "praise": "general",
}


class TagManager:
    """Manages topic tags metadata, hierarchy, keyword matching, and Chinese name resolution."""
    _cached_tags: Optional[dict[str, TagDefinition]] = None

    @classmethod
    def clear_cache(cls):
        cls._cached_tags = None

    @classmethod
    def _load(cls) -> dict[str, TagDefinition]:
        if cls._cached_tags is not None:
            return cls._cached_tags

        tags_map: dict[str, TagDefinition] = {}
        for item in BUILTIN_TAGS:
            tags_map[item["key"]] = TagDefinition(**item)

        if TAG_CONFIG_PATH.exists():
            try:
                data = json.loads(TAG_CONFIG_PATH.read_text(encoding="utf-8"))
                for item in data.get("custom_tags", []):
                    tags_map[item["key"]] = TagDefinition(**item)
            except Exception:
                pass

        cls._cached_tags = tags_map
        return tags_map

    @classmethod
    def get_all_tags(cls) -> list[TagDefinition]:
        return list(cls._load().values())

    @classmethod
    def get_level1_tags(cls) -> list[TagDefinition]:
        return [t for t in cls.get_all_tags() if t.level == 1]

    @classmethod
    def get_level2_tags(cls, parent_key: Optional[str] = None) -> list[TagDefinition]:
        tags = [t for t in cls.get_all_tags() if t.level == 2]
        if parent_key:
            return [t for t in tags if t.parent_key == parent_key]
        return tags

    @classmethod
    def get_tag(cls, key: str) -> TagDefinition:
        return cls.get_tag_info(key)

    @classmethod
    def get_tag_info(cls, key: str) -> TagDefinition:
        tags = cls._load()
        resolved_key = TAG_LEGACY_ALIASES.get(key, key)
        if resolved_key in tags:
            return tags[resolved_key]
        if key in tags:
            return tags[key]

        key_lower = str(key).lower().strip()
        if key_lower in FEEDBACK_AND_TAG_ZH_MAP:
            name_zh = FEEDBACK_AND_TAG_ZH_MAP[key_lower]
        else:
            name_zh = key.replace("_", " ").title()

        return TagDefinition(
            key=key,
            name_zh=name_zh,
            category="general",
            level=1,
            color="slate",
            description=f"标签: {name_zh}",
            is_system=False,
        )

    @classmethod
    def format_tag_display(cls, tag: str) -> str:
        """
        Guarantees clean, human-readable Chinese display for any tag key, colon-separated
        category path, or raw feedback identifier (e.g. 'usability_opportunity' -> '易用性与交互优化').
        """
        if not tag:
            return ""
        tag_str = str(tag).strip()

        # If already purely Chinese (contains CJK characters and no colons)
        if ":" not in tag_str and any("\u4e00" <= ch <= "\u9fff" for ch in tag_str):
            return tag_str

        # Handle colon-separated hierarchy e.g. general:usage_confusion:travel_lock
        if ":" in tag_str:
            parts = [p.strip() for p in tag_str.split(":") if p.strip()]
            zh_parts = []
            for p in parts:
                p_lower = p.lower()
                if any("\u4e00" <= ch <= "\u9fff" for ch in p):
                    zh_parts.append(p)
                elif p_lower in FEEDBACK_AND_TAG_ZH_MAP:
                    zh_parts.append(FEEDBACK_AND_TAG_ZH_MAP[p_lower])
                else:
                    loaded = cls.get_tag_info(p_lower)
                    if loaded and loaded.name_zh and loaded.name_zh.lower() != p_lower:
                        zh_parts.append(loaded.name_zh)
                    else:
                        zh_parts.append(p)
            return " · ".join(dict.fromkeys(zh_parts))

        # Single key lookup
        tag_lower = tag_str.lower()
        tags_map = cls._load()
        if tag_lower in tags_map:
            loaded = tags_map[tag_lower]
            if loaded.level == 2 and loaded.parent_key:
                parent = cls.get_tag_info(loaded.parent_key)
                return f"{parent.name_zh} · {loaded.name_zh}"
            return loaded.name_zh

        if tag_lower in FEEDBACK_AND_TAG_ZH_MAP:
            return FEEDBACK_AND_TAG_ZH_MAP[tag_lower]

        loaded = cls.get_tag_info(tag_lower)
        if loaded and loaded.name_zh and loaded.name_zh.lower() != tag_lower:
            return loaded.name_zh

        return tag_str

    @classmethod
    def get_tag_display_name(cls, key: str) -> str:
        tag = cls.get_tag_info(key)
        if tag.level == 2 and tag.parent_key:
            parent = cls.get_tag_info(tag.parent_key)
            return f"{parent.name_zh} · {tag.name_zh}"
        return cls.format_tag_display(key)

    @classmethod
    def get_tag_color(cls, key: str) -> str:
        return cls.get_tag_info(key).color

    @classmethod
    def match_best_tag(cls, text: str) -> tuple[str, str, str]:
        """
        Intelligently classifies given text against the hierarchical taxonomy.
        Returns (l1_key, l2_key, display_name_zh).
        Prioritizes specific problem domains before falling back to general.
        """
        if not text:
            return ("general", "gen_daily_share", "综合交流")

        low_text = text.lower()

        # 0. Mobile App & Software Ecology (App Download, Links, APK, App updates)
        # Prioritized BEFORE Firmware OTA to avoid classifying App update as firmware
        if any(k in low_text for k in [
            "app下载", "下载app", "下载链接", "安装包", "apk", "testflight", "应用商店", "app store", "appstore",
            "app更新", "更新app", "新版app", "新版应用", "新版本app", "软件下载", "更新软件", "安卓安装包",
            "软件更新", "软件升级", "app升级", "升级app", "苹果商店", "华为应用市场", "应用市场", "下载不了app",
            "下载地址", "客户端下载", "最新安装包", "app版本"
        ]) or (("app" in low_text or "软件" in low_text or "应用" in low_text) and any(k in low_text for k in ["下载", "链接", "安装", "更新", "升级", "安装包", "版本", "地址"])):
            return ("software_app", "app_download_install", "软件与App · 软件下载与安装更新")

        if (("app" in low_text or "软件" in low_text or "客户端" in low_text) and any(k in low_text for k in ["闪退", "打不开", "白屏", "闪退出来", "崩溃", "闪退了", "停止运行", "卡在启动页"])):
            return ("software_app", "app_crash_freeze", "软件与App · App闪退与运行卡死")

        # 1. UI Font Size & Visibility (e.g. "我早就看不清楚了", "字号太小")
        if any(k in low_text for k in ["看不清", "看不清楚", "字太小", "字号", "字体", "瞎了", "看不见", "太小了", "字体大小", "字太密", "看不清字", "放大", "缩放", "清晰度"]):
            return ("ui_ux", "ui_font", "界面与显示 · 字体与清晰度")

        # 2. UI Dark Theme & Skins
        if any(k in low_text for k in ["皮肤", "暗黑", "深色", "夜间模式", "夜间", "配色", "刺眼", "太亮", "主题换肤", "护眼", "背景图"]):
            return ("ui_ux", "ui_theme", "界面与显示 · 皮肤与暗黑模式")

        # 3. Device & Platform Compatibility (iPad, Pad, HarmonyOS, iOS, Android)
        if any(k in low_text for k in ["平板", "ipad", "平板适配", "平板横屏", "pad"]):
            return ("device_compat", "compat_pad", "设备与兼容 · 平板与iPad适配")
        if any(k in low_text for k in ["鸿蒙", "harmonyos", "华为鸿蒙", "鸿蒙版", "纯血鸿蒙"]):
            return ("device_compat", "compat_harmony", "设备与兼容 · 鸿蒙HarmonyOS兼容")
        if any(k in low_text for k in ["新系统", "ios18", "安卓14", "安卓15", "系统更新后", "兼容性", "黑屏"]):
            return ("device_compat", "compat_os_ver", "设备与兼容 · 系统版本与机型适配")

        # 4. Sheet Music & Library
        if any(k in low_text for k in ["错谱", "标错", "和弦不对", "歌词错", "谱子错", "曲谱报错", "小节不对", "缺字", "和弦标错"]):
            return ("sheet_music", "sheet_report", "曲谱与乐库 · 曲谱报错纠错")
        if any(k in low_text for k in ["ai制谱", "ai扒谱", "自动生成谱", "智能制谱", "ai做谱", "扒谱"]):
            return ("sheet_music", "sheet_ai_create", "曲谱与乐库 · AI制谱与扒谱")
        if any(k in low_text for k in ["手动制谱", "自建谱", "导入曲谱", "自制曲谱", "制谱工具", "编辑和弦"]):
            return ("sheet_music", "sheet_manual_create", "曲谱与乐库 · 手动制谱与自建导入")
        if any(k in low_text for k in ["曲谱分类", "歌单分类", "歌单", "收藏夹"]):
            return ("sheet_music", "sheet_category", "曲谱与乐库 · 曲谱分类与歌单")
        if any(k in low_text for k in ["求谱", "加歌", "加一首", "乐库", "曲谱", "谱子", "没有这首歌", "希望能出", "流行歌", "经典老歌", "新歌", "周杰伦", "想弹", "曲库"]):
            return ("sheet_music", "sheet_request", "曲谱与乐库 · 求谱与乐库扩充")

        # 5. UI Layout & Visual Overlap
        if any(k in low_text for k in ["遮挡", "排版", "布局", "横屏", "竖屏", "界面重叠", "错位", "ui卡顿", "显示不全", "按键挡住"]):
            return ("ui_ux", "ui_layout", "界面与显示 · 排版与界面布局")

        # 6. Performance & Tracking
        if any(k in low_text for k in ["走带", "卡顿", "跳音", "谱面卡住", "光标跳", "走带慢", "走带快", "跟不上谱", "滚屏"]):
            return ("performance", "play_track", "弹唱与走带 · 谱面走带与同步")
        if any(k in low_text for k in ["旋律跟唱", "导唱", "跟唱音量", "人声跟唱", "人声消除", "伴唱"]):
            return ("performance", "play_vocal", "弹唱与走带 · 旋律跟唱与人声")
        if any(k in low_text for k in ["多轨", "分轨", "伴奏分轨", "多轨道"]):
            return ("performance", "play_multitrack", "弹唱与走带 · 多轨伴奏与分轨")
        if any(k in low_text for k in ["投屏", "屏幕共享", "电视投屏", "大屏投屏"]):
            return ("performance", "play_cast", "弹唱与走带 · 投屏与屏幕共享")
        if any(k in low_text for k in ["合奏", "打分系统", "评分", "练习反馈", "节奏准确率"]):
            return ("performance", "play_ensemble_rating", "弹唱与走带 · 合奏与打分系统")

        # 7. Bluetooth & Connectivity
        if any(k in low_text for k in ["延迟", "伴奏延迟", "声音延迟", "音频不同步", "慢半拍"]):
            return ("bluetooth_conn", "ble_audio", "蓝牙与无线 · 伴奏传输与延迟")
        if any(k in low_text for k in ["蓝牙", "搜不到", "配对失败", "蓝牙断开", "蓝牙掉线", "连不上", "重连"]):
            return ("bluetooth_conn", "ble_guitar", "蓝牙与无线 · 吉他配对与掉线")

        # 8. Audio & Config
        if any(k in low_text for k in ["风格包", "伴奏风格", "扫弦风格", "节奏包"]):
            return ("audio_config", "cfg_style_pack", "配置与音色 · 伴奏风格包")
        if any(k in low_text for k in ["音色卡", "扩展卡", "旋律音色", "换音色", "音质", "失真音色", "木吉他音色"]):
            return ("audio_config", "cfg_sound_preset", "配置与音色 · 旋律音色与音色卡")
        if any(k in low_text for k in ["和弦指板", "指板切换", "和弦库", "高把位", "简化和弦"]):
            return ("audio_config", "cfg_chord_board", "配置与音色 · 和弦指板与把位")
        if any(k in low_text for k in ["鼓机", "bpm", "节拍速度", "节拍器", "鼓点"]):
            return ("audio_config", "cfg_drum_bpm", "配置与音色 · 鼓机与节拍BPM")
        if any(k in low_text for k in ["变调", "移调", "升调", "降调", "音量调节", "伴奏音量", "吉他音量", "破音", "底噪"]):
            return ("audio_config", "cfg_tone_volume", "配置与音色 · 音调与音量平衡")
        if any(k in low_text for k in ["旅行锁", "按键锁", "锁定按键", "防误触", "锁机"]):
            return ("audio_config", "cfg_travel_lock", "配置与音色 · 旅行锁与按键防误触")

        # 9. Recording & Social
        if any(k in low_text for k in ["横屏录制", "镜头反转", "不露脸", "遮脸", "录制视频"]):
            return ("recording_share", "rec_video_lens", "录制与社区 · 横屏录制与镜头控制")
        if any(k in low_text for k in ["美颜", "滤镜", "封面编辑", "修改封面"]):
            return ("recording_share", "rec_beauty_cover", "录制与社区 · 美颜滤镜与封面编辑")
        if any(k in low_text for k in ["评论区", "聊天对话", "求谱专区", "私信", "分享好友", "分享微信"]):
            return ("recording_share", "rec_social_comment", "录制与社区 · 社区动态与评论互动")

        # 10. Hardware & Peripherals
        if any(k in low_text for k in ["拨片", "按键手感", "回弹", "按键失灵", "拨片生硬"]):
            return ("hardware_craft", "hw_buttons_picks", "硬件与外设 · 按键手感与拨片")
        if any(k in low_text for k in ["麦克风", "话筒", "c2", "c2扩展引擎", "外设", "音箱外接"]):
            return ("hardware_craft", "hw_peripherals", "硬件与外设 · 麦克风与C2外设")
        if any(k in low_text for k in ["琴身", "做工", "缝隙", "毛刺", "公差", "材质", "外壳", "松动", "掉漆"]):
            return ("hardware_craft", "hw_body_craft", "硬件与外设 · 琴身做工与材质")

        # 11. Firmware & Power
        if any(k in low_text for k in ["固件", "ota", "固件升级", "升级失败", "变砖"]):
            return ("firmware_power", "fw_ota", "固件与电源 · OTA固件在线升级")
        if any(k in low_text for k in ["死机", "重启", "开不了机", "无法开机", "报错代码"]):
            return ("firmware_power", "fw_crash", "固件与电源 · 死机与报错代码")
        if any(k in low_text for k in ["充电", "充不进电", "电量", "电池", "续航", "掉电快"]):
            return ("firmware_power", "fw_battery", "固件与电源 · 充电与电池续航")

        # 12. Account & Security
        if any(k in low_text for k in ["验证码", "收不到验证码", "验证码频繁"]):
            return ("account_login", "acc_verify_code", "账号与安全 · 验证码收发")
        if any(k in low_text for k in ["登录", "登不上", "手机号登录", "微信登录", "apple id", "换绑", "解绑"]):
            return ("account_login", "acc_login_bind", "账号与安全 · 登录与第三方绑定")
        if any(k in low_text for k in ["违规", "封号", "注销", "账号注销"]):
            return ("account_login", "acc_violation", "账号与安全 · 违规封禁与注销")

        # 13. U1 & New Products
        if any(k in low_text for k in ["u1上市", "u1发售", "u1什么时候", "几月发售", "研发进度", "上市时间", "上市发售", "什么时候上市", "排期几月"]):
            return ("new_product", "np_schedule", "新品探讨 · 研发排期与发售时间")
        if any(k in low_text for k in ["u1价格", "u1多少钱", "预售", "定金", "以旧换新", "置换补贴"]):
            return ("new_product", "np_pricing", "新品探讨 · 预售价格与置换政策")
        if any(k in low_text for k in ["u1", "新品吉他", "二代琴", "配置曝光", "新款"]):
            return ("new_product", "np_specs", "新品探讨 · 硬件规格与外观曝光")

        # 14. Logistics & Service
        if any(k in low_text for k in ["发货", "顺丰", "快递", "物流单号", "几天到", "查物流"]):
            return ("logistics_srv", "logistics_track", "物流与售后 · 顺丰时效与物流单号")
        if any(k in low_text for k in ["退货", "换货", "售后", "客服", "保修", "7天无理由"]):
            return ("logistics_srv", "service_policy", "物流与售后 · 售后退换与保修保障")

        # 15. Tutorial & Beginner
        if any(k in low_text for k in ["新手", "入门", "怎么弹", "指法", "扫弦", "零基础", "和弦按法"]):
            return ("tutorial_guide", "tut_fingering", "新手与教学 · 初学指法与扫弦练习")
        if any(k in low_text for k in ["新手引导", "教学视频", "重置引导", "说明书", "教程"]):
            return ("tutorial_guide", "tut_guide_video", "新手与教学 · 新手引导与教学视频")

        # 16. General / UI fallback
        if any(k in low_text for k in ["ui", "界面"]):
            return ("ui_ux", "ui_layout", "界面与显示 · 排版与界面布局")

        # 17. General Daily Sharing
        if any(k in low_text for k in ["好听", "喜欢", "神器", "分享", "弹唱", "录音", "视频", "出游", "太棒了"]):
            return ("general", "gen_daily_share", "综合交流 · 日常弹唱与好评心得")

        return ("general", "general", "综合交流")

    # Alias for semantic category & tag matching
    get_best_category_and_tags = match_best_tag

    @classmethod
    def upsert_tag(cls, tag_data: dict[str, Any]) -> TagDefinition:
        tags = cls._load()
        key = str(tag_data.get("key", "")).strip().lower().replace(" ", "_")
        if not key:
            raise ValueError("Tag key cannot be empty")

        name_zh = str(tag_data.get("name_zh", "")).strip() or key
        category = str(tag_data.get("category", "general")).strip()
        level = int(tag_data.get("level", 1 if not tag_data.get("parent_key") else 2))
        parent_key = tag_data.get("parent_key") or None
        issue_types = tag_data.get("issue_types") or ["general"]
        keywords = tag_data.get("keywords") or []
        color = str(tag_data.get("color", "indigo")).strip()
        description = str(tag_data.get("description", "")).strip()
        is_system = tags[key].is_system if key in tags else False

        tag = TagDefinition(
            key=key,
            name_zh=name_zh,
            category=category,
            level=level,
            parent_key=parent_key,
            issue_types=issue_types,
            keywords=keywords,
            color=color,
            description=description,
            is_system=is_system,
        )
        tags[key] = tag
        cls._save()
        return tag

    @classmethod
    def delete_tag(cls, key: str) -> bool:
        tags = cls._load()
        if key in tags:
            if tags[key].is_system:
                return False
            del tags[key]
            cls._save()
            return True
        return False

    @classmethod
    def _save(cls):
        if cls._cached_tags is None:
            return
        custom_list = [t.model_dump() for t in cls._cached_tags.values() if not t.is_system]
        TAG_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        TAG_CONFIG_PATH.write_text(json.dumps({"custom_tags": custom_list}, ensure_ascii=False, indent=2), encoding="utf-8")
