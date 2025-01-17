"""Class definitions for configuring language model training"""
from __future__ import annotations

import os

import yaml

from .base_config import BaseConfig

__all__ = ["TrainLMConfig", "train_lm_yaml_to_config", "load_basic_train_lm"]


class TrainLMConfig(BaseConfig):
    """
    Class for storing configuration information for training language models

    Attributes
    ----------
    order: int
    method: str
    prune: bool
    count_threshold: int
    prune_thresh_small: float
    prune_thresh_medium: float
    use_mp: bool
    """

    def __init__(self):
        self.order = 3
        self.method = "kneser_ney"
        self.prune = False
        self.count_threshold = 1
        self.prune_thresh_small = 0.0000003
        self.prune_thresh_medium = 0.0000001
        self.use_mp = True


def train_lm_yaml_to_config(path: str) -> TrainLMConfig:
    """
    Helper function to load language model training configurations

    Parameters
    ----------
    path: str
        Path to yaml file

    Returns
    -------
    :class:`~montreal_forced_aligner.config.train_lm_config.TrainLMConfig`
        Language model training configuration
    """
    with open(path, "r", encoding="utf8") as f:
        data = yaml.load(f, Loader=yaml.SafeLoader)
        config = TrainLMConfig()
        config.update(data)
    return config


def load_basic_train_lm() -> TrainLMConfig:
    """
    Helper function to load the default parameters

    Returns
    -------
    :class:`~montreal_forced_aligner.config.train_lm_config.TrainLMConfig`
        Default language model training configuration
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    training_config = train_lm_yaml_to_config(os.path.join(base_dir, "basic_train_lm.yaml"))
    return training_config
