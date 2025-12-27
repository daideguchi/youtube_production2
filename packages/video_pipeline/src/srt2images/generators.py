from abc import ABC, abstractmethod
from typing import List, Dict, Any

class ImageGenerator(ABC):
    @abstractmethod
    def generate_batch(self, cues: List[Dict[str, Any]], **kwargs) -> None:
        """画像をバッチ生成する。QuotaExhaustedErrorは上位に伝播させる。"""
        pass

class NanobananaGenerator(ImageGenerator):
    def __init__(self, mode, bin_path, timeout_sec, config_path, retry_until_success, max_retries, placeholder_text):
        self.mode = mode
        self.bin_path = bin_path
        self.timeout_sec = timeout_sec
        self.config_path = config_path
        self.retry_until_success = retry_until_success
        self.max_retries = max_retries
        self.placeholder_text = placeholder_text

    def generate_batch(self, cues: List[Dict[str, Any]], **kwargs) -> None:
        """
        画像をバッチ生成する。
        
        Raises:
            QuotaExhaustedError: 429エラーが連続した場合
        """
        from .nanobanana_client import generate_image_batch as nanobanana_generate_batch
        # Note: QuotaExhaustedErrorはgenerate_image_batch内から投げられ、
        # ここではキャッチせずに上位（pipeline.py）に伝播させる
        
        nanobanana_generate_batch(
            cues=cues,
            mode=self.mode,
            concurrency=kwargs.get('concurrency', 3),
            force=kwargs.get('force', False),
            width=kwargs.get('width'),
            height=kwargs.get('height'),
            bin_path=self.bin_path,
            timeout_sec=self.timeout_sec,
            config_path=self.config_path,
            retry_until_success=self.retry_until_success,
            max_retries=self.max_retries,
            placeholder_text=self.placeholder_text,
        )

def get_image_generator(args) -> ImageGenerator:
    if args.nanobanana != 'none':
        return NanobananaGenerator(
            mode=args.nanobanana,
            bin_path=args.nanobanana_bin,
            timeout_sec=args.nanobanana_timeout,
            config_path=args.nanobanana_config,
            retry_until_success=args.retry_until_success,
            max_retries=args.max_retries,
            placeholder_text=args.placeholder_text
        )
    return None
