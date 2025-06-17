import yaml
import os
import pydantic


EMBEDDING_MODEL = "textembedding-gecko-001"


class Provider(pydantic.BaseModel):
    name: str
    base_url: str
    api_key: str


class Idiolect(pydantic.BaseModel):
    name: str
    prompts: list[str] = []


class Character(pydantic.BaseModel):
    name: str
    chat_model: str
    vision_model: str
    chat_model_provider: Provider
    vision_model_provider: Provider
    personality: str
    idiolect: list[Idiolect]


class Config(pydantic.BaseModel):
    name: str
    protocol: str
    onebot_server: str
    character: Character
    enable: bool
    enabled_groups: list[str]
    enabled_tools: list[str] = pydantic.Field(default_factory=list)


AMADEUS_CONFIG_ENV = "AMADEUS_CONFIG"
AMADEUS_CONFIG_STR = os.environ.get(AMADEUS_CONFIG_ENV) or "{}"

data = yaml.safe_load(AMADEUS_CONFIG_STR)
AMADEUS_CONFIG = Config.model_validate(yaml.safe_load(AMADEUS_CONFIG_STR))
