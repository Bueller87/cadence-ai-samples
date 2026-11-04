"""Small escaped Markdown reader for allow-listed checkout documentation."""
from __future__ import annotations

import html
import posixpath
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from source import InvalidSelection
from explorer import SKIP


IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
               '.svg': 'image/svg+xml'}


def reader_path(explorer, relative, *, image=False):
    parts = Path(relative).parts
    if (not parts or Path(relative).is_absolute() or '\\' in relative or
            any(p.startswith('.') or p in (SKIP - ({'docs'} if image else set())) for p in parts)):
        raise InvalidSelection('Document is not available through this reader.')
    if image:
        allowed = (len(parts) >= 5 and parts[0] == 'recipes' and
                   parts[2:4] == ('docs', 'images')) or (
                   len(parts) >= 4 and parts[0] == 'contribfest' and 'images' in parts[2:-1])
        allowed = allowed and Path(relative).suffix.lower() in IMAGE_TYPES
    else:
        allowed = relative in {
            'README.md', 'CONTRIBFEST.md', 'CONTRIBUTING.md', 'WORKFLOW.md',
            'AGENTS.md', 'agent-context/README.md',
            'tools/explorer/README.md',
            'recipes/incident-triage/python/bare/exercises/retry_policy_live/README.md',
        } or (
            len(parts) == 3 and parts[0] == 'recipes' and parts[2] == 'README.md') or (
            len(parts) == 3 and parts[:2] == ('contribfest', 'kubecon-na-2026') and
            parts[2].endswith('.md'))
    path = explorer.root / relative
    if not allowed or not explorer.inside(path) or not path.is_file():
        raise InvalidSelection('Document is not available through this reader.')
    return path


def local_url(explorer, document, target, *, image=False):
    try:
        url = urlsplit(html.unescape(target))
        if (url.scheme or url.netloc or url.query or '\\' in target or
                url.path.startswith('/') or any(ord(c) < 32 for c in target)):
            return None
        if not url.path:
            return '#' + quote(unquote(url.fragment), safe='-') if not image else None
        path = posixpath.normpath(posixpath.join(posixpath.dirname(document), unquote(url.path)))
        try:
            reader_path(explorer, path, image=image)
            route = '/image' if image else '/doc'
        except InvalidSelection:
            if image:
                return None
            explorer.source_path(path)
            route = '/source'
        return route + '?path=' + quote(path, safe='') + (
            '#' + quote(unquote(url.fragment), safe='-') if url.fragment else '')
    except (ValueError, OSError, InvalidSelection):
        return None


