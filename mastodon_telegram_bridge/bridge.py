import asyncio
import json
import logging
import os
from html import escape as escape_html
from tempfile import TemporaryDirectory
from typing import Type
from urllib.parse import urlencode, urlsplit, urlunsplit

from mastodon import AttribAccessDict, CallbackStreamListener, Mastodon, MastodonAPIError, MastodonNotFoundError
from mastodon.return_types import Status, try_cast_recurse
from telethon import TelegramClient, events
from telethon.extensions import html as telegram_html
from telethon.tl.custom.message import Message
from websockets.asyncio.client import connect

from .filter import Filter, MastodonFilter, TelegramFilter
from .footer import Footer, MastodonFooter, TelegramFooter
from .storage import Storage
from .typing import (BridgeOptionsDict, MastodonOptionsDict, MastodonToTelegramOptions,
                     StorageOptionsDict, TelegramOptionsDict, TelegramToMastodonOptions)
from .utils import fediverse_to_telegram_html, format_exception, telegram_to_markdown


logger = logging.getLogger(__name__)


class Bridge:
    """The bridge between Mastodon and Telegram."""

    def __init__(self, *, telegram: TelegramOptionsDict, mastodon: MastodonOptionsDict,
                 options: BridgeOptionsDict, mastodon_filter: Type[Filter] = MastodonFilter,
                 telegram_filter: Type[Filter] = TelegramFilter,
                 mastodon_footer: Type[Footer] = MastodonFooter,
                 telegram_footer: Type[Footer] = TelegramFooter,
                 storage: StorageOptionsDict | None = None) -> None:
        self.mastodon = Mastodon(**mastodon)
        self.telegram = TelegramClient(
            telegram.get('session', 'mastodon-telegram-bridge'),
            telegram['api_id'], telegram['api_hash'],
        )
        self.bot_token = telegram['token']
        self._mastodon_username = self.mastodon.me().username
        self._mastodon_app_name = self.mastodon.app_verify_credentials().name
        self.mastodon_to_telegram = MastodonToTelegramOptions(**options['mastodon_to_telegram'])
        self.telegram_to_mastodon = TelegramToMastodonOptions(**options['telegram_to_mastodon'])
        if self.telegram_to_mastodon.visibility not in (None, 'public', 'unlisted', 'private', 'direct'):
            raise ValueError('telegram_to_mastodon.visibility must be public, unlisted, private or direct')
        if self.telegram_to_mastodon.format not in ('markdown', 'plain', 'html'):
            raise ValueError('telegram_to_mastodon.format must be markdown, plain or html')
        if self.telegram_to_mastodon.disable and self.mastodon_to_telegram.disable:
            raise ValueError('Both telegram_to_mastodon and mastodon_to_telegram are disabled, nothing to do.')
        self.mastodon_filter = mastodon_filter(**self.telegram_to_mastodon.filter)
        self.telegram_filter = telegram_filter(**self.mastodon_to_telegram.filter)
        self.mastodon_footer = mastodon_footer(**self.telegram_to_mastodon.footer)
        self.telegram_footer = telegram_footer(**self.mastodon_to_telegram.footer)
        self.storage_path = (storage or {}).get('path', 'bridge.sqlite3')
        self._posting_lock = asyncio.Lock()

    async def _report_error(self, chat_id: int, exc: Exception) -> None:
        logger.exception('Bridge error', exc_info=exc)
        try:
            await self.telegram.send_message(chat_id, format_exception(exc), parse_mode=None)
        except Exception:
            logger.exception('Could not report bridge error to Telegram')

    async def _wait_for_media_ready(self, media_ids: list[int]) -> None:
        wait_time = 1
        for _ in range(5):
            pending = []
            for media_id in media_ids:
                try:
                    await asyncio.to_thread(self.mastodon.media, media_id)
                except MastodonAPIError as exc:
                    if len(exc.args) > 1 and exc.args[1] == 206:
                        pending.append(media_id)
                    else:
                        raise
            if not pending:
                return
            await asyncio.sleep(wait_time)
            wait_time *= 2
        raise TimeoutError('Media is not ready after 5 retries')

    def _format_telegram_message(self, message: Message) -> str:
        post_format = self.telegram_to_mastodon.format
        if post_format == 'markdown':
            text = telegram_to_markdown(message.raw_text or '', message.entities)
        elif post_format == 'html':
            text = telegram_html.unparse(message.raw_text or '', message.entities)
        else:
            text = message.raw_text or ''
        footer = self.mastodon_footer(message)
        if footer:
            text += '\n\n' + (escape_html(footer) if post_format == 'html' else footer)
        return text

    async def _send_media_to_mastodon(self, messages: list[Message]) -> None:
        caption_message = next((message for message in messages if message.raw_text), messages[0])
        if not self.mastodon_filter(caption_message.raw_text or ''):
            return
        text = self._format_telegram_message(caption_message)
        if len(messages) > 4:
            logger.warning('Too many media items: %d', len(messages))
            return
        source_ids = [(caption_message.chat_id, caption_message.id)] + [
            (message.chat_id, message.id) for message in messages if message.id != caption_message.id]
        post_id = self.storage.reserve_telegram(source_ids)
        if post_id is None:
            return
        try:
            await self._post_media_to_mastodon(messages, caption_message, text, post_id)
        except Exception:
            self.storage.release(post_id)
            raise

    async def _post_media_to_mastodon(self, messages: list[Message], caption_message: Message,
                                     text: str, post_id: int) -> None:
        cfg = self.telegram_to_mastodon
        media_ids = []
        with TemporaryDirectory(prefix='mastodon') as tmpdir:
            for message in messages:
                if not (message.photo or message.video):
                    logger.warning('Unsupported media type in album: %s', message.id)
                    continue
                file_path = os.path.join(tmpdir, str(message.id))
                downloaded = await self.telegram.download_media(message, file=file_path)
                if downloaded:
                    media = await asyncio.to_thread(self.mastodon.media_post, downloaded)
                    media_ids.append(media.id)
        if not media_ids:
            self.storage.release(post_id)
            return
        await self._wait_for_media_ready(media_ids)
        async with self._posting_lock:
            status = await asyncio.to_thread(self.mastodon.status_post, text,
                                             content_type=f'text/{cfg.format}', media_ids=media_ids,
                                             visibility=cfg.visibility,
                                             in_reply_to_id=self._reply_status_id(caption_message),
                                             idempotency_key=f'tg:{caption_message.chat_id}:{caption_message.id}')
            self.storage.complete_telegram(post_id, status.id)
        await self._notify_success(caption_message, status.url, cfg.pm_chat_id)

    def _reply_status_id(self, message: Message) -> str | None:
        if not message.reply_to_msg_id:
            return None
        parent = self.storage.telegram_post(message.chat_id, message.reply_to_msg_id)
        return parent.status_id if parent and parent.state == 'sent' else None

    async def _notify_success(self, message: Message, url: str, pm_chat_id: int) -> None:
        text = f'Successfully forwarded to Mastodon.\n{url}'
        if message.is_group and message.fwd_from:
            await message.reply(text, parse_mode=None)
        else:
            await self.telegram.send_message(pm_chat_id, text, parse_mode=None)

    async def _send_message_to_mastodon(self, message: Message) -> None:
        cfg = self.telegram_to_mastodon
        try:
            if message.photo or message.video:
                await self._send_media_to_mastodon([message])
            elif message.raw_text:
                if not self.mastodon_filter(message.raw_text):
                    return
                post_id = self.storage.reserve_telegram([(message.chat_id, message.id)])
                if post_id is None:
                    return
                text = self._format_telegram_message(message)
                try:
                    async with self._posting_lock:
                        status = await asyncio.to_thread(self.mastodon.status_post, text,
                                                          content_type=f'text/{cfg.format}', visibility=cfg.visibility,
                                                          in_reply_to_id=self._reply_status_id(message),
                                                          idempotency_key=f'tg:{message.chat_id}:{message.id}')
                        self.storage.complete_telegram(post_id, status.id)
                except Exception:
                    self.storage.release(post_id)
                    raise
                await self._notify_success(message, status.url, cfg.pm_chat_id)
        except Exception as exc:
            await self._report_error(cfg.pm_chat_id, exc)

    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        if not event.out and not event.message.grouped_id:
            await self._send_message_to_mastodon(event.message)

    async def _on_album(self, event: events.Album.Event) -> None:
        if event.messages[0].out:
            return
        try:
            await self._send_media_to_mastodon(event.messages)
        except Exception as exc:
            await self._report_error(self.telegram_to_mastodon.pm_chat_id, exc)

    async def _on_edited_message(self, event: events.MessageEdited.Event) -> None:
        message = event.message
        post = self.storage.telegram_post(message.chat_id, message.id)
        if not post or post.origin != 'telegram' or post.state != 'sent' or post.primary_message_id != message.id:
            return
        try:
            text = self._format_telegram_message(message)
            media_ids = None
            if message.photo or message.video:
                status = await asyncio.to_thread(self.mastodon.status, post.status_id)
                media_ids = [media.id for media in (status.media_attachments or [])]
            await asyncio.to_thread(self.mastodon._Mastodon__status_internal, text,
                                    edit=post.status_id,
                                    content_type=f'text/{self.telegram_to_mastodon.format}',
                                    media_ids=media_ids)
        except Exception as exc:
            await self._report_error(self.telegram_to_mastodon.pm_chat_id, exc)

    async def _on_deleted_message(self, event: events.MessageDeleted.Event) -> None:
        logger.info('Received Telegram deletion event: chat=%s messages=%s', event.chat_id, event.deleted_ids)
        await self._delete_telegram_messages(event.chat_id, event.deleted_ids)

    async def _delete_telegram_messages(self, chat_id: int | None, message_ids: list[int]) -> None:
        for message_id in message_ids:
            if chat_id is None:
                posts = self.storage.telegram_posts_by_message_id(message_id)
            else:
                post = self.storage.telegram_post(chat_id, message_id)
                posts = [post] if post else []
            for post in posts:
                if post.state != 'sent':
                    continue
                try:
                    async with self._posting_lock:
                        current = self.storage.fediverse_post(post.status_id)
                        if not current or current.state != 'sent':
                            continue
                        try:
                            await asyncio.to_thread(self.mastodon.status_delete, post.status_id)
                        except MastodonNotFoundError:
                            logger.info('Fediverse status %s was already deleted', post.status_id)
                        self.storage.mark_deleted(post.id)
                        logger.info('Synchronized deletion of Telegram message %s and Fediverse status %s',
                                    message_id, post.status_id)
                except Exception as exc:
                    await self._report_error(self.telegram_to_mastodon.pm_chat_id, exc)

    async def _check_channel_deletions(self, missing_last_check: set[int]) -> set[int]:
        chat_id = self.telegram_to_mastodon.channel_chat_id
        message_ids = self.storage.sent_telegram_messages(chat_id)
        missing_now: set[int] = set()
        for start in range(0, len(message_ids), 100):
            batch = message_ids[start:start + 100]
            messages = await self.telegram.get_messages(chat_id, ids=batch)
            missing_now.update(message_id for message_id, message in zip(batch, messages)
                               if message is None)
        for message_id in missing_now & missing_last_check:
            logger.info('Telegram channel message %s is missing on consecutive checks', message_id)
            await self._delete_telegram_messages(chat_id, [message_id])
        return missing_now

    async def _poll_channel_deletions(self) -> None:
        missing_last_check: set[int] = set()
        while True:
            try:
                missing_last_check = await self._check_channel_deletions(missing_last_check)
            except Exception:
                logger.exception('Could not check Telegram channel messages for deletions')
                missing_last_check.clear()
            await asyncio.sleep(60)

    async def _on_deleted_status(self, status_id: str) -> None:
        post = self.storage.fediverse_post(status_id)
        if not post or post.state != 'sent':
            return
        try:
            async with self._posting_lock:
                if self.storage.fediverse_post(status_id).state != 'sent':
                    return
                for chat_id, message_id in self.storage.post_messages(post.id):
                    await self.telegram.delete_messages(chat_id, [message_id])
                self.storage.mark_deleted(post.id)
        except Exception as exc:
            await self._report_error(self.mastodon_to_telegram.pm_chat_id, exc)

    async def _send_message_to_telegram(self, status: AttribAccessDict) -> None:
        cfg = self.mastodon_to_telegram
        try:
            if not (status.account.username == self._mastodon_username and
                    (status.application is None or status.application.name != self._mastodon_app_name) and
                    self.telegram_filter(status)):
                return
            async with self._posting_lock:
                post_id = self.storage.reserve_fediverse(status.id)
            if post_id is None:
                return
            try:
                await self._post_to_telegram(status, post_id)
            except Exception:
                self.storage.release(post_id)
                raise
        except Exception as exc:
            await self._report_error(cfg.pm_chat_id, exc)

    async def _post_to_telegram(self, status: AttribAccessDict, post_id: int) -> None:
        cfg = self.mastodon_to_telegram
        if status.reblog:
            if cfg.forward_reblog_link_only:
                if status.reblog.visibility != 'public':
                    self.storage.release(post_id)
                    return
                sent = await self.telegram.send_message(cfg.channel_chat_id,
                                                        escape_html(self.telegram_footer(status.reblog)),
                                                        parse_mode='html', link_preview=False)
                self.storage.complete_fediverse(post_id, [(sent.chat_id, sent.id)])
                return
            status = status.reblog
        text = fediverse_to_telegram_html(status.content)
        if status.spoiler_text:
            text = f'<strong>{escape_html(status.spoiler_text)}</strong>\n\n{text}'
        footer = self.telegram_footer(status)
        if footer:
            text += '\n' + escape_html(footer)
        media_urls = [item.url for item in status.media_attachments
                      if item.type in ('image', 'video')]
        if media_urls:
            sent = await self.telegram.send_file(cfg.channel_chat_id, media_urls,
                                                 caption=text, parse_mode='html')
        else:
            sent = await self.telegram.send_message(cfg.channel_chat_id, text,
                                                    parse_mode='html', link_preview=False)
        sent_messages = sent if isinstance(sent, list) else [sent]
        self.storage.complete_fediverse(post_id, [(message.chat_id, message.id) for message in sent_messages])

    async def _stream_pleroma_user(self, queue: asyncio.Queue[tuple[str, object]], streaming_url: str) -> None:
        parsed = urlsplit(streaming_url)
        path = parsed.path.rstrip('/') or '/api/v1/streaming'
        query = urlencode({'stream': 'user', 'access_token': self.mastodon.access_token})
        url = urlunsplit((parsed.scheme, parsed.netloc, path, query, ''))
        while True:
            try:
                async with connect(url) as websocket:
                    logger.info('Connected to Pleroma/Akkoma user stream')
                    async for message in websocket:
                        event = json.loads(message)
                        if event.get('event') == 'update':
                            payload = event['payload']
                            status = json.loads(payload) if isinstance(payload, str) else payload
                            await queue.put(('update', try_cast_recurse(Status, status)))
                        elif event.get('event') == 'delete':
                            await queue.put(('delete', event['payload']))
            except Exception as exc:
                logger.warning('Pleroma/Akkoma user stream disconnected: %s',
                               str(exc).replace(self.mastodon.access_token, '[redacted]'))
            await asyncio.sleep(5)

    async def _run(self) -> None:
        self.storage = Storage(self.storage_path, self.mastodon.api_base_url)
        queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
        stream = None
        stream_task = None
        consumer = None
        deletion_poll_task = None
        try:
            await self.telegram.start(bot_token=self.bot_token)
            if not self.telegram_to_mastodon.disable:
                self.telegram.add_event_handler(
                    self._on_new_message,
                    events.NewMessage(chats=self.telegram_to_mastodon.channel_chat_id),
                )
                self.telegram.add_event_handler(
                    self._on_album,
                    events.Album(chats=self.telegram_to_mastodon.channel_chat_id),
                )
                self.telegram.add_event_handler(
                    self._on_edited_message,
                    events.MessageEdited(chats=self.telegram_to_mastodon.channel_chat_id),
                )
            self.telegram.add_event_handler(self._on_deleted_message, events.MessageDeleted())
            if not self.telegram_to_mastodon.disable:
                deletion_poll_task = asyncio.create_task(self._poll_channel_deletions())
            instance = await asyncio.to_thread(self.mastodon.instance)
            if any(name in instance.version.lower() for name in ('pleroma', 'akkoma')):
                stream_task = asyncio.create_task(
                    self._stream_pleroma_user(queue, instance.urls.streaming_api),
                )
            else:
                loop = asyncio.get_running_loop()
                listener = CallbackStreamListener(
                    update_handler=lambda status: loop.call_soon_threadsafe(queue.put_nowait, ('update', status)),
                    delete_handler=lambda status_id: loop.call_soon_threadsafe(queue.put_nowait, ('delete', status_id)),
                )
                stream = await asyncio.to_thread(self.mastodon.stream_user, listener=listener,
                                                  run_async=True, reconnect_async=True)

            async def consume() -> None:
                while True:
                    event_type, payload = await queue.get()
                    if event_type == 'delete':
                        await self._on_deleted_status(payload)
                    elif not self.mastodon_to_telegram.disable:
                        await self._send_message_to_telegram(payload)

            consumer = asyncio.create_task(consume())
            await self.telegram.run_until_disconnected()
        finally:
            if deletion_poll_task:
                deletion_poll_task.cancel()
                await asyncio.gather(deletion_poll_task, return_exceptions=True)
            if stream_task:
                stream_task.cancel()
                await asyncio.gather(stream_task, return_exceptions=True)
            if consumer:
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
            if stream:
                await asyncio.to_thread(stream.close)
            await self.telegram.disconnect()

    def run(self, dry_run: bool = False) -> None:
        if dry_run:
            logger.info('Configuration valid; dry run complete.')
            return
        try:
            asyncio.run(self._run())
        except KeyboardInterrupt:
            logger.info('Bridge stopped.')
