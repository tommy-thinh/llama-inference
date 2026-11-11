"""Bounded stateless chat input."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def nonblank(cls, content: str) -> str:
        if not content.strip():
            raise ValueError("Message content must not be blank")
        return content


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    messages: list[Message] = Field(min_length=1, max_length=16)
    max_tokens: int = Field(default=1024, ge=1, le=2048)
    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float = Field(default=0.9, gt=0, le=1)
    seed: int = Field(default=42, ge=0, le=2147483647)

    @model_validator(mode="after")
    def conversation_bounds(self) -> Self:
        if sum(len(message.content) for message in self.messages) > 8000:
            raise ValueError("Total message content exceeds 8000 characters")
        turns = self.messages[1:] if self.messages[0].role == "system" else self.messages
        if not turns or turns[-1].role != "user":
            raise ValueError("Conversation must end with a user message")
        for index, message in enumerate(turns):
            if message.role != ("user" if index % 2 == 0 else "assistant"):
                raise ValueError("Conversation must alternate user and assistant turns")
        return self

    def upstream_payload(self) -> dict:
        return {
            **self.model_dump(),
            "model": "llama-3.2-1b-instruct",
            "stream": True,
            "stream_options": {"include_usage": True},
            "cache_prompt": False,
        }


class ConversationTitle(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=80)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Title must not be blank")
        return value.strip()


class NewConversation(ConversationTitle):
    title: str = "New conversation"


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    content: str = Field(min_length=1, max_length=4000)
    max_tokens: int = Field(default=1024, ge=1, le=2048)
    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float = Field(default=0.9, gt=0, le=1)
    seed: int = Field(default=42, ge=0, le=2147483647)

    @field_validator("content")
    @classmethod
    def nonblank(cls, value: str) -> str:
        return Message(role="user", content=value).content
