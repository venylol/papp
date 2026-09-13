from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import re
import subprocess
import time
from pathlib import Path
from typing import Any


HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
UL_RE = re.compile(r"^\s*[-*+]\s+(.+)$")
OL_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$")
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
METADATA_PREFIXES = ("报告人：", "身份：", "日期：", "报告日期：", "调查对象：", "被报告对局：")


def read_text(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8-sig")


def write_text(path: str | Path, value: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value, encoding="utf-8", newline="")


def inline_html(text: str, image_base_dir: Path | None = None) -> str:
    tokens: list[str] = []

    def store(value: str) -> str:
        index = len(tokens)
        tokens.append(value)
        return f"@@TOKEN{index}@@"

    def code_replace(match: re.Match[str]) -> str:
        return store(f"<code>{html.escape(match.group(1))}</code>")

    text = re.sub(r"<br\s*/?>", lambda _: store("<br>"), text, flags=re.IGNORECASE)
    text = re.sub(r"`([^`]+)`", code_replace, text)

    def image_replace(match: re.Match[str]) -> str:
        alt = html.escape(match.group(1), quote=True)
        raw_source = match.group(2).strip()
        if re.match(r"^(?:https?://|data:)", raw_source, flags=re.IGNORECASE):
            source = html.escape(raw_source, quote=True)
        else:
            image_path = Path(raw_source)
            if not image_path.is_absolute():
                if image_base_dir is None:
                    raise ValueError(f"relative Markdown image has no base directory: {raw_source}")
                image_path = image_base_dir / image_path
            source = image_data_uri(image_path.resolve())
        return store(f'<img src="{source}" alt="{alt}">')

    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", image_replace, text)

    def link_replace(match: re.Match[str]) -> str:
        label = html.escape(match.group(1))
        url = html.escape(match.group(2), quote=True)
        return store(f'<a href="{url}">{label}</a>')

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link_replace, text)
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"(?<![\"'=])(https?://[^\s<]+)", r'<a href="\1">\1</a>', escaped)
    for index, token in enumerate(tokens):
        escaped = escaped.replace(f"@@TOKEN{index}@@", token)
    return escaped


def table_cells(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [cell.strip() for cell in value.split("|")]


def starts_block(lines: list[str], index: int) -> bool:
    line = lines[index]
    return bool(
        not line.strip()
        or HEADING_RE.match(line)
        or UL_RE.match(line)
        or OL_RE.match(line)
        or line.strip().startswith("```")
        or line.lstrip().startswith(">")
        or line.strip() in {"---", "***"}
        or (index + 1 < len(lines) and "|" in line and TABLE_SEPARATOR_RE.match(lines[index + 1]))
    )


def markdown_to_html(markdown: str, image_base_dir: Path | None = None) -> str:
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if line.strip().startswith("```"):
            language = line.strip()[3:].strip()
            index += 1
            body: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                body.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            class_attr = f' class="language-{html.escape(language)}"' if language else ""
            output.append(f"<pre><code{class_attr}>{html.escape(chr(10).join(body))}</code></pre>")
            continue
        heading = HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            output.append(f"<h{level}>{inline_html(heading.group(2), image_base_dir)}</h{level}>")
            index += 1
            continue
        if index + 1 < len(lines) and "|" in line and TABLE_SEPARATOR_RE.match(lines[index + 1]):
            headers = table_cells(line)
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(table_cells(lines[index]))
                index += 1
            output.append('<div class="table-wrap"><table><thead><tr>')
            output.extend(f"<th>{inline_html(cell, image_base_dir)}</th>" for cell in headers)
            output.append("</tr></thead><tbody>")
            for row in rows:
                padded = row + [""] * (len(headers) - len(row))
                output.append("<tr>" + "".join(f"<td>{inline_html(cell, image_base_dir)}</td>" for cell in padded[:len(headers)]) + "</tr>")
            output.append("</tbody></table></div>")
            continue
        unordered = UL_RE.match(line)
        if unordered:
            items: list[str] = []
            while index < len(lines):
                match = UL_RE.match(lines[index])
                if not match:
                    break
                items.append(match.group(1))
                index += 1
            output.append("<ul>" + "".join(f"<li>{inline_html(item, image_base_dir)}</li>" for item in items) + "</ul>")
            continue
        ordered = OL_RE.match(line)
        if ordered:
            items = []
            while index < len(lines):
                match = OL_RE.match(lines[index])
                if not match:
                    break
                items.append(match.group(1))
                index += 1
            output.append("<ol>" + "".join(f"<li>{inline_html(item, image_base_dir)}</li>" for item in items) + "</ol>")
            continue
        if line.lstrip().startswith(">"):
            quote: list[str] = []
            while index < len(lines) and lines[index].lstrip().startswith(">"):
                quote.append(lines[index].lstrip()[1:].strip())
                index += 1
            output.append(f"<blockquote><p>{inline_html(' '.join(quote), image_base_dir)}</p></blockquote>")
            continue
        if line.strip() in {"---", "***"}:
            output.append("<hr>")
            index += 1
            continue
        paragraph = [line.strip()]
        index += 1
        while index < len(lines) and not starts_block(lines, index):
            paragraph.append(lines[index].strip())
            index += 1
        output.append(f"<p>{inline_html(' '.join(paragraph), image_base_dir)}</p>")
    return "\n".join(output)


def split_title_and_body(markdown: str) -> tuple[str, str]:
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    title = "Report"
    index = 0
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        index = 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    while index < len(lines) and lines[index].startswith(METADATA_PREFIXES):
        index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    return title, "\n".join(lines[index:])


def image_data_uri(path: str | Path) -> str:
    source = Path(path)
    mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(source.read_bytes()).decode('ascii')}"


