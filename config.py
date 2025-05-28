import os
import shutil
import logging
from pathlib import Path
from typing import Any, Dict, Optional
import yaml

# Configure logger
logger = logging.getLogger('arona.config')

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
        self.default_config_path = self.config_dir / "config.default.yaml"
        self.config_path = self.config_dir / "config.yaml"
        
        self._ensure_config_exists()
        self._load_config()
    
    def _ensure_config_exists(self) -> None:
        """Ensure config.yaml exists, if not, create it from the default config."""
        if not self.config_path.exists():
            if not self.default_config_path.exists():
                logger.error("config.yaml not found. Please download it from the repository.")
                exit(1)
            
            shutil.copy(self.default_config_path, self.config_path)
            logger.info(f"Created config file at {self.config_path}. Please edit it with your settings and restart the bot.")
            exit(1)
    
    def _load_config(self) -> None:
        """Load the configuration from the YAML file."""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self._config = yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"Error loading config file: {e}")
            self._config = {}
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value by dot notation (e.g., 'discord.token')."""
        keys = key.split('.')
        value = self._config
        
        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default
    
    def get_message(self, key: str, **kwargs) -> str:
        """Get a formatted message from the messages section."""
        message = self.get(f"messages.{key}", key)  # Default to the key itself if not found
        try:
            return message.format(**kwargs)
        except (KeyError, AttributeError):
            return message

config = Config()
get = config.get
get_message = config.get_message
