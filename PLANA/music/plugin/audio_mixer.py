# PLANA/music/audio_mixer.py
import discord
import struct
import asyncio
from typing import Dict, Optional


class AudioMixer(discord.AudioSource):
    """
    複数の discord.AudioSource をリアルタイムでミキシングするクラス。
    音楽を再生しながら、TTSなどの他の音声をオーバーレイ再生するために使用します。
    """

    def __init__(self):
        self.sources: Dict[str, discord.AudioSource] = {}
        self.volumes: Dict[str, float] = {}
        self.lock = asyncio.Lock()
        self._is_done = False
        self.active = True

    def is_done(self) -> bool:
        """ミキサーが終了したかどうかを返します。"""
        return self._is_done

    def stop(self):
        """ミキサーを停止し、クリーンアップを実行します。"""
        self.active = False
        self._is_done = True
        self.cleanup()

    def read(self) -> bytes:
        """discord.pyから20msごとに呼び出され、ミックスされた音声データを返します。"""
        if not self.active or self._is_done:
            return b''

        # 48kHz、16bit、ステレオPCMの20ms分の無音データ
        # 960サンプル * 2チャンネル * 2バイト/サンプル = 3840バイト
        final_frame = bytearray(3840)
        finished_sources = []

        # read()は同期的に呼ばれるため、ソースリストをコピーして処理
        sources_to_process = list(self.sources.items())

        for name, source in sources_to_process:
            try:
                frame = source.read()
                if not frame:
                    finished_sources.append(name)
                    continue

                if len(frame) < 3840:
                    frame += b'\x00' * (3840 - len(frame))

                source_samples = struct.iter_unpack('<h', frame)
                final_samples = struct.iter_unpack('<h', final_frame)

                mixed_frame_data = bytearray()
                volume = self.volumes.get(name, 1.0)

                for source_sample, final_sample in zip(source_samples, final_samples):
                    s_val = source_sample[0]
                    f_val = final_sample[0]

                    mixed_sample = f_val + int(s_val * volume)

                    # 16bit整数の範囲にクリッピング
                    mixed_sample = max(-32768, min(32767, mixed_sample))

                    mixed_frame_data.extend(struct.pack('<h', mixed_sample))

                final_frame = mixed_frame_data

            except Exception:
                finished_sources.append(name)

        if finished_sources:
            try:
                loop = asyncio.get_running_loop()
                for name in finished_sources:
                    asyncio.run_coroutine_threadsafe(self.remove_source(name), loop)
            except RuntimeError:
                # ループが実行されていないスレッドから呼ばれた場合
                for name in finished_sources:
                    # このコンテキストでは非同期ロックが機能しない可能性があるが、ベストエフォートで削除
                    source = self.sources.pop(name, None)
                    self.volumes.pop(name, None)
                    if source and hasattr(source, 'cleanup'):
                        source.cleanup()

        return bytes(final_frame)

    async def add_source(self, name: str, source: discord.AudioSource, volume: float = 1.0):
        """ミキサーに新しい音声ソースを追加します。"""
        async with self.lock:
            if name in self.sources:
                old_source = self.sources.get(name)
                if old_source and hasattr(old_source, 'cleanup'):
                    old_source.cleanup()

            self.sources[name] = source
            self.volumes[name] = max(0.0, volume)

    async def remove_source(self, name: str) -> Optional[discord.AudioSource]:
        """指定されたソースをミキサーから削除します。"""
        async with self.lock:
            source = self.sources.pop(name, None)
            self.volumes.pop(name, None)
            if source and hasattr(source, 'cleanup'):
                source.cleanup()
            return source

    async def set_volume(self, name: str, volume: float):
        """特定のソースの音量を設定します。"""
        async with self.lock:
            if name in self.volumes:
                self.volumes[name] = max(0.0, volume)

    def get_source(self, name: str) -> Optional[discord.AudioSource]:
        return self.sources.get(name)

    def cleanup(self):
        """すべてのソースをクリーンアップします。"""
        for source in self.sources.values():
            if hasattr(source, 'cleanup'):
                source.cleanup()
        self.sources.clear()
        self.volumes.clear()