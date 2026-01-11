#!/usr/bin/env python3
"""
CapCut UI操作システム
CapCutドラフトの全レイヤーをUI操作で完全制御するシステム

主要コンポーネント:
- DraftManager: CapCutドラフト操作の中核
- LayerController: レイヤー制御の基底クラス
- ParameterCalculator: 自動配置・適正値算出
- ImageLayerController: 画像レイヤー制御
- TextLayerController: テキストレイヤー制御
- EffectLayerController: エフェクトレイヤー制御
- AudioLayerController: オーディオレイヤー制御
"""

__version__ = "1.0.0"
__author__ = "srt2images-timeline Development Team"

# コアモジュールのインポート
from .core.draft_manager import DraftManager
from .core.layer_controller import LayerController, Transform2D
from .core.parameter_calculator import ParameterCalculator, PositionResult, ContentInfo, LayoutType

# レイヤーコントローラーのインポート
from .layers.image_layer import ImageLayerController
from .layers.text_layer import TextLayerController, TextStyle
from .layers.effect_layer import EffectLayerController, EffectParameters
from .layers.audio_layer import AudioLayerController, AudioParameters

# ユーティリティのインポート
from .utils.settings_manager import SettingsManager

# 全モジュールのエクスポート
__all__ = [
    # コア
    'DraftManager',
    'LayerController',
    'Transform2D',
    'ParameterCalculator',
    'PositionResult',
    'ContentInfo',
    'LayoutType',

    # レイヤー制御
    'ImageLayerController',
    'TextLayerController',
    'TextStyle',
    'EffectLayerController',
    'EffectParameters',
    'AudioLayerController',
    'AudioParameters',

    # ユーティリティ
    'SettingsManager',
]

# バージョン情報
def get_version_info():
    """バージョン情報を取得"""
    return {
        'version': __version__,
        'author': __author__,
        'components': len(__all__),
        'description': 'CapCut UI操作システム - 全レイヤー完全制御'
    }