def render_markdown(explorer, document, source):
    """Render the supported subset, including bounded empty anchor declarations."""
    token = re.compile(r'(`+)(.+?)\1|(!?)\[([^]]*)\]\(([^\s)]+)\)|\*\*([^*]+)\*\*|\*([^*\n]+)\*')

    def inline(text):
        result, end = [], 0
        for match in token.finditer(text):
            result.append(html.escape(text[end:match.start()]))
            ticks, code, bang, label, target, bold, italic = match.groups()
            if ticks:
                result.append('<code>' + html.escape(code) + '</code>')
            elif target is not None:
                label = html.escape(label)
                href = local_url(explorer, document, target, image=bool(bang))
                external = False
                if not bang and href is None:
                    try:
                        url = urlsplit(target)
                        external = (url.scheme in {'https', 'http'} and bool(url.hostname)
                                    and not url.username and not url.password
                                    and not any(ord(c) < 32 for c in target))
                    except ValueError:
                        external = False
                    if external:
                        href = target
                if href is None:
                    shown = label
                    if not bang:
                        try:
                            url = urlsplit(html.unescape(target))
                            relative = (not url.scheme and not url.netloc and bool(url.path)
                                        and not url.path.startswith('/') and '\\' not in target
                                        and not any(ord(c) < 32 for c in target))
                        except ValueError:
                            relative = False
                        if relative:
                            path = posixpath.normpath(posixpath.join(
                                posixpath.dirname(document), unquote(url.path)))
                            shown += ' <code>' + html.escape(path) + '</code>'
                    result.append(shown)
                elif bang:
                    result.append(f'<img src="{html.escape(href, quote=True)}" alt="{label}" loading="lazy">')
                else:
                    attrs = ' target="_blank" rel="noopener noreferrer"' if external else ''
                    result.append(f'<a href="{html.escape(href, quote=True)}"{attrs}>{label}</a>')
            else:
                tag, value = ('strong', bold) if bold is not None else ('em', italic)
                result.append(f'<{tag}>{html.escape(value)}</{tag}>')
            end = match.end()
        result.append(html.escape(text[end:]))
        return ''.join(result)

    lines, result, index, slugs = source.splitlines(), [], 0, {}
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        anchor = re.fullmatch(r'\s*<a id="([A-Za-z][A-Za-z0-9_-]{0,127})"></a>\s*', line)
        if anchor:
            result.append('<a id="' + anchor[1] + '"></a>')
            index += 1
            continue
        fence = re.match(r'^\s*(`{3,}|~{3,})(.*)$', line)
        if fence:
            marker = fence[1]
            code = []
            index += 1
            while index < len(lines) and not re.match(r'^\s*' + re.escape(marker[0]) + '{' + str(len(marker)) + r',}\s*$', lines[index]):
                code.append(lines[index])
                index += 1
            result.append('<pre><code>' + html.escape('\n'.join(code)) + '</code></pre>')
        elif heading := re.match(r'^(#{1,6})\s+(.+?)\s*#*$', line):
            label = heading[2]
            slug = re.sub(r'[^\w\- ]', '', label.lower()).replace(' ', '-')
            count = slugs.get(slug, 0)
            slugs[slug] = count + 1
            anchor = slug + (f'-{count}' if count else '')
            level = len(heading[1])
            result.append(f'<h{level} id="{html.escape(anchor, quote=True)}">{inline(label)}</h{level}>')
        elif index + 1 < len(lines) and '|' in line and re.fullmatch(r'\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*', lines[index + 1]):
            def cells(row, tag):
                return '<tr>' + ''.join(f'<{tag}>{inline(c.strip())}</{tag}>' for c in row.strip().strip('|').split('|')) + '</tr>'
            result.append('<div class="reader-table"><table><thead>' + cells(line, 'th') + '</thead><tbody>')
            index += 2
            while index < len(lines) and '|' in lines[index] and lines[index].strip():
                result.append(cells(lines[index], 'td'))
                index += 1
            result.append('</tbody></table></div>')
            continue
        elif re.match(r'^\s*(?:[-+*]|\d+[.)])\s+', line):
            tag = 'ul' if re.match(r'^\s*[-+*]\s+', line) else 'ol'
            result.append(f'<{tag}>')
            while index < len(lines):
                item = re.match(r'^\s*(?:([-+*])|\d+[.)])\s+(.+)', lines[index])
                if not item or ('ul' if item[1] else 'ol') != tag:
                    break
                text = [item[2]]
                index += 1
                while index < len(lines) and lines[index].strip():
                    nxt = lines[index]
                    if re.match(r'^\s*(?:[-+*]|\d+[.)])\s+', nxt):
                        break
                    if re.match(r'^\s*(?:`{3,}|~{3,})', nxt):
                        break
                    if not nxt[:1].isspace():
                        break
                    text.append(nxt)
                    index += 1
                result.append('<li>' + inline('\n'.join(text)) + '</li>')
            result.append(f'</{tag}>')
            continue
        elif line.startswith('> '):
            result.append('<blockquote>' + inline(line[2:]) + '</blockquote>')
        elif re.fullmatch(r'\s*(?:---+|\*\*\*+|___+)\s*', line):
            result.append('<hr>')
        else:
            paragraph = [line]
            index += 1
            while (index < len(lines) and lines[index].strip() and
                   not re.match(r'^\s*(?:#|>|[-+*] |\d+[.)] |`{3}|~{3})', lines[index]) and
                   not (index + 1 < len(lines) and '|' in lines[index] and '---' in lines[index + 1])):
                paragraph.append(lines[index])
                index += 1
            result.append('<p>' + inline('\n'.join(paragraph)) + '</p>')
            continue
        index += 1
    return '\n'.join(result)


def reader_page(explorer, document):
    path = reader_path(explorer, document)
    body = render_markdown(explorer, document, path.read_text())
    title = html.escape(path.name)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{title} | Samples Explorer</title>'
            '<script src="/theme.js"></script><link rel="stylesheet" href="/style.css">'
            '</head><body><main class="reader"><nav><a href="/">Samples Explorer</a>'
            ' · <a href="/doc?path=contribfest%2Fkubecon-na-2026%2FREADME.md">ContribFest</a>'
            f'</nav><p class="muted">{html.escape(document)}</p><article>{body}</article>'
            '</main></body></html>')
