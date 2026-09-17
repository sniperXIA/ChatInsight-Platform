from typing import Any, Optional
from pydantic import BaseModel, Field


class OCRBlock(BaseModel):
    text: str
    confidence: Optional[float] = None
    bbox: Optional[list[float]] = None  # [ymin, xmin, ymax, xmax] normalized 0~1000 or 0~1


class ImageEntity(BaseModel):
    entity_type: str  # device_model, os, app_feature, error_code, hardware_part, etc.
    value: str
    confidence: float = 1.0


class ImageEnrichmentOutput(BaseModel):
    summary: str = Field(description="画面综合内容简要概述（客观描述画面中可见事实）")
    ocr_blocks: list[OCRBlock] = Field(default_factory=list, description="画面提取的文本与位置")
    screen_or_scene: Optional[str] = Field(default=None, description="界面/场景名称（如App演奏界面、开箱实物、报错弹窗）")
    user_action: Optional[str] = Field(default=None, description="可观察到的用户操作（如正在插入音色卡、点击返回键）")
    observed_state: Optional[str] = Field(default=None, description="可观察到的界面或设备状态（如进度条卡在0%、LED灯常亮）")
    error_codes: list[str] = Field(default_factory=list, description="明确可见的错误码或报错提示语句")
    entities: list[ImageEntity] = Field(default_factory=list, description="提取的实体标签")
    safety_or_privacy_notes: list[str] = Field(default_factory=list, description="安全或隐私说明（如已遮蔽二维码或人脸）")
    uncertainty: list[str] = Field(default_factory=list, description="画面模糊或无法确定的事项说明")


class ASRSegment(BaseModel):
    start_ms: int
    end_ms: int
    text: str
    confidence: Optional[float] = None
    speaker: Optional[str] = None


class VideoTimelineSegment(BaseModel):
    start_ms: int
    end_ms: int
    transcript: Optional[str] = None
    visual_summary: Optional[str] = None
    ocr_text: Optional[str] = None
    observed_action: Optional[str] = None
    observed_result: Optional[str] = None
    keyframe_filename: Optional[str] = None
    confidence: float = 1.0


class VideoEnrichmentOutput(BaseModel):
    summary: str = Field(description="视频全流程客观描述与核心现象总结")
    timeline: list[VideoTimelineSegment] = Field(default_factory=list, description="关键时间节点事件序列")
    transcript_full: Optional[str] = Field(default=None, description="视频语音全文转写")
    audio_status: str = Field(default="available", description="available | no_audio_track | silent | not_applicable")
    identified_issues: list[str] = Field(default_factory=list, description="视频中展现的产品异常现象点")
    entities: list[ImageEntity] = Field(default_factory=list, description="视频中涉及的产品模块与设备实体")
    uncertainty: list[str] = Field(default_factory=list, description="不确定或需人工复核的事项")


class MediaContextPacket(BaseModel):
    media_id: str
    kind: str
    source_message_id: Optional[str] = None
    mapping_state: str = "confirmed"
    mapping_confidence: float = 1.0
    summary: Optional[str] = None
    searchable_text: str = ""
    ocr_text: Optional[str] = None
    transcript: Optional[str] = None
    timeline: list[VideoTimelineSegment] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    analysis_revision: int = 1
