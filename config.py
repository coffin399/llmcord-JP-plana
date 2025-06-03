import os
import shutil
import logging
from pathlib import Path
from typing import Any, Dict, Optional
import yaml
import modules.shittim.error.ShittimError as error

# Configure logger
logger = logging.getLogger('shittim.config')

class Config:
    _instance = None
    _config: Dict[str, Any] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._initialized = True
        self.config_dir = Path(__file__).parent
        self.default_config_path = self.config_dir / "shittim.config.default.yaml"
        self.config_path = self.config_dir / "shittim.config.yaml"
        
        self._ensure_config_exists()
        self._load_config()
    
    def _ensure_config_exists(self) -> bool:
        if not self.config_path.exists():
            if not self.default_config_path.exists():
                logger.error("shittim.config.yaml not found. Please download it from the repository.")
                raise error.ShittimConfigDefaultNotFoundError("shittim.config.default.yaml not found")
            
            shutil.copy(self.default_config_path, self.config_path)
            logger.info(f"Created config file at {self.config_path}. Please edit it with your settings and restart the bot.")
            return False
        return True
    
    def _load_config(self) -> None:
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self._config = yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"Error loading config file: {e}")
            self._config = {}
    
    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split('.')
        value = self._config
        
        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default
    
    def get_message(self, key: str, **kwargs) -> str:
        message = self.get(f"messages.{key}", key)
        try:
            return message.format(**kwargs)
        except (KeyError, AttributeError):
            return message
