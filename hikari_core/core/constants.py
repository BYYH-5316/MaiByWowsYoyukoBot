"""静态数据常量与包元信息。

原 ``hikari_core/data_source.py`` 按功能拆分：
- 数据常量 → ``core/constants.py``（本文件）
- 渲染辅助 → ``core/render_helpers.py``
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

dir_path = Path(__file__).resolve().parent.parent  # hikari_core/
template_path = dir_path / 'Template'


@dataclass
class matching:
    keywords: Tuple[str, ...]
    match_keywords: str


nations = [
    matching(('commonwealth', '英联邦'), 'Commonwealth'),
    matching(('europe', '欧洲'), 'Europe'),
    matching(('france', '法国'), 'France'),
    matching(('germany', '德国'), 'Germany'),
    matching(('italy', '意大利'), 'Italy'),
    matching(('japan', '日本'), 'Japan'),
    matching(('pan_america', '泛美'), 'Pan_America'),
    matching(('pan_asia', '泛亚'), 'Pan_Asia'),
    matching(('uk', '英国', 'United_Kingdom'), 'United_Kingdom'),
    matching(('usa', '美国'), 'USA'),
    matching(('ussr', '苏联'), 'Russia'),
    matching(('netherlands', '荷兰'), 'Netherlands'),
    matching(('spain', '西班牙'), 'Spain'),
]

shiptypes = [
    matching(('Cruiser', '巡洋舰', '巡洋', 'CA'), 'Cruiser'),
    matching(('Battleship', '战列舰', '战列', 'BB'), 'Battleship'),
    matching(('Destroyer', '驱逐舰', '驱逐', 'DD'), 'Destroyer'),
    matching(('Submarine', '潜艇', 'SS'), 'Submarine'),
    matching(('Auxiliary', '辅助航母', 'CVE'), 'Auxiliary'),
    matching(('AirCarrier', '航空母舰', '航母', 'CV'), 'AirCarrier'),
]

levels = [  # 原列表存在重复的 '4'，已去重
    matching(('1', '1级', '一级', '一'), '1'),
    matching(('2', '2级', '二级', '二'), '2'),
    matching(('3', '3级', '三级', '三'), '3'),
    matching(('4', '4级', '四级', '四'), '4'),
    matching(('5', '5级', '五级', '五'), '5'),
    matching(('6', '6级', '六级', '六'), '6'),
    matching(('7', '7级', '七级', '七'), '7'),
    matching(('8', '8级', '八级', '八'), '8'),
    matching(('9', '9级', '九级', '九'), '9'),
    matching(('10', '10级', '十级', '十'), '10'),
    matching(('11', '11级', '十一级', '十一'), '11'),
]

servers = [
    matching(('asia', '亚服', 'asian'), 'asia'),
    matching(('eu', '欧服', 'europe'), 'eu'),
    matching(('na', '美服', 'NorthAmerican'), 'na'),
    matching(('ru', '俄服', 'Russia'), 'ru'),
    matching(('cn', '国服', 'china'), 'cn'),
]
