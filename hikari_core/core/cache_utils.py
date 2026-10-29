import os
from pathlib import Path

from loguru import logger

_project_base_dir:Path = None

def initial_cache_file(dir_path: Path):
    logger.info(f'设置缓存目录为{dir_path}')
    global _project_base_dir
    _project_base_dir = dir_path

def get_cache_file() -> Path:
    if _project_base_dir is None:
        dir_path = Path(os.getcwd()) / "data" / "wows-yuyuko"
        dir_path.mkdir(parents= True,exist_ok=True)
        return dir_path
    else:
        dir_path = _project_base_dir
        dir_path.mkdir(parents= True,exist_ok=True)
        return dir_path

def get_cache_file_str(dir:str = '') -> str:
    if dir == '':
        return str(get_cache_file())
    else:
        dir_path = get_cache_file() / dir
        dir_path.mkdir(parents= True,exist_ok=True)
        return str(dir_path)