def appendix_html(manifest_path: str | None) -> str:
    if not manifest_path:
        return ""
    manifest = json.loads(read_text(manifest_path))
    items = manifest.get("images") if isinstance(manifest, dict) else manifest
    if not isinstance(items, list):
        raise ValueError("appendix manifest must be a list or an object with an images list")
    figures: list[str] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("path"):
            raise ValueError("each appendix item must contain path and may contain caption")
        caption = inline_html(str(item.get("caption") or Path(item["path"]).name))
        figures.append(
            f'<figure class="appendix-page"><figcaption>{caption}</figcaption>'
            f'<img src="{image_data_uri(item["path"])}"></figure>'
        )
    heading = inline_html(str(manifest.get("heading", "附录：截图依据"))) if isinstance(manifest, dict) else "附录：截图依据"
    intro = inline_html(str(manifest.get("intro", ""))) if isinstance(manifest, dict) else ""
    intro_html = f"<p>{intro}</p>" if intro else ""
    return f'<section class="appendix"><h2>{heading}</h2>{intro_html}{"".join(figures)}</section>'


CSS = r"""
:root{color-scheme:light}*{box-sizing:border-box}html{background:#f6f8fa}body{margin:0;color:#1f2328;background:#f6f8fa;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei UI","Noto Sans SC",Arial,sans-serif;font-size:16px;line-height:1.55}.page-shell{max-width:1012px;margin:28px auto;background:#fff;border:1px solid #d0d7de;border-radius:6px;box-shadow:0 1px 3px rgba(31,35,40,.08)}.markdown-body{padding:45px 52px 64px;word-wrap:break-word}.markdown-body h1,.markdown-body h2,.markdown-body h3,.markdown-body h4{font-weight:600;line-height:1.25}.markdown-body h1{font-size:2em;margin:0 0 16px;padding-bottom:.3em;border-bottom:1px solid #d8dee4}.markdown-body h2{font-size:1.5em;margin:34px 0 16px;padding-bottom:.3em;border-bottom:1px solid #d8dee4}.markdown-body h3{font-size:1.25em;margin:26px 0 12px}.markdown-body h4{font-size:1em;margin:22px 0 8px}.markdown-body p{margin:0 0 16px}.markdown-body img{display:block;max-width:100%;max-height:210mm;width:auto;height:auto;margin:8px auto 16px;border:1px solid #d0d7de;border-radius:6px}.markdown-body ul,.markdown-body ol{margin:0 0 16px;padding-left:2em}.markdown-body li+li{margin-top:.25em}.markdown-body code{padding:.2em .4em;margin:0;font-size:85%;white-space:break-spaces;background:rgba(175,184,193,.2);border-radius:6px;font-family:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace}.markdown-body pre{padding:16px;overflow:auto;background:#f6f8fa;border-radius:6px}.markdown-body pre code{padding:0;background:transparent}.markdown-body blockquote{margin:0 0 16px;padding:0 1em;color:#57606a;border-left:.25em solid #d0d7de}.table-wrap{width:100%;overflow-x:auto;margin:0 0 18px}.markdown-body table{border-spacing:0;border-collapse:collapse;width:max-content;min-width:100%;font-size:14px}.markdown-body th,.markdown-body td{padding:7px 10px;border:1px solid #d0d7de;text-align:left;vertical-align:top}.markdown-body th{font-weight:600;background:#f6f8fa}.markdown-body tr:nth-child(2n){background:#f6f8fa}.cover{min-height:920px;padding:150px 72px 80px;text-align:center;background:linear-gradient(180deg,#f6f8fa 0,#fff 42%);border-bottom:1px solid #d0d7de}.cover h1{margin:0;font-size:38px;line-height:1.25;font-weight:650}.cover .subtitle{margin:18px 0 54px;color:#57606a;font-size:18px}.cover dl{display:grid;grid-template-columns:120px 280px;max-width:400px;margin:0 auto;text-align:left;border:1px solid #d0d7de;border-radius:6px;overflow:hidden}.cover dt,.cover dd{margin:0;padding:12px 16px;border-bottom:1px solid #d8dee4}.cover dt{font-weight:600;background:#f6f8fa}.cover dd{background:#fff}.cover dt:last-of-type,.cover dd:last-of-type{border-bottom:0}.appendix{padding-top:8px}.appendix-page{text-align:center;margin:0;padding:18px 0 0}.appendix-page figcaption{font-weight:600;margin-bottom:14px}.appendix-page img{display:block;max-width:420px;max-height:870px;width:auto;height:auto;margin:0 auto;border:1px solid #d0d7de;border-radius:6px}
@page{size:A4;margin:14mm 14mm 16mm}@media print{html,body{background:#fff}.page-shell{max-width:none;margin:0;border:0;border-radius:0;box-shadow:none}.cover{min-height:260mm;padding:56mm 18mm 20mm;break-after:page}.markdown-body{padding:0;font-size:10.4pt;line-height:1.52}.markdown-body h1{font-size:20pt}.markdown-body h2{font-size:17pt;break-after:avoid-page}.markdown-body h3{font-size:13pt;break-after:avoid-page}.markdown-body h4{font-size:11pt;break-after:avoid-page}.markdown-body table{font-size:8.2pt;width:100%;table-layout:auto}.markdown-body th,.markdown-body td{padding:4px 6px}tr{break-inside:avoid-page}.appendix{break-before:page}.appendix-page{break-before:page;height:255mm;display:flex;flex-direction:column;justify-content:flex-start}.appendix-page:first-of-type{break-before:auto;height:auto;break-inside:avoid-page}.appendix-page:first-of-type img{max-height:180mm}.appendix-page img{max-height:225mm;max-width:118mm}.table-wrap{overflow:visible}a{color:inherit;text-decoration:none}}
"""


