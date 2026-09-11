import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from docx import Document as DocxDocument
from pypdf import PdfReader


class UnsupportedDocumentError(ValueError):
    pass


@dataclass
class ParsedQuestion:
    sequence: int | None
    stem: str
    options: list[str] | None
    answer: str | None
    analysis: str | None
    source_page: int | None = None


@dataclass
class ChunkDraft:
    sequence: int
    content: str
    chapter: str | None
    source_page_start: int | None
    source_page_end: int | None
    char_start: int
    char_end: int


def extract_text(path: Path) -> tuple[str, int | None]:
    return extract_text_from_bytes(path.read_bytes(), path.suffix)


def extract_text_from_bytes(payload: bytes, suffix: str) -> tuple[str, int | None]:
    suffix = suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(BytesIO(payload))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
        return "\n\f\n".join(pages), len(pages)
    if suffix == ".docx":
        document = DocxDocument(BytesIO(payload))
        return "\n".join(paragraph.text for paragraph in document.paragraphs), None
    if suffix in {".txt", ".md"}:
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return payload.decode(encoding), None
            except UnicodeDecodeError:
                continue
        raise ValueError("Text file encoding is not supported")
    raise UnsupportedDocumentError("Only PDF, DOCX, TXT, and MD files are supported")


QUESTION_START = re.compile(r"(?m)^\s*(\d{1,5})[.\u3002\u3001]\s+")
OPTION_LINE = re.compile(r"(?m)^\s*([A-H])[.\u3002\u3001]\s*(.+)$")
ANSWER_LINE = re.compile(r"(?mi)^\s*(?:\u53c2\u8003)?\u7b54\u6848\s*[:\uff1a]\s*(.+)$")
ANALYSIS_LINE = re.compile(r"(?mi)^\s*(?:\u7b54\u6848)?\u89e3\u6790\s*[:\uff1a]\s*(.+)$")


def parse_questions(text: str) -> list[ParsedQuestion]:
    matches = list(QUESTION_START.finditer(text))
    if not matches:
        cleaned = text.strip()
        return [ParsedQuestion(None, cleaned, None, None, None)] if cleaned else []

    questions: list[ParsedQuestion] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        raw = text[match.end():end].strip()
        question = _parse_question_block(int(match.group(1)), raw)
        if question.stem:
            questions.append(question)
    return questions


def _parse_question_block(sequence: int, raw: str) -> ParsedQuestion:
    answer_match = ANSWER_LINE.search(raw)
    analysis_match = ANALYSIS_LINE.search(raw)
    boundaries = [match.start() for match in (answer_match, analysis_match) if match]
    body = raw[:min(boundaries)].strip() if boundaries else raw
    options = [f"{match.group(1)}. {match.group(2).strip()}" for match in OPTION_LINE.finditer(body)]
    first_option = OPTION_LINE.search(body)
    stem = body[:first_option.start()].strip() if first_option else body.strip()

    answer = answer_match.group(1).strip() if answer_match else None
    analysis = analysis_match.group(1).strip() if analysis_match else None
    return ParsedQuestion(sequence, stem, options or None, answer, analysis)


_HEADING_PREFIX = re.compile(r"^(?:#{1,6}\s+|\u7b2c[\d\u4e00-\u9fff]+[\u7ae0\u8282]\s*|\d+(?:\.\d+){0,3}[.\u3001]?\s+)")
_BOUNDARY_CHARS = "\n\u3002\uff01\uff1f\uff1b.!?;"


