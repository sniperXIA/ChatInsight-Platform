import mimetypes
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from packages.domain.enums import MediaKind, MediaLinkState
from packages.domain.models import sha256_file
from packages.importers.contracts import (
    MediaCandidateLink,
    SourceMedia,
    SourceMediaRef,
    SourceMessage,
)


class MediaResolver:
    """Discovers media files and resolves mappings between messages and media assets."""

    VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

    def scan_group_media(self, group_dir: Path) -> list[SourceMedia]:
        """Discover all media files within the group folder (in `images`, `image`, or root)."""
        media_list: list[SourceMedia] = []
        seen_files: set[str] = set()

        search_dirs = [group_dir / "images", group_dir / "image", group_dir]
        order = 1

        for sdir in search_dirs:
            if not sdir.exists() or not sdir.is_dir():
                continue
            for fpath in sorted(sdir.iterdir()):
                if not fpath.is_file() or fpath.suffix.lower() == ".txt":
                    continue

                abs_str = str(fpath.resolve())
                if abs_str in seen_files:
                    continue
                seen_files.add(abs_str)

                suffix = fpath.suffix.lower()
                if suffix in self.VIDEO_EXTS:
                    kind = MediaKind.VIDEO
                elif suffix in self.IMAGE_EXTS:
                    kind = MediaKind.IMAGE
                else:
                    continue

                rel_path = str(fpath.relative_to(group_dir)).replace("\\", "/")
                file_hash = sha256_file(str(fpath))
                size_bytes = fpath.stat().st_size
                mime_type, _ = mimetypes.guess_type(str(fpath))

                media_obj = SourceMedia(
                    schema_version=2,
                    media_external_id=f"med_{file_hash[:24]}",
                    source_message_id=None,
                    order=order,
                    kind=kind,
                    relative_path=rel_path,
                    sha256=file_hash,
                    size_bytes=size_bytes,
                    mime_type=mime_type or ("video/mp4" if kind == MediaKind.VIDEO else "image/jpeg"),
                )
                media_list.append(media_obj)
                order += 1

        return media_list

    def resolve_links(
        self,
        messages: list[SourceMessage],
        media_items: list[SourceMedia],
        group_dir: Path,
    ) -> Tuple[list[MediaCandidateLink], list[SourceMedia]]:
        """Resolve links between messages and media items."""
        links: list[MediaCandidateLink] = []
        media_by_filename = {Path(m.relative_path).name: m for m in media_items}
        linked_media_ids: set[str] = set()

        # Step 1: Explicit filename matching (Confidence 1.0, Confirmed)
        for msg in messages:
            extracted_names: list[str] = msg.raw_payload_json.get("extracted_media_names", [])
            for order, fname in enumerate(extracted_names, start=1):
                if fname in media_by_filename:
                    matched_media = media_by_filename[fname]
                    matched_media.source_message_id = msg.source_message_id
                    linked_media_ids.add(matched_media.media_external_id)

                    msg.media_refs.append(
                        SourceMediaRef(media_external_id=matched_media.media_external_id, order=order)
                    )

                    links.append(
                        MediaCandidateLink(
                            message_id=msg.source_message_id,
                            media_asset_id=matched_media.media_external_id,
                            media_filename=fname,
                            media_order=order,
                            method="explicit_filename",
                            confidence=1.0,
                            state=MediaLinkState.CONFIRMED,
                            evidence_json={"matched_name": fname, "reason": "exact_bracket_filename"},
                        )
                    )

        # Step 2: Ambiguous [图片] placeholders resolution
        unlinked_images = [
            m for m in media_items if m.kind == MediaKind.IMAGE and m.media_external_id not in linked_media_ids
        ]
        image_placeholder_msgs = [msg for msg in messages if msg.raw_payload_json.get("has_image_placeholder")]

        if unlinked_images and image_placeholder_msgs:
            # Check if there is a 1-to-1 exact sequence match
            if len(unlinked_images) == len(image_placeholder_msgs):
                for idx, (img_msg, img_media) in enumerate(zip(image_placeholder_msgs, unlinked_images), start=1):
                    # Link as candidate
                    fname = Path(img_media.relative_path).name
                    # If 1-to-1 matching, confidence is high but marked needs_review if not verified by manifest
                    conf = 0.85
                    state = MediaLinkState.CONFIRMED if conf >= 0.98 else MediaLinkState.NEEDS_REVIEW
                    img_media.source_message_id = img_msg.source_message_id
                    linked_media_ids.add(img_media.media_external_id)

                    img_msg.media_refs.append(
                        SourceMediaRef(media_external_id=img_media.media_external_id, order=1)
                    )

                    links.append(
                        MediaCandidateLink(
                            message_id=img_msg.source_message_id,
                            media_asset_id=img_media.media_external_id,
                            media_filename=fname,
                            media_order=1,
                            method="order_candidate",
                            confidence=conf,
                            state=state,
                            evidence_json={
                                "heuristic": "1_to_1_sequence_candidate",
                                "note": "Placeholder [图片] matched by single sequence alignment",
                            },
                        )
                    )
            else:
                # Multiple candidates or unequal count -> ambiguous
                for img_msg in image_placeholder_msgs:
                    for img_media in unlinked_images:
                        fname = Path(img_media.relative_path).name
                        links.append(
                            MediaCandidateLink(
                                message_id=img_msg.source_message_id,
                                media_asset_id=img_media.media_external_id,
                                media_filename=fname,
                                media_order=1,
                                method="order_candidate",
                                confidence=0.45,
                                state=MediaLinkState.NEEDS_REVIEW,
                                evidence_json={
                                    "heuristic": "ambiguous_multiple_candidates",
                                    "candidate_count": len(unlinked_images),
                                    "note": "Multiple unlinked images found for placeholder; requires human confirmation",
                                },
                            )
                        )

        return links, media_items