def find_edge(explicit: str | None) -> Path:
    candidates = [
        explicit,
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError("Microsoft Edge executable was not found; pass --edge")


def render_pdf(edge: Path, html_path: Path, pdf_path: Path) -> None:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if pdf_path.exists():
        raise FileExistsError(f"output PDF already exists: {pdf_path}")
    command = [
        str(edge), "--headless", "--disable-gpu", "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri(),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
    deadline = time.time() + 15.0
    while time.time() < deadline and not pdf_path.exists():
        time.sleep(0.2)
    if not pdf_path.exists():
        raise RuntimeError(f"Edge did not create the PDF (exit {completed.returncode}): {completed.stderr}")


def build_document(args: argparse.Namespace) -> tuple[str, str]:
    markdown_path = Path(args.markdown).resolve()
    source = read_text(markdown_path)
    markdown_title, body_markdown = split_title_and_body(source)
    title = args.title or markdown_title
    article = markdown_to_html(
        body_markdown if not args.no_cover else source,
        markdown_path.parent,
    )
    cover = ""
    if not args.no_cover:
        rows = []
        if args.reporter:
            rows.append(("报告人", args.reporter))
        if args.role:
            rows.append(("身份", args.role))
        if args.report_date:
            rows.append(("报告日期", args.report_date))
        dl = "".join(f"<dt>{inline_html(label)}</dt><dd>{inline_html(value)}</dd>" for label, value in rows)
        subtitle = f'<p class="subtitle">{inline_html(args.subtitle)}</p>' if args.subtitle else ""
        cover = f'<section class="cover"><h1>{inline_html(title)}</h1>{subtitle}<dl>{dl}</dl></section>'
    appendix = appendix_html(args.appendix_manifest)
    document = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>'
        f"<style>{CSS}</style></head><body><main class=\"page-shell\">{cover}"
        f'<article class="markdown-body">{article}{appendix}</article></main></body></html>'
    )
    return title, document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render UTF-8 Markdown as a GitHub Issue style PDF")
    parser.add_argument("--markdown", required=True)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--html-output")
    parser.add_argument("--title")
    parser.add_argument("--subtitle")
    parser.add_argument("--reporter")
    parser.add_argument("--role")
    parser.add_argument("--report-date")
    parser.add_argument("--appendix-manifest")
    parser.add_argument("--edge")
    parser.add_argument("--no-cover", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    title, document = build_document(args)
    pdf_path = Path(args.pdf)
    html_path = Path(args.html_output) if args.html_output else pdf_path.with_suffix(".html")
    write_text(html_path, document)
    render_pdf(find_edge(args.edge), html_path, pdf_path)
    print(json.dumps({
        "title": title,
        "html": str(html_path.resolve()),
        "pdf": str(pdf_path.resolve()),
        "pdfBytes": pdf_path.stat().st_size,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