def chunk_document(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
    chunk_model: str = "interface",
    retain_context: bool = True,
    split_by_page: bool = True,
    custom_delimiter: str = "",
) -> list[ChunkDraft]:
    if chunk_size < 100:
        raise ValueError("CHUNK_SIZE must be at least 100")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("CHUNK_OVERLAP must be non-negative and smaller than CHUNK_SIZE")
    if chunk_model not in {"interface", "structured", "fixed", "delimiter"}:
        raise ValueError("Unsupported chunk model")
    if chunk_model == "delimiter" and not custom_delimiter:
        raise ValueError("custom_delimiter is required when chunk_model is delimiter")

    chunks: list[ChunkDraft] = []
    chapter: str | None = None
    global_offset = 0
    pages = text.split("\f") if split_by_page else [text.replace("\f", "\n")]

    for page_index, page_text in enumerate(pages, start=1):
        page_number = page_index if split_by_page and len(pages) > 1 else None
        for section, section_text, start, end in _sections_for_page(
            page_text,
            chapter,
            chunk_model,
            custom_delimiter,
        ):
            if section is not None:
                chapter = section
            for content, local_start, local_end in _split_windows(section_text, chunk_size, chunk_overlap):
                chunks.append(
                    ChunkDraft(
                        sequence=len(chunks) + 1,
                        content=_with_section_context(content, chapter) if retain_context else content,
                        chapter=chapter,
                        source_page_start=page_number,
                        source_page_end=page_number,
                        char_start=global_offset + start + local_start,
                        char_end=global_offset + start + local_end,
                    )
                )
        global_offset += len(page_text) + 1

    return chunks


def _sections_for_page(
    page_text: str,
    active_chapter: str | None,
    chunk_model: str,
    custom_delimiter: str,
) -> list[tuple[str | None, str, int, int]]:
    if chunk_model == "fixed":
        return [(None, page_text.strip(), 0, len(page_text))] if page_text.strip() else []
    if chunk_model == "delimiter":
        return _sections_by_delimiter(page_text, custom_delimiter)

    sections: list[tuple[str | None, str, int, int]] = []
    chapter = active_chapter
    cursor = 0

    for line_match in re.finditer(r"(?m)^.*$", page_text):
        line = line_match.group(0).strip()
        if not line or not _is_section_boundary(line, chunk_model):
            continue
        _append_section(sections, chapter, page_text, cursor, line_match.start())
        chapter = line
        cursor = line_match.end()

    _append_section(sections, chapter, page_text, cursor, len(page_text))
    return sections


def _sections_by_delimiter(page_text: str, delimiter: str) -> list[tuple[str | None, str, int, int]]:
    sections: list[tuple[str | None, str, int, int]] = []
    cursor = 0

    for match in re.finditer(re.escape(delimiter), page_text):
        _append_section(sections, None, page_text, cursor, match.start())
        cursor = match.end()

    _append_section(sections, None, page_text, cursor, len(page_text))
    return sections


def _is_section_boundary(line: str, chunk_model: str) -> bool:
    if _HEADING_PREFIX.match(line):
        return True
    if chunk_model != "interface":
        return False
    return bool(
        re.match(r"(?i)^(?:\[?(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\]?\s+)(?:https?://|/)", line)
        or re.match(r"^(?:接口名称|接口地址|请求路径|Endpoint)\s*[:：]", line, re.IGNORECASE)
    )


def _append_section(
    sections: list[tuple[str | None, str, int, int]],
    chapter: str | None,
    page_text: str,
    start: int,
    end: int,
) -> None:
    raw = page_text[start:end]
    content = raw.strip()
    if not content:
        return
    leading = len(raw) - len(raw.lstrip())
    sections.append((chapter, content, start + leading, end))


def _split_windows(text: str, chunk_size: int, chunk_overlap: int) -> list[tuple[str, int, int]]:
    windows: list[tuple[str, int, int]] = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = min(start + chunk_size, text_length)
        if end < text_length:
            boundary_start = start + int(chunk_size * 0.6)
            boundary = max(text.rfind(marker, boundary_start, end) for marker in _BOUNDARY_CHARS)
            if boundary >= boundary_start:
                end = boundary + 1

        raw = text[start:end]
        content = raw.strip()
        if content:
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw) - len(raw.rstrip())
            windows.append((content, start + leading, end - trailing))

        if end >= text_length:
            break
        next_start = end - chunk_overlap
        start = next_start if next_start > start else end

    return windows


def _with_section_context(content: str, chapter: str | None) -> str:
    if not chapter or content.startswith(chapter):
        return content
    return f"{chapter}\n{content}"
