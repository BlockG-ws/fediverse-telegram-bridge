import re
from typing import Sequence
from urllib.parse import quote

from betterlogging.outer.better_exceptions import ExceptionFormatter
from bs4 import BeautifulSoup
from markdownify import MarkdownConverter
from telethon.helpers import add_surrogate, del_surrogate
from telethon.tl.types import (MessageEntityBlockquote, MessageEntityBold, MessageEntityCode,
                               MessageEntityEmail, MessageEntityItalic, MessageEntityPre,
                               MessageEntityStrike, MessageEntityTextUrl, MessageEntityUrl,
                               TypeMessageEntity)


class TelegramMarkdownConverter(MarkdownConverter):
    """Telegram Markdown Converter

    Convert markdown to telegram markdown style
    """
    class Options(MarkdownConverter.Options):
        """Markdown Converter Options

        Options for markdown converter
        Reference: https://core.telegram.org/bots/api#markdown-style
        """
        autolinks = False
        escape_brackets = True
        escape_backquote = True

    def escape(self, text: str, parent_tags: set[str]) -> str:
        """Escape some characters

        Args:
            text (str): text to escape

        Returns:
            str: escaped text
        """
        if not text:
            return ''
        if self.options['escape_asterisks']:
            text = text.replace('*', r'\*')
        if self.options['escape_underscores']:
            text = text.replace('_', r'\_')
        if self.options['escape_brackets']:
            text = text.replace('[', r'\[')
        if self.options['escape_backquote']:
            text = text.replace('`', r'\`')
        return text


def markdownify(text: str, **options) -> str:
    """Markdownify text to telegram markdown style

    Args:
        text (str): text to convert

    Returns:
        str: converted text
    """
    return TelegramMarkdownConverter(**options).convert(text)


def fediverse_to_telegram_html(content: str) -> str:
    """Preserve Fediverse HTML formatting and block spacing for Telethon."""
    soup = BeautifulSoup(content, 'html.parser')
    for image in soup.find_all('img'):
        image.replace_with(image.get('alt', ''))
    for line_break in soup.find_all('br'):
        line_break.replace_with('\n')
    for block in soup.find_all(['p', 'div', 'blockquote', 'pre']):
        if any(str(sibling).strip() for sibling in block.next_siblings):
            block.insert_after('\n\n')
    for item in soup.find_all('li'):
        item.insert_after('\n')
    return str(soup).rstrip('\n')


def telegram_to_markdown(text: str, entities: Sequence[TypeMessageEntity] | None) -> str:
    """Convert Telegram's UTF-16 text entities to Fediverse Markdown."""
    text = add_surrogate(text)
    entities = sorted(entities or [], key=lambda entity: (entity.offset, -entity.length))

    def render(start: int, end: int, remaining: list[TypeMessageEntity]) -> str:
        parts = []
        position = start
        while remaining:
            entity = remaining.pop(0)
            entity_end = entity.offset + entity.length
            if entity.offset < position or entity_end > end or entity.length == 0:
                continue
            parts.append(escape(text[position:entity.offset]))
            children = [child for child in remaining
                        if child.offset >= entity.offset and child.offset + child.length <= entity_end]
            remaining[:] = [child for child in remaining if child not in children]
            content = (text[entity.offset:entity_end]
                       if isinstance(entity, (MessageEntityCode, MessageEntityPre))
                       else render(entity.offset, entity_end, children))
            parts.append(wrap(entity, content))
            position = entity_end
        parts.append(escape(text[position:end]))
        return ''.join(parts)

    def escape(value: str) -> str:
        value = re.sub(r'([\\`*_\[\]~|])', r'\\\1', value)
        value = re.sub(r'(?m)^([ \t]*)([#>+\-])', r'\1\\\2', value)
        return re.sub(r'(?m)^([ \t]*\d+)([.)])(?=\s)', r'\1\\\2', value)

    def wrap(entity, content: str) -> str:
        if isinstance(entity, MessageEntityBold):
            return f'**{content}**'
        if isinstance(entity, MessageEntityItalic):
            return f'*{content}*'
        if isinstance(entity, MessageEntityStrike):
            return f'~~{content}~~'
        if isinstance(entity, MessageEntityCode):
            delimiter = '`' * (max((len(match.group()) for match in re.finditer(r'`+', content)), default=0) + 1)
            padding = ' ' if content.startswith('`') or content.endswith('`') else ''
            return f'{delimiter}{padding}{content}{padding}{delimiter}'
        if isinstance(entity, MessageEntityPre):
            delimiter = '`' * max(3, max((len(match.group()) for match in re.finditer(r'`+', content)), default=0) + 1)
            language = re.sub(r'[^\w+-]', '', entity.language or '')
            return f'\n{delimiter}{language}\n{content}\n{delimiter}\n'
        if isinstance(entity, (MessageEntityTextUrl, MessageEntityUrl, MessageEntityEmail)):
            if isinstance(entity, MessageEntityTextUrl):
                url = entity.url
            else:
                url = del_surrogate(text[entity.offset:entity.offset + entity.length])
                if isinstance(entity, MessageEntityEmail):
                    url = f'mailto:{url}'
            url = quote(url, safe=":/?#[]@!$&'*+,;=%")
            return f'[{content}]({url})'
        if isinstance(entity, MessageEntityBlockquote):
            return '\n' + '\n'.join(f'> {line}' for line in content.split('\n')) + '\n'
        return content

    return del_surrogate(render(0, len(text), entities))


def format_exception(exc: Exception) -> str:
    """Format exception to string

    Args:
        exc (Exception): exception to format

    Returns:
        str: formatted exception
    """
    return ''.join(ExceptionFormatter().format_exception(type(exc), exc, exc.__traceback__